"""Shared VoyageAI RAG pipeline: corpus build, retrieval, and grounded answers.

Runs locally (not containerised). Corpus sources are read over the same
microservices-net HTTP ports each backend already uses (budget-db,
accommodation-db) plus repo documentation, so the corpus reflects live
feature data without the RAG server needing filesystem access to any
container's SQLite volume.
"""
import hashlib
import json
import os
import re
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import chromadb
import requests

BASE_DIR = Path(__file__).resolve().parent
REPO_ROOT = BASE_DIR.parent.parent  # ai-services/rag-server -> ai-services -> repo root
CORPUS_PATH = BASE_DIR / "corpus" / "corpus.jsonl"
AUDIT_PATH = BASE_DIR / "rag-audit.jsonl"
CHROMA_PATH = BASE_DIR / "chroma"

BUDGET_DB_URL = os.environ.get("BUDGET_DB_URL", "http://localhost:6004")
ACCOMMODATION_DB_URL = os.environ.get("ACCOMMODATION_DB_URL", "http://localhost:6002")
DESTINATION_DB_URL = os.environ.get("DESTINATION_DB_URL", "http://localhost:6001")
OLLAMA_GENERATE_URL = os.environ.get("OLLAMA_GENERATE_URL", "http://localhost:11434/api/generate")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "qwen2.5:7b")

# Only factual docs. README.md / student-1/README.md are excluded on purpose: they hold demo
# prompts and usage examples ("Compare Tokyo and Kyoto for nightlife and food") that are not evidence
# that any destination has those properties.
DOC_SOURCES = [
    REPO_ROOT / "docs" / "ARCHITECTURE.md",
    REPO_ROOT / "budget-service" / "SUMMARY.md",
    REPO_ROOT / "accommodation-service" / "SUMMARY.md",
]

# Legacy persisted corpora may still hold chunks ingested from these (source_id is the repo-relative path).
# They are rejected at retrieval time so stale README/demo text can never be evidence.
EXCLUDED_SOURCE_IDS = {"README.md", "student-1/README.md"}

IGNORED_DIRS = {".git", ".venv", "venv", "__pycache__", "node_modules", "chroma"}
COLLECTION_NAME = "voyageai_shared_context"
EMBED_VECTOR_SIZE = 256

_collection = None
_last_corpus_chunks: list[dict[str, Any]] = []
_source_status: dict[str, str] = {}
_missing_docs: list[str] = []

# Relevance acceptance (see tool-contracts.md "Retrieval acceptance").
MIN_MATCHED = 2
MIN_SCORE = 0.5
INSUFFICIENT_ANSWER = "Insufficient evidence to answer this question from the current corpus."
TIER_ORDER = {"tier_1": 0, "tier_2": 1, "tier_3": 2}
STOPWORDS = set(
    "a an the is are was were be been am of to in on at for and or with what which who whom where when how why "
    "do does did can could should would will i me my you your tell about there any some it its this that these those "
    "from by as if than then so not no yes more most much many very also just into out up over under between near around "
    "give show list please have has had go known use get want best good top recommend place places destination destinations".split()
)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


_TOKEN_RE = re.compile(r"[a-z0-9]+")


def embed_texts(texts: list[str]) -> list[list[float]]:
    vectors: list[list[float]] = []
    for text in texts:
        values = [0.0] * EMBED_VECTOR_SIZE
        # Split on any non-alphanumeric boundary rather than whitespace, so
        # punctuation-glued key=value text (e.g. "destination_id=dest-tokyo,")
        # yields "tokyo" as its own token instead of one opaque blob that
        # never matches a plain-language query mentioning the city by name.
        tokens = _TOKEN_RE.findall((text or "").lower())
        if not tokens:
            vectors.append(values)
            continue
        for token in tokens:
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            for i, byte in enumerate(digest):
                idx = i % EMBED_VECTOR_SIZE
                values[idx] += (byte / 255.0) - 0.5
        norm = sum(v * v for v in values) ** 0.5
        if norm > 0:
            values = [v / norm for v in values]
        vectors.append(values)
    return vectors


def get_collection():
    global _collection
    if _collection is None:
        client = chromadb.PersistentClient(path=str(CHROMA_PATH))
        _collection = client.get_or_create_collection(name=COLLECTION_NAME)
    return _collection


def reset_collection() -> None:
    global _collection
    client = chromadb.PersistentClient(path=str(CHROMA_PATH))
    try:
        client.delete_collection(name=COLLECTION_NAME)
    except Exception:
        pass
    _collection = client.get_or_create_collection(name=COLLECTION_NAME)


def append_audit(tool_name, tool_input, tool_output, validation_status, outcome, start_time) -> None:
    record = {
        "request_id": str(uuid.uuid4()),
        "tool_name": tool_name,
        "tool_input": tool_input,
        "tool_output": tool_output,
        "timestamp": now_iso(),
        "duration_ms": int((time.time() - start_time) * 1000),
        "validation_status": validation_status,
        "outcome": outcome,
    }
    try:
        AUDIT_PATH.parent.mkdir(parents=True, exist_ok=True)
        with AUDIT_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, default=str) + "\n")
    except (OSError, TypeError, ValueError):
        pass  # auditing must never turn a tool call into a failure


def chunk_text(text: str, max_words: int = 80) -> list[str]:
    words = text.split()
    return [" ".join(words[i : i + max_words]).strip() for i in range(0, len(words), max_words) if words[i : i + max_words]]


def load_budget_chunks() -> list[dict[str, Any]]:
    try:
        response = requests.get(f"{BUDGET_DB_URL}/expenses", timeout=5)
        response.raise_for_status()
        expenses = response.json()
    except (requests.exceptions.RequestException, ValueError) as exc:
        _source_status["budget_db"] = f"unavailable: {exc}"
        return []
    _source_status["budget_db"] = "ok"

    chunks = [
        {
            "chunk_id": "budget_expense_count",
            "source_id": "budget-db:/expenses",
            "authority_tier": "tier_1",
            "text": f"There are {len(expenses)} logged budget expenses across all trips.",
            "metadata": {"source_type": "budget_db", "metric": "count"},
            "indexed_at": now_iso(),
        }
    ]
    for expense in expenses[:500]:
        chunks.append(
            {
                "chunk_id": f"budget_expense_{expense.get('id')}",
                "source_id": "budget-db:/expenses",
                "authority_tier": "tier_1",
                "text": (
                    f"Expense record: trip={expense.get('trip_reference')}, "
                    f"item={expense.get('expense')}, category={expense.get('category')}, "
                    f"estimated_cost={expense.get('estimated_cost')}, "
                    f"actual_cost={expense.get('actual_cost')}, status={expense.get('status')}."
                ),
                "metadata": {"source_type": "budget_db", "table": "expenses"},
                "indexed_at": now_iso(),
            }
        )
    return chunks


def load_accommodation_chunks() -> list[dict[str, Any]]:
    try:
        response = requests.get(f"{ACCOMMODATION_DB_URL}/accommodations", timeout=5)
        response.raise_for_status()
        body = response.json()
        records = body.get("data", []) if isinstance(body, dict) else body
    except (requests.exceptions.RequestException, ValueError) as exc:
        _source_status["accommodation_db"] = f"unavailable: {exc}"
        return []
    _source_status["accommodation_db"] = "ok"

    chunks = []
    for record in records[:200]:
        chunks.append(
            {
                "chunk_id": f"accommodation_{record.get('id')}",
                "source_id": "accommodation-db:/accommodations",
                "authority_tier": "tier_1",
                "text": (
                    f"Accommodation record: name={record.get('name')}, "
                    f"destination_id={record.get('destination_id')}, "
                    f"price_per_night={record.get('price_per_night')}, rating={record.get('rating')}."
                ),
                "metadata": {"source_type": "accommodation_db", "table": "accommodations"},
                "indexed_at": now_iso(),
            }
        )
    return chunks


def load_destination_chunks() -> list[dict[str, Any]]:
    """One tier_1 chunk per record from the Destination Database API (never SQLite)."""
    try:
        response = requests.get(f"{DESTINATION_DB_URL}/destinations", timeout=5)
        response.raise_for_status()
        records = response.json()
        if not isinstance(records, list):
            raise ValueError("unexpected /destinations payload")
    except (requests.exceptions.RequestException, ValueError) as exc:
        _source_status["destination_db"] = f"unavailable: {exc}"
        return []
    _source_status["destination_db"] = "ok"

    chunks = []
    for record in records:
        parts = [
            f"Destination record: destination_id={record.get('destination_id')}, "
            f"city={record.get('city')}, country={record.get('country')}."
        ]
        if record.get("description"):
            parts.append(f"Description: {record['description']}")
        if record.get("average_daily_cost") is not None:
            parts.append(f"Average daily cost: {record['average_daily_cost']}.")
        if record.get("recommended_trip_length") is not None:
            parts.append(f"Recommended trip length: {record['recommended_trip_length']} days.")
        if record.get("travel_style"):
            parts.append(f"Travel style: {record['travel_style']}.")
        categories = record.get("categories")
        if categories:
            parts.append("Categories: " + (", ".join(categories) if isinstance(categories, list) else str(categories)) + ".")
        chunks.append(
            {
                "chunk_id": f"destination_{record.get('destination_id')}",
                "source_id": "destination-db:/destinations",
                "authority_tier": "tier_1",
                "text": " ".join(parts),
                "metadata": {
                    "source_type": "destination_db",
                    "destination_id": record.get("destination_id"),
                    "city": record.get("city"),
                    "country": record.get("country"),
                },
                "indexed_at": now_iso(),
            }
        )
    return chunks


def load_doc_chunks() -> list[dict[str, Any]]:
    chunks = []
    for path in DOC_SOURCES:
        if not path.exists():
            try:
                _missing_docs.append(str(path.relative_to(REPO_ROOT)).replace("\\", "/"))
            except ValueError:
                _missing_docs.append(str(path))
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        rel = path.relative_to(REPO_ROOT)
        for i, chunk in enumerate(chunk_text(text), start=1):
            chunks.append(
                {
                    "chunk_id": f"{path.parent.name}_{path.stem}_{i}",
                    "source_id": str(rel).replace("\\", "/"),
                    "authority_tier": "tier_2",
                    "text": chunk,
                    "metadata": {"source_type": "doc", "file": path.name},
                    "indexed_at": now_iso(),
                }
            )
    return chunks


def load_repository_chunks() -> list[dict[str, Any]]:
    files: list[str] = []
    for root, dirs, filenames in os.walk(REPO_ROOT, topdown=True, onerror=lambda e: None):
        dirs[:] = [d for d in dirs if d not in IGNORED_DIRS]
        for filename in filenames:
            try:
                rel = (Path(root) / filename).relative_to(REPO_ROOT)
                files.append(str(rel).replace("\\", "/"))
            except (OSError, ValueError):
                continue

    text = "Repository files include: " + ", ".join(sorted(files[:400]))
    return [
        {
            "chunk_id": "repo_index",
            "source_id": "repository",
            "authority_tier": "tier_3",
            "text": text,
            "metadata": {"source_type": "repository", "file_count": len(files)},
            "indexed_at": now_iso(),
        }
    ]


def build_corpus() -> list[dict[str, Any]]:
    _source_status.clear()
    _missing_docs.clear()
    chunks: list[dict[str, Any]] = []
    chunks.extend(load_destination_chunks())
    chunks.extend(load_budget_chunks())
    chunks.extend(load_accommodation_chunks())
    chunks.extend(load_doc_chunks())
    chunks.extend(load_repository_chunks())
    return chunks


def write_corpus(chunks: list[dict[str, Any]]) -> None:
    CORPUS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with CORPUS_PATH.open("w", encoding="utf-8") as f:
        for chunk in chunks:
            f.write(json.dumps(chunk) + "\n")


def read_corpus() -> list[dict[str, Any]]:
    if not CORPUS_PATH.exists():
        return []
    chunks = []
    with CORPUS_PATH.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    chunks.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return chunks


def _normalise_term(token: str) -> str:
    if len(token) > 4 and token.endswith("ies"):
        return token[:-3] + "y"
    if len(token) > 4 and token.endswith(("ches", "shes", "xes")):
        return token[:-2]
    if len(token) > 3 and token.endswith("s") and not token.endswith("ss"):
        return token[:-1]
    return token


def query_terms(text: str) -> set[str]:
    return {
        _normalise_term(t)
        for t in _TOKEN_RE.findall((text or "").lower())
        if len(t) > 1 and t not in STOPWORDS
    }


def _corpus_chunks() -> list[dict[str, Any]]:
    chunks = _last_corpus_chunks or read_corpus()
    return [c for c in chunks if str(c.get("source_id", "")).replace("\\", "/") not in EXCLUDED_SOURCE_IDS]


def _chroma_distances(query: str, k: int) -> dict[str, float]:
    collection = get_collection()
    if collection.count() == 0:
        if refresh_corpus(caller="auto_refresh").get("status") != "success":
            raise RuntimeError("empty_collection")
        collection = get_collection()  # refresh replaces the collection handle
    count = collection.count()
    if count == 0:
        raise RuntimeError("empty_collection")
    results = collection.query(query_embeddings=embed_texts([query]), n_results=min(count, max(4 * k, 25)))
    ids = (results.get("ids") or [[]])[0]
    distances = (results.get("distances") or [[]])[0]
    return {chunk_id: distances[i] for i, chunk_id in enumerate(ids) if i < len(distances)}


def rank_accepted(
    query: str, corpus: list[dict[str, Any]], distances: dict[str, float], k: int
) -> tuple[list[dict[str, Any]], int]:
    """Relevance-first acceptance and ranking. Returns (accepted top-k, accepted total)."""
    q_terms = query_terms(query)
    if not q_terms:
        return [], 0
    entities: set[str] = set()
    for chunk in corpus:
        meta = chunk.get("metadata") or {}
        if meta.get("source_type") == "destination_db":
            entities |= query_terms(f"{meta.get('city') or ''} {meta.get('country') or ''}")

    accepted = []
    for chunk in corpus:
        matched = q_terms & query_terms(chunk.get("text", ""))
        if not matched:
            continue
        score = len(matched) / len(q_terms)
        if not ((len(matched) >= min(MIN_MATCHED, len(q_terms)) and score >= MIN_SCORE) or matched & entities):
            continue
        accepted.append(
            {
                "rank": 0,
                "chunk_id": chunk.get("chunk_id"),
                "source_id": chunk.get("source_id"),
                "authority_tier": chunk.get("authority_tier"),
                "distance": distances.get(chunk.get("chunk_id")),
                "text": chunk.get("text", ""),
                "relevance_score": round(score, 4),
                "matched_terms": sorted(matched),
            }
        )

    accepted.sort(
        key=lambda r: (
            -r["relevance_score"],
            -len(r["matched_terms"]),
            r["distance"] if isinstance(r["distance"], (int, float)) else 1e9,
            TIER_ORDER.get(r["authority_tier"], 9),
        )
    )
    top = accepted[: max(k, 1)]
    for i, row in enumerate(top, start=1):
        row["rank"] = i
    return top, len(accepted)


def refresh_corpus(caller: str = "system") -> dict[str, Any]:
    global _last_corpus_chunks
    start = time.time()
    try:
        chunks = build_corpus()
        _last_corpus_chunks = chunks
        write_corpus(chunks)
        vector_store_status, vector_store_error = "ready", None

        try:
            reset_collection()
            collection = get_collection()
            if chunks:
                ids = [c["chunk_id"] for c in chunks]
                docs = [c["text"] for c in chunks]
                metas = [
                    {"source_id": c["source_id"], "authority_tier": c["authority_tier"], "indexed_at": c["indexed_at"]}
                    for c in chunks
                ]
                collection.add(ids=ids, documents=docs, metadatas=metas, embeddings=embed_texts(docs))
        except Exception as exc:
            vector_store_status, vector_store_error = "degraded", str(exc)

        output = {
            "status": "success",
            "caller": caller,
            "chunk_count": len(chunks),
            "collection": COLLECTION_NAME,
            "vector_store_status": vector_store_status,
            "source_status": dict(_source_status),
            "missing_docs": list(_missing_docs),
        }
        if vector_store_error:
            output["vector_store_error"] = vector_store_error
        append_audit("refresh_corpus", {"caller": caller}, output, "pass", "corpus_refreshed", start)
        return output
    except Exception as exc:
        output = {"status": "error", "error": str(exc)}
        append_audit("refresh_corpus", {"caller": caller}, output, "fail", "error", start)
        return output


def retrieve_context(query: str, k: int = 5, caller: str = "system") -> dict[str, Any]:
    start = time.time()
    try:
        retrieval_mode = "hybrid"
        distances: dict[str, float] = {}
        try:
            distances = _chroma_distances(query, k)
        except Exception:
            retrieval_mode = "lexical_fallback"

        corpus = _corpus_chunks()
        if not corpus and refresh_corpus(caller="auto_refresh").get("status") == "success":
            corpus = _corpus_chunks()
        if not corpus:
            output = {"status": "error", "error_type": "retrieval_failed", "error": "corpus_unavailable", "query": query}
            append_audit("retrieve_context", {"query": query, "k": k, "caller": caller}, output, "fail", "error", start)
            return output

        ranked, accepted_total = rank_accepted(query, corpus, distances, k)
        output = {
            "status": "success",
            "query": query,
            "caller": caller,
            "k": k,
            "retrieval_mode": retrieval_mode,
            "candidate_count": len(corpus),
            "rejected_count": len(corpus) - accepted_total,
            "results": ranked,
        }
        append_audit(
            "retrieve_context",
            {"query": query, "k": k, "caller": caller},
            {
                "result_count": len(ranked),
                "chunk_ids": [r["chunk_id"] for r in ranked],
                "scores": [r["relevance_score"] for r in ranked],
                "rejected_count": output["rejected_count"],
                "retrieval_mode": retrieval_mode,
            },
            "pass",
            "context_retrieved" if ranked else "no_relevant_context",
            start,
        )
        return output
    except Exception as exc:
        output = {"status": "error", "error_type": "retrieval_failed", "error": str(exc), "query": query}
        append_audit("retrieve_context", {"query": query, "k": k, "caller": caller}, output, "fail", "error", start)
        return output


def confidence_from_results(results: list[dict[str, Any]]) -> str:
    """Confidence from accepted retrieval quality (relevance scores), not tier or count alone."""
    if not results:
        return "Insufficient"
    top = max(r.get("relevance_score", 0) for r in results)
    if all(r.get("authority_tier") == "tier_3" for r in results):
        return "Low"
    if top >= 0.75 and len(results) >= 2:
        return "High"
    if top >= MIN_SCORE and max(len(r.get("matched_terms", [])) for r in results) >= MIN_MATCHED:
        return "Medium"
    return "Low"


class LLMUnavailable(Exception):
    """Ollama unreachable, errored, or returned nothing usable."""


def generate_with_ollama(query: str, context: str) -> str:
    prompt = f"""
You are a retrieval-grounded VoyageAI travel assistant.
Use only the provided context.
Each context item starts with a [chunk_id] label. The labels are references only: never answer with a label.
Answer in one or two complete sentences that name the relevant destinations or facts.
If evidence is missing, return exactly: Insufficient evidence.

QUESTION:
{query}

CONTEXT:
{context}

Return exactly:
Answer:
<answer>
"""
    try:
        resp = requests.post(
            OLLAMA_GENERATE_URL,
            json={"model": OLLAMA_MODEL, "prompt": prompt, "stream": False, "options": {"temperature": 0}},
            timeout=120,
        )
        resp.raise_for_status()
        body = resp.json()
    except (requests.exceptions.RequestException, ValueError) as exc:
        raise LLMUnavailable(f"Ollama unavailable ({OLLAMA_MODEL}): {exc}") from exc
    if not isinstance(body, dict) or body.get("error"):
        raise LLMUnavailable(f"Ollama error ({OLLAMA_MODEL}): {body.get('error') if isinstance(body, dict) else body}")
    text = (body.get("response") or "").strip()
    if not text:
        raise LLMUnavailable(f"Ollama returned an empty response ({OLLAMA_MODEL})")
    return text


def _failure(query: str, error_type: str, error: str, **extra: Any) -> dict[str, Any]:
    return {
        "status": "error",
        "error_type": error_type,
        "error": error,
        "query": query,
        "answer": None,
        "citations": [],
        "confidence_category": None,
        **extra,
    }


def answer_question(query: str, k: int = 5, caller: str = "system") -> dict[str, Any]:
    start = time.time()
    audit_input = {"query": query, "k": k, "caller": caller}
    retrieval = retrieve_context(query=query, k=k, caller=caller)
    if retrieval.get("status") != "success":
        output = _failure(query, "retrieval_failed", retrieval.get("error", "retrieval_failed"))
        append_audit("answer_question", audit_input, output, "fail", "retrieval_failed", start)
        return output

    results = retrieval.get("results", [])
    summary = {
        "k": k,
        "retrieved_count": len(results),
        "candidate_count": retrieval.get("candidate_count", 0),
        "rejected_count": retrieval.get("rejected_count", 0),
        "retrieval_mode": retrieval.get("retrieval_mode"),
        "top_score": results[0]["relevance_score"] if results else None,
    }

    def insufficient(outcome: str) -> dict[str, Any]:
        output = {
            "status": "insufficient_context",
            "query": query,
            "answer": INSUFFICIENT_ANSWER,
            "citations": [],
            "confidence_category": "Insufficient",
            "retrieval_summary": summary,
        }
        append_audit("answer_question", audit_input, {"confidence_category": "Insufficient", "citation_count": 0}, "pass", outcome, start)
        return output

    if not results:
        return insufficient("insufficient_context")

    context = "\n\n".join(f"[{r['chunk_id']}] {r['text']}" for r in results)
    try:
        answer = generate_with_ollama(query, context)
    except LLMUnavailable as exc:
        output = _failure(query, "llm_unavailable", str(exc), retrieval_summary=summary)
        append_audit("answer_question", audit_input, output, "fail", "llm_unavailable", start)
        return output

    if answer.lower().startswith("insufficient evidence"):
        return insufficient("llm_declined")
    if answer.lower().startswith("answer:"):
        answer = answer[len("answer:"):].strip()

    confidence = confidence_from_results(results)
    citations = [
        {"chunk_id": r.get("chunk_id"), "source_id": r.get("source_id"), "authority_tier": r.get("authority_tier")}
        for r in results
    ]
    output = {
        "status": "success",
        "query": query,
        "answer": answer,
        "citations": citations,
        "confidence_category": confidence,
        "retrieval_summary": summary,
    }
    append_audit(
        "answer_question",
        audit_input,
        {"confidence_category": confidence, "citation_ids": [c["chunk_id"] for c in citations]},
        "pass",
        "answer_generated",
        start,
    )
    return output


if __name__ == "__main__":
    print(json.dumps(refresh_corpus(), indent=2))
    print(json.dumps(retrieve_context("budget expenses over estimate", 5), indent=2))
    print(json.dumps(answer_question("Which destination is known for street food?", 5), indent=2))
