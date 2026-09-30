"""Activity Manager RAG pipeline: REFRESH (corpus + index), RETRIEVE (top-k), ANSWER (grounded).

Runs locally on the host (not containerised), like mcp-server/. Follows Lab 08's
shape (ai-services/rag-server/rag_pipeline.py is the shared-feature version)
adapted to activity-service, with these deliberate differences:

- Ranking is by similarity first; authority tier only breaks ties. The lab sorts
  by tier first, which lets a weak tier-1 match outrank a strong tier-2 match.
- The hash embedding spreads tokens over all EMBED_VECTOR_SIZE dimensions
  (signed feature hashing). The lab's version adds each SHA-256 digest's 32
  bytes to indices 0-31 only, so 224 of its 256 dimensions are always zero.
- The collection uses cosine distance, with relevance thresholds calibrated
  per embedding space (DISTANCE_THRESHOLDS).

Every public tool returns {"status": "success", ...} or
{"status": "error", "error": ..., "error_type": ...} and never raises.
"""
import hashlib
import json
import logging
import os
import re
import sqlite3
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

try:
    import chromadb
except ImportError:  # retrieval degrades to lexical fallback; refresh reports it
    chromadb = None

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent
SERVICE_ROOT = BASE_DIR.parent  # rag-server -> activity-service
CORPUS_PATH = BASE_DIR / "corpus" / "corpus.jsonl"
AUDIT_PATH = BASE_DIR / "rag-audit.jsonl"
CHROMA_PATH = BASE_DIR / "chroma"

# tier_1 sources: the SQLite file first (when it holds activities), then the
# database-service HTTP API the MCP server already uses (same env var name).
ACTIVITY_DB_PATH = Path(os.environ.get(
    "RAG_ACTIVITY_DB_PATH", SERVICE_ROOT / "database-service" / "activity-db.sqlite"
))
ACTIVITY_DB_URL = os.environ.get("ACTIVITY_DB_URL", "http://localhost:6003")
# tier_2 source. Reports this pipeline writes (rag-*.md) are excluded so its own
# metrics tables never become evidence for the answers they measure.
DOCS_DIR = SERVICE_ROOT / "docs"
GENERATED_REPORT_PREFIX = "rag-"
IGNORED_DIRS = {".git", ".venv", "venv", "__pycache__", ".pytest_cache", "node_modules", "chroma"}

OLLAMA_GENERATE_URL = os.environ.get("OLLAMA_GENERATE_URL", "http://localhost:11434/api/generate")
OLLAMA_EMBED_URL = os.environ.get("OLLAMA_EMBED_URL", "http://localhost:11434/api/embeddings")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "qwen2.5:0.5b")
EMBED_MODEL = os.environ.get("EMBED_MODEL", "nomic-embed-text")
# "ollama" by default (falls back to "hash" when Ollama is down); see
# docs/reports/rag-improvement-report.md for the before/after evidence.
EMBEDDING_MODE = os.environ.get("EMBEDDING_MODE", "ollama").strip().lower()
OLLAMA_TIMEOUT_SECONDS = float(os.environ.get("OLLAMA_TIMEOUT_SECONDS", "60"))
DB_TIMEOUT_SECONDS = float(os.environ.get("ACTIVITY_DB_TIMEOUT_SECONDS", "5"))

COLLECTION_NAME = "activity_enterprise_context"
EMBED_VECTOR_SIZE = 256
DOC_CHUNK_WORDS = 80
MAX_K = 20
MAX_QUERY_CHARS = 1000

# (strong, relevant) cosine-distance cut-offs per score space, 0 = identical.
# Calibrated on the live corpus: nomic distances are compressed (true matches
# 0.18-0.33, off-topic queries 0.48-0.55) while hash distances spread wide.
# See tool-contracts.md "Confidence rule".
DISTANCE_THRESHOLDS = {"ollama": (0.25, 0.4), "hash": (0.35, 0.6), "lexical": (0.35, 0.6)}

TIER_ORDER = {"tier_1": 0, "tier_2": 1, "tier_3": 2}
INSUFFICIENT = "Insufficient evidence"
TOKEN_RE = re.compile(r"[a-z0-9]+")


class InvalidInput(ValueError):
    """A caller-supplied argument failed validation (HTTP 400, not 500)."""


# ---------------------------------------------------------------- helpers

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def tokenize(text: str) -> list[str]:
    return TOKEN_RE.findall((text or "").lower())


def error_result(message: str, error_type: str, **extra: Any) -> dict[str, Any]:
    return {"status": "error", "error": message, "error_type": error_type, **extra}


def make_chunk(chunk_id: str, source_id: str, tier: str, text: str, metadata: dict[str, Any]) -> dict[str, Any]:
    return {
        "chunk_id": chunk_id,
        "source_id": source_id,
        "authority_tier": tier,
        "text": text,
        "metadata": metadata,
        "indexed_at": now_iso(),
    }


def append_audit(tool_name: str, tool_input: dict[str, Any], tool_output: dict[str, Any],
                 trace_id: str, start: float) -> None:
    """Append-only JSONL audit. A failed write is logged, never raised, so
    auditing can't turn a successful tool call into an error."""
    ok = tool_output.get("status") == "success"
    record = {
        "request_id": str(uuid.uuid4()),
        "trace_id": trace_id,
        "tool_name": tool_name,
        "tool_input": tool_input,
        "tool_output": tool_output,
        "timestamp": now_iso(),
        "duration_ms": int((time.time() - start) * 1000),
        "validation_status": "fail" if tool_output.get("error_type") == "invalid_input" else "pass",
        "outcome": "success" if ok else tool_output.get("error_type", "error"),
    }
    try:
        AUDIT_PATH.parent.mkdir(parents=True, exist_ok=True)
        with AUDIT_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")
    except OSError as exc:
        logger.error("audit write failed: %s", exc)


# ---------------------------------------------------------------- embeddings

def hash_embed(text: str) -> list[float]:
    """Signed feature hashing of the token bag into EMBED_VECTOR_SIZE dims, L2-normalised."""
    values = [0.0] * EMBED_VECTOR_SIZE
    for token in tokenize(text):
        digest = hashlib.sha256(token.encode("utf-8")).digest()
        index = int.from_bytes(digest[:4], "big") % EMBED_VECTOR_SIZE
        values[index] += 1.0 if digest[4] % 2 == 0 else -1.0
    norm = sum(v * v for v in values) ** 0.5
    return [v / norm for v in values] if norm else values


def ollama_embed(text: str) -> list[float]:
    response = requests.post(
        OLLAMA_EMBED_URL, json={"model": EMBED_MODEL, "prompt": text}, timeout=OLLAMA_TIMEOUT_SECONDS
    )
    response.raise_for_status()
    embedding = response.json().get("embedding")
    if not embedding:
        raise ValueError(f"no embedding returned by {EMBED_MODEL}")
    return embedding


def embed_texts(texts: list[str], mode: str | None = None) -> tuple[list[list[float]], str, str | None]:
    """Returns (vectors, mode_used, fallback_error). "ollama" mode falls back to
    "hash" for the whole batch on any failure, so one batch never mixes spaces."""
    mode = mode or EMBEDDING_MODE
    if mode == "ollama":
        try:
            return [ollama_embed(text) for text in texts], "ollama", None
        except (requests.exceptions.RequestException, ValueError) as exc:
            return [hash_embed(text) for text in texts], "hash", f"ollama embeddings unavailable: {exc}"
    return [hash_embed(text) for text in texts], "hash", None


# ---------------------------------------------------------------- tier_1: activities DB

def read_sqlite(path: Path) -> tuple[list[dict], list[dict]]:
    # mode=ro so a missing file raises instead of being created empty
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        activities = [dict(r) for r in conn.execute("SELECT * FROM activities ORDER BY activity_id")]
        assignments = [dict(r) for r in conn.execute("SELECT * FROM activities_assignment ORDER BY assignment_id")]
    finally:
        conn.close()
    return activities, assignments


def read_database_service() -> tuple[list[dict], list[dict]]:
    def get(path: str) -> list[dict]:
        response = requests.get(f"{ACTIVITY_DB_URL}{path}", timeout=DB_TIMEOUT_SECONDS)
        response.raise_for_status()
        return response.json()
    return get("/activities"), get("/assignments")


def load_activity_data() -> tuple[list[dict], list[dict], str]:
    """Returns (activities, assignments, source_id). An empty SQLite file (the
    local dev file, while Docker's volume holds the real data) falls through
    to the HTTP API rather than indexing "0 activities" as tier-1 truth."""
    sqlite_result = None
    try:
        sqlite_result = read_sqlite(ACTIVITY_DB_PATH)
        if sqlite_result[0]:
            return *sqlite_result, f"sqlite:{ACTIVITY_DB_PATH.name}"
    except sqlite3.Error as exc:
        logger.info("SQLite source unavailable (%s); using database-service API", exc)
    try:
        return *read_database_service(), f"database-service:{ACTIVITY_DB_URL}"
    except (requests.exceptions.RequestException, ValueError) as exc:
        if sqlite_result is not None:
            return *sqlite_result, f"sqlite:{ACTIVITY_DB_PATH.name}"
        raise RuntimeError(f"no activity data source reachable: {exc}") from exc


def format_price(value: Any) -> str:
    return f"{float(value):.2f}" if isinstance(value, (int, float)) else "unknown"


def activity_chunks(activities: list[dict], assignments: list[dict], source_id: str) -> list[dict[str, Any]]:
    names = {a["activity_id"]: a.get("activity_name") for a in activities}
    chunks = [
        make_chunk(
            f"activity_{a['activity_id']}", source_id, "tier_1",
            f"Activity record: activity_id={a['activity_id']}, name={a.get('activity_name')}, "
            f"category={a.get('activity_type')}, price={format_price(a.get('activity_cost'))}, "
            f"duration={a.get('duration')}.",
            {"source_type": "activity_record", "activity_id": a["activity_id"],
             "name": a.get("activity_name"), "category": a.get("activity_type")},
        )
        for a in activities
    ]
    chunks += [
        make_chunk(
            f"assignment_{s['assignment_id']}", source_id, "tier_1",
            f"Assignment record: assignment_id={s['assignment_id']}, activity_id={s['activity_id']}, "
            f"activity_name={names.get(s['activity_id'])}, scheduled_time={s.get('assignment_time')}.",
            {"source_type": "assignment_record", "activity_id": s["activity_id"]},
        )
        for s in assignments
    ]
    return chunks + summary_chunks(activities, source_id)


def summary_chunks(activities: list[dict], source_id: str) -> list[dict[str, Any]]:
    by_category: dict[str, list[dict]] = {}
    for a in activities:
        by_category.setdefault(a.get("activity_type") or "Uncategorised", []).append(a)

    counts = ", ".join(f"{cat}={len(items)}" for cat, items in sorted(by_category.items()))
    chunks = [
        make_chunk("activity_count", source_id, "tier_1",
                   f"Activity summary: there are {len(activities)} activities in total in the activities database.",
                   {"source_type": "summary", "metric": "total_count", "value": len(activities)}),
        make_chunk("activity_category_counts", source_id, "tier_1",
                   f"Activity summary by category: {counts}.",
                   {"source_type": "summary", "metric": "count_per_category"}),
    ]
    for category, items in sorted(by_category.items()):
        listed = ", ".join(f"{a.get('activity_name')} (activity_id={a['activity_id']})" for a in items)
        chunks.append(make_chunk(
            f"activity_category_{slug(category)}", source_id, "tier_1",
            f"Activity category summary: category={category} has {len(items)} activities: {listed}.",
            {"source_type": "summary", "metric": "category", "category": category, "value": len(items)},
        ))
    return chunks


def slug(value: str) -> str:
    return "_".join(tokenize(value)) or "unknown"


# ---------------------------------------------------------------- tier_2 / tier_3

def chunk_words(text: str, max_words: int) -> list[str]:
    words = text.split()
    return [" ".join(words[i:i + max_words]) for i in range(0, len(words), max_words)]


def doc_paths() -> list[Path]:
    if not DOCS_DIR.is_dir():
        return []
    return sorted(
        p for p in DOCS_DIR.rglob("*")
        if p.suffix in (".md", ".json") and p.is_file() and not p.is_symlink()
        and not p.name.startswith(GENERATED_REPORT_PREFIX)
    )


def doc_chunks() -> list[dict[str, Any]]:
    chunks = []
    for path in doc_paths():
        rel = path.relative_to(SERVICE_ROOT).as_posix()
        text = path.read_text(encoding="utf-8", errors="ignore")
        for i, piece in enumerate(chunk_words(text, DOC_CHUNK_WORDS), start=1):
            chunks.append(make_chunk(f"{rel}#{i}", rel, "tier_2", piece,
                                     {"source_type": "doc", "file": rel, "part": i}))
    return chunks


def repository_chunk() -> dict[str, Any]:
    files = []
    for root, dirs, filenames in os.walk(SERVICE_ROOT, followlinks=False):
        dirs[:] = sorted(d for d in dirs if d not in IGNORED_DIRS)
        files += [(Path(root) / f).relative_to(SERVICE_ROOT).as_posix() for f in sorted(filenames)]
    return make_chunk("repo_index", "repository:activity-service", "tier_3",
                      "Repository file index for activity-service: " + ", ".join(files),
                      {"source_type": "repository", "file_count": len(files)})


# ---------------------------------------------------------------- corpus + vector store

def build_corpus() -> tuple[list[dict[str, Any]], str]:
    activities, assignments, source_id = load_activity_data()
    chunks = activity_chunks(activities, assignments, source_id) + doc_chunks() + [repository_chunk()]
    return chunks, source_id


def write_corpus(chunks: list[dict[str, Any]]) -> None:
    CORPUS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with CORPUS_PATH.open("w", encoding="utf-8") as f:
        for chunk in chunks:
            f.write(json.dumps(chunk) + "\n")


def read_corpus() -> list[dict[str, Any]]:
    if not CORPUS_PATH.exists():
        return []
    with CORPUS_PATH.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def chroma_client():
    if chromadb is None:
        raise RuntimeError("chromadb is not installed")
    return chromadb.PersistentClient(path=str(CHROMA_PATH),
                                     settings=chromadb.Settings(anonymized_telemetry=False))


def get_collection():
    return chroma_client().get_collection(COLLECTION_NAME, embedding_function=None)


def index_chunks(chunks: list[dict[str, Any]], embeddings: list[list[float]], mode: str) -> None:
    client = chroma_client()
    if COLLECTION_NAME in [c.name for c in client.list_collections()]:
        client.delete_collection(COLLECTION_NAME)
    collection = client.create_collection(
        COLLECTION_NAME, embedding_function=None,
        metadata={"hnsw:space": "cosine", "embedding_mode": mode},
    )
    metadatas = [
        {"source_id": c["source_id"], "authority_tier": c["authority_tier"], "indexed_at": c["indexed_at"],
         **{k: v for k, v in c["metadata"].items() if isinstance(v, (str, int, float, bool))}}
        for c in chunks
    ]
    collection.add(ids=[c["chunk_id"] for c in chunks], documents=[c["text"] for c in chunks],
                   metadatas=metadatas, embeddings=embeddings)


# ---------------------------------------------------------------- REFRESH

def refresh_corpus(caller: str = "student", trace_id: str | None = None) -> dict[str, Any]:
    trace_id = trace_id or str(uuid.uuid4())
    start = time.time()
    try:
        chunks, tier_1_source = build_corpus()
        write_corpus(chunks)
        output = {
            "status": "success", "caller": caller, "chunk_count": len(chunks),
            "collection": COLLECTION_NAME, "corpus_path": str(CORPUS_PATH),
            "tier_counts": {t: sum(c["authority_tier"] == t for c in chunks) for t in TIER_ORDER},
            "tier_1_source": tier_1_source,
        }
        output.update(update_vector_store(chunks))
    except Exception as exc:  # the tool contract: structured error, never a raise
        output = error_result(f"refresh failed: {exc}", "tool_error", caller=caller)
    append_audit("refresh_corpus", {"caller": caller}, summarise_refresh(output), trace_id, start)
    return output


def update_vector_store(chunks: list[dict[str, Any]]) -> dict[str, Any]:
    embeddings, mode, fallback_error = embed_texts([c["text"] for c in chunks])
    status = {"vector_store_status": "ready", "embedding_mode": mode}
    if fallback_error:
        status["embedding_fallback"] = fallback_error
    try:
        index_chunks(chunks, embeddings, mode)
    except Exception as exc:  # corpus.jsonl still serves lexical retrieval
        status.update(vector_store_status="degraded", vector_store_error=str(exc))
    return status


def summarise_refresh(output: dict[str, Any]) -> dict[str, Any]:
    keys = ("status", "chunk_count", "tier_counts", "tier_1_source", "vector_store_status",
            "embedding_mode", "embedding_fallback", "vector_store_error", "error", "error_type")
    return {k: output[k] for k in keys if k in output}


# ---------------------------------------------------------------- RETRIEVE

def validate_query(query: Any, k: Any) -> str:
    if not isinstance(query, str) or not query.strip():
        raise InvalidInput("query is required and must be a non-empty string")
    if len(query) > MAX_QUERY_CHARS:
        raise InvalidInput(f"query must be at most {MAX_QUERY_CHARS} characters")
    if isinstance(k, bool) or not isinstance(k, int) or not 1 <= k <= MAX_K:
        raise InvalidInput(f"k must be an integer between 1 and {MAX_K}")
    return query.strip()


def rank(rows: list[dict[str, Any]], k: int) -> list[dict[str, Any]]:
    """Similarity first (smaller distance), authority tier only as a tie-breaker."""
    rows.sort(key=lambda r: (round(r["distance"], 6), TIER_ORDER.get(r["authority_tier"], 9)))
    top = rows[:k]
    for i, row in enumerate(top, start=1):
        row["rank"] = i
    return top


def vector_retrieve(query: str, k: int, trace_id: str) -> tuple[list[dict[str, Any]], str]:
    """Returns (rows, embedding_mode of the index)."""
    try:
        collection = get_collection()
    except Exception:
        collection = None
    if collection is None or collection.count() == 0:
        refreshed = refresh_corpus(caller="auto_refresh", trace_id=trace_id)
        if refreshed.get("vector_store_status") != "ready":
            raise RuntimeError(refreshed.get("vector_store_error") or refreshed.get("error", "refresh failed"))
        collection = get_collection()

    # the query must be embedded in the same space the index was built in
    index_mode = (collection.metadata or {}).get("embedding_mode", "hash")
    query_vectors, used_mode, fallback_error = embed_texts([query], mode=index_mode)
    if used_mode != index_mode:
        raise RuntimeError(fallback_error)

    found = collection.query(query_embeddings=query_vectors, n_results=min(k, collection.count()))
    rows = [
        {"chunk_id": chunk_id, "source_id": meta.get("source_id"), "authority_tier": meta.get("authority_tier"),
         "distance": float(distance), "text": text}
        for chunk_id, meta, distance, text in zip(
            found["ids"][0], found["metadatas"][0], found["distances"][0], found["documents"][0]
        )
    ]
    return rows, index_mode


def lexical_retrieve(query: str, k: int, trace_id: str) -> list[dict[str, Any]]:
    """Token overlap over corpus.jsonl; distance = 1 - fraction of query tokens matched."""
    corpus = read_corpus()
    if not corpus:
        refresh_corpus(caller="auto_refresh", trace_id=trace_id)
        corpus = read_corpus()
    if not corpus:
        raise RuntimeError("corpus unavailable: refresh produced no chunks")
    query_tokens = set(tokenize(query))
    return [
        {"chunk_id": c["chunk_id"], "source_id": c["source_id"], "authority_tier": c["authority_tier"],
         "distance": 1 - len(query_tokens & set(tokenize(c["text"]))) / max(len(query_tokens), 1),
         "text": c["text"]}
        for c in corpus
    ]


def retrieve_context(query: str, k: int = 5, caller: str = "student", trace_id: str | None = None) -> dict[str, Any]:
    trace_id = trace_id or str(uuid.uuid4())
    start = time.time()
    try:
        query = validate_query(query, k)
        try:
            (rows, embedding_mode), mode = vector_retrieve(query, k, trace_id), "vector"
        except Exception as exc:
            logger.warning("vector retrieval unavailable (%s); using lexical fallback", exc)
            rows, embedding_mode, mode = lexical_retrieve(query, k, trace_id), None, "lexical_fallback"
        output = {"status": "success", "query": query, "caller": caller, "k": k, "retrieval_mode": mode,
                  "embedding_mode": embedding_mode, "results": rank(rows, k)}
    except InvalidInput as exc:
        output = error_result(str(exc), "invalid_input", query=query)
    except Exception as exc:
        output = error_result(f"retrieval failed: {exc}", "tool_error", query=query)
    append_audit("retrieve_context", {"query": query, "k": k, "caller": caller}, summarise_retrieval(output),
                 trace_id, start)
    return output


def summarise_retrieval(output: dict[str, Any]) -> dict[str, Any]:
    if output.get("status") != "success":
        return {k: output[k] for k in ("status", "error", "error_type")}
    return {"status": "success", "retrieval_mode": output["retrieval_mode"],
            "chunk_ids": [r["chunk_id"] for r in output["results"]],
            "distances": [round(r["distance"], 4) for r in output["results"]]}


# ---------------------------------------------------------------- ANSWER

def thresholds_for(retrieval: dict[str, Any]) -> tuple[float, float]:
    return DISTANCE_THRESHOLDS[retrieval.get("embedding_mode") or "lexical"]


def confidence_category(results: list[dict[str, Any]], insufficient: bool,
                        thresholds: tuple[float, float]) -> str:
    """Relevance and authority together — see tool-contracts.md."""
    if insufficient or not results:
        return "Unknown"
    strong_distance, relevant_distance = thresholds
    relevant = [r for r in results if r["distance"] <= relevant_distance]
    if not relevant:
        return "Low"
    tier_1_relevant = sum(r["authority_tier"] == "tier_1" for r in relevant)
    if relevant[0]["distance"] <= strong_distance and tier_1_relevant >= 2:
        return "High"
    return "Medium" if relevant[0]["authority_tier"] in ("tier_1", "tier_2") else "Low"


def citation(chunk: dict[str, Any]) -> dict[str, Any]:
    return {"chunk_id": chunk["chunk_id"], "source_id": chunk["source_id"],
            "authority_tier": chunk["authority_tier"]}


def activity_records() -> list[dict[str, Any]]:
    return [c for c in read_corpus() if c["metadata"].get("source_type") == "activity_record"]


def mentioned_category(query: str, records: list[dict[str, Any]]) -> str | None:
    tokens = set(tokenize(query))
    categories = {r["metadata"].get("category") for r in records if r["metadata"].get("category")}
    return next((c for c in sorted(categories) if set(tokenize(c)) <= tokens), None)


def deterministic_answer(query: str) -> tuple[str, list[dict[str, Any]]] | None:
    """Exact answers computed from the full tier_1 record set, not from top-k
    similarity. Returns (answer, cited_chunks) or None when no pattern applies."""
    text = query.lower()
    if "activit" not in text:
        return None
    records = activity_records()
    category = mentioned_category(query, records)
    if category:
        matches = [r for r in records if r["metadata"].get("category") == category]
        names = ", ".join(f"{r['metadata']['name']} (activity_id={r['metadata']['activity_id']})" for r in matches)
        return f"There are {len(matches)} {category} activities: {names}.", matches
    if re.search(r"\bhow many\b|\bnumber of\b|\btotal\b|\bcount\b", text):
        summary = [c for c in read_corpus() if c["chunk_id"] == "activity_count"]
        return f"There are {len(records)} activities in total.", summary or records
    return None


def build_prompt(query: str, context: list[dict[str, Any]]) -> str:
    blocks = "\n".join(f"[{c['chunk_id']}] ({c['authority_tier']}) {c['text']}" for c in context)
    return (
        "You answer questions about the VoyageAI Activity Manager.\n"
        "Use only the context below. Do not use outside knowledge.\n"
        f"If the context does not contain the answer, reply exactly: {INSUFFICIENT}\n"
        "Otherwise reply in exactly this format:\n"
        "Answer: <one or two sentences>\n"
        "Evidence: <the [chunk_id]s you used>\n\n"
        f"Context:\n{blocks}\n\nQuestion: {query}"
    )


def generate(prompt: str) -> str:
    response = requests.post(
        OLLAMA_GENERATE_URL,
        json={"model": OLLAMA_MODEL, "prompt": prompt, "stream": False, "options": {"temperature": 0}},
        timeout=OLLAMA_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    return (response.json().get("response") or "").strip()


def parse_generation(raw: str, context: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
    """Returns (answer_text, cited_chunks). Cites the chunk ids the model named
    under Evidence when they exist in the context, else the whole context."""
    if raw.startswith(INSUFFICIENT) or not raw:
        return INSUFFICIENT, []
    match = re.search(r"Answer:\s*(.*?)(?:\n\s*Evidence:(.*))?$", raw, re.DOTALL | re.IGNORECASE)
    answer = match.group(1).strip() if match else raw
    evidence = (match.group(2) or "") if match else ""
    named = [c for c in context if c["chunk_id"] in evidence]
    return answer, named or context


def answer_question(query: str, k: int = 5, caller: str = "student", trace_id: str | None = None) -> dict[str, Any]:
    trace_id = trace_id or str(uuid.uuid4())
    start = time.time()
    retrieval = retrieve_context(query, k, caller=caller, trace_id=trace_id)
    if retrieval["status"] != "success":
        output = {**retrieval, "error": f"retrieval failed: {retrieval['error']}"}
    else:
        try:
            output = grounded_answer(retrieval)
        except Exception as exc:  # e.g. an unreadable corpus.jsonl
            output = error_result(f"answer failed: {exc}", "tool_error", query=retrieval["query"])
    append_audit("answer_question", {"query": query, "k": k, "caller": caller}, summarise_answer(output),
                 trace_id, start)
    return output


def grounded_answer(retrieval: dict[str, Any]) -> dict[str, Any]:
    query, results = retrieval["query"], retrieval["results"]
    summary = {"k": retrieval["k"], "retrieved_count": len(results),
               "retrieval_mode": retrieval["retrieval_mode"], "embedding_mode": retrieval["embedding_mode"],
               "top_chunk": results[0]["chunk_id"] if results else None}
    base = {"query": query, "retrieval_summary": summary}

    exact = deterministic_answer(query)
    if exact:
        answer, cited = exact
        return {"status": "success", **base, "answer": answer, "answer_source": "deterministic",
                "citations": [citation(c) for c in cited], "confidence_category": "High"}

    thresholds = thresholds_for(retrieval)
    context = [r for r in results if r["distance"] <= thresholds[1]]
    if not context:  # nothing relevant: refuse without asking the model to guess
        return {"status": "success", **base, "answer": INSUFFICIENT, "answer_source": "deterministic",
                "citations": [], "confidence_category": "Unknown"}
    try:
        raw = generate(build_prompt(query, context))
    except (requests.exceptions.RequestException, ValueError) as exc:
        return error_result(f"LLM unavailable ({OLLAMA_MODEL}): {exc}", "llm_unavailable", **base,
                            citations=[citation(c) for c in context])
    answer, cited = parse_generation(raw, context)
    return {"status": "success", **base, "answer": answer, "answer_source": "llm",
            "citations": [citation(c) for c in cited],
            "confidence_category": confidence_category(results, answer == INSUFFICIENT, thresholds),
            "model": OLLAMA_MODEL, "model_output": raw}


def summarise_answer(output: dict[str, Any]) -> dict[str, Any]:
    if output.get("status") != "success":
        return {k: output[k] for k in ("status", "error", "error_type") if k in output}
    return {"status": "success", "answer": output["answer"][:200], "answer_source": output["answer_source"],
            "confidence_category": output["confidence_category"],
            "citation_ids": [c["chunk_id"] for c in output["citations"]]}
