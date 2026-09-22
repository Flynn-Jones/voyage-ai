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
OLLAMA_GENERATE_URL = os.environ.get("OLLAMA_GENERATE_URL", "http://localhost:11434/api/generate")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "qwen2.5:7b")

DOC_SOURCES = [
    REPO_ROOT / "docs" / "ARCHITECTURE.md",
    REPO_ROOT / "README.md",
    REPO_ROOT / "budget-service" / "SUMMARY.md",
    REPO_ROOT / "accommodation-service" / "SUMMARY.md",
]

IGNORED_DIRS = {".git", ".venv", "venv", "__pycache__", "node_modules", "chroma"}
COLLECTION_NAME = "voyageai_shared_context"
EMBED_VECTOR_SIZE = 256

_collection = None
_last_corpus_chunks: list[dict[str, Any]] = []


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def embed_texts(texts: list[str]) -> list[list[float]]:
    vectors: list[list[float]] = []
    for text in texts:
        values = [0.0] * EMBED_VECTOR_SIZE
        tokens = (text or "").lower().split()
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
    AUDIT_PATH.parent.mkdir(parents=True, exist_ok=True)
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
    with AUDIT_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")


def chunk_text(text: str, max_words: int = 80) -> list[str]:
    words = text.split()
    return [" ".join(words[i : i + max_words]).strip() for i in range(0, len(words), max_words) if words[i : i + max_words]]


def load_budget_chunks() -> list[dict[str, Any]]:
    try:
        response = requests.get(f"{BUDGET_DB_URL}/expenses", timeout=5)
        response.raise_for_status()
        expenses = response.json()
    except requests.exceptions.RequestException as exc:
        return [
            {
                "chunk_id": "budget_unavailable",
                "source_id": "budget-db:/expenses",
                "authority_tier": "tier_1",
                "text": f"Budget database unavailable: {exc}",
                "metadata": {"source_type": "budget_db"},
                "indexed_at": now_iso(),
            }
        ]

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
        records = response.json().get("data", response.json() if isinstance(response.json(), list) else [])
    except Exception:
        return []

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


def load_doc_chunks() -> list[dict[str, Any]]:
    chunks = []
    for path in DOC_SOURCES:
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        rel = path.relative_to(REPO_ROOT)
        for i, chunk in enumerate(chunk_text(text), start=1):
            chunks.append(
                {
                    "chunk_id": f"{path.stem}_{i}",
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
    chunks: list[dict[str, Any]] = []
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


def lexical_fallback_retrieve(query: str, k: int) -> list[dict[str, Any]]:
    corpus = _last_corpus_chunks or read_corpus()
    query_tokens = set((query or "").lower().split())
    tier_weight = {"tier_1": 3, "tier_2": 2, "tier_3": 1}

    scored = []
    for chunk in corpus:
        text_tokens = set(chunk.get("text", "").lower().split())
        overlap = len(query_tokens.intersection(text_tokens))
        scored.append(
            {
                "rank": 0,
                "chunk_id": chunk.get("chunk_id"),
                "source_id": chunk.get("source_id"),
                "authority_tier": chunk.get("authority_tier"),
                "distance": None,
                "text": chunk.get("text", ""),
                "_score": overlap,
            }
        )

    scored.sort(key=lambda r: (tier_weight.get(r.get("authority_tier"), 0), r.get("_score", 0)), reverse=True)
    top = scored[: max(k, 1)]
    for i, row in enumerate(top, start=1):
        row["rank"] = i
        row.pop("_score", None)
    return top


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
        retrieval_mode = "vector"
        ranked = []
        try:
            collection = get_collection()
            if collection.count() == 0:
                if refresh_corpus(caller="auto_refresh").get("status") != "success":
                    raise RuntimeError("empty_collection")

            results = collection.query(query_embeddings=embed_texts([query]), n_results=k)
            ids = (results.get("ids") or [[]])[0]
            docs = (results.get("documents") or [[]])[0]
            metas = (results.get("metadatas") or [[]])[0]
            distances = (results.get("distances") or [[]])[0]

            for i, chunk_id in enumerate(ids):
                meta = metas[i] if i < len(metas) and isinstance(metas[i], dict) else {}
                ranked.append(
                    {
                        "rank": i + 1,
                        "chunk_id": chunk_id,
                        "source_id": meta.get("source_id"),
                        "authority_tier": meta.get("authority_tier"),
                        "distance": distances[i] if i < len(distances) else None,
                        "text": docs[i] if i < len(docs) else "",
                    }
                )

            tier_weight = {"tier_1": 3, "tier_2": 2, "tier_3": 1}
            ranked.sort(
                key=lambda x: (
                    tier_weight.get(x.get("authority_tier"), 0),
                    -(x.get("distance") if isinstance(x.get("distance"), (int, float)) else 1e9),
                ),
                reverse=True,
            )
        except Exception:
            retrieval_mode = "lexical_fallback"
            if not _last_corpus_chunks and not CORPUS_PATH.exists():
                if refresh_corpus(caller="auto_refresh").get("status") != "success":
                    return {"status": "error", "error": "corpus_unavailable"}
            ranked = lexical_fallback_retrieve(query, k)

        output = {
            "status": "success",
            "query": query,
            "caller": caller,
            "k": k,
            "retrieval_mode": retrieval_mode,
            "results": ranked,
        }
        append_audit(
            "retrieve_context",
            {"query": query, "k": k, "caller": caller},
            {"result_count": len(ranked)},
            "pass",
            "context_retrieved",
            start,
        )
        return output
    except Exception as exc:
        output = {"status": "error", "error": str(exc), "query": query}
        append_audit("retrieve_context", {"query": query, "k": k, "caller": caller}, output, "fail", "error", start)
        return output


def confidence_from_results(results: list[dict[str, Any]]) -> str:
    if not results:
        return "Unknown"
    tier_1 = sum(1 for r in results if r.get("authority_tier") == "tier_1")
    tier_2 = sum(1 for r in results if r.get("authority_tier") == "tier_2")
    if tier_1 >= 2 and len(results) >= 3:
        return "High"
    if tier_1 >= 1 or tier_2 >= 2:
        return "Medium"
    return "Low"


def generate_with_ollama(query: str, context: str) -> str:
    prompt = f"""
You are a retrieval-grounded travel budgeting assistant.
Use only the provided context.
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
            json={"model": OLLAMA_MODEL, "prompt": prompt, "stream": False},
            timeout=120,
        )
        resp.raise_for_status()
        return resp.json().get("response", "Insufficient evidence.")
    except Exception as exc:
        return f"Ollama unavailable: {exc}"


def answer_question(query: str, k: int = 5, caller: str = "system") -> dict[str, Any]:
    start = time.time()
    retrieval = retrieve_context(query=query, k=k, caller=caller)
    if retrieval.get("status") != "success":
        output = {"status": "error", "query": query, "error": retrieval.get("error", "retrieval_failed")}
        append_audit("answer_question", {"query": query, "k": k, "caller": caller}, output, "fail", "retrieval_failed", start)
        return output

    results = retrieval.get("results", [])
    confidence = confidence_from_results(results)

    if confidence == "Unknown" or not results:
        output = {
            "status": "success",
            "query": query,
            "answer": "Insufficient evidence to answer this question from the current corpus.",
            "citations": [],
            "confidence_category": "Insufficient",
        }
        append_audit("answer_question", {"query": query, "k": k, "caller": caller}, output, "pass", "insufficient_context", start)
        return output

    context = "\n\n".join(r.get("text", "") for r in results)
    answer = generate_with_ollama(query, context)
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
        "retrieval_summary": {"k": k, "retrieved_count": len(results)},
    }
    append_audit(
        "answer_question",
        {"query": query, "k": k, "caller": caller},
        {"confidence_category": confidence, "citation_count": len(citations)},
        "pass",
        "answer_generated",
        start,
    )
    return output


if __name__ == "__main__":
    print(json.dumps(refresh_corpus(), indent=2))
    print(json.dumps(retrieve_context("budget expenses over estimate", 5), indent=2))
    print(json.dumps(answer_question("What accommodation options are logged for Tokyo?", 5), indent=2))
