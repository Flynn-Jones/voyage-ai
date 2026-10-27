"""Shared VoyageAI RAG pipeline: corpus build, retrieval, and grounded answers.

Runs locally (not containerised). Corpus sources are read over the same
microservices-net HTTP ports each backend already uses (destination-db,
accommodation-db, activity-db, budget-db, itinerary-db) plus repo
documentation, so the corpus reflects live feature data without the RAG
server needing filesystem access to any container's SQLite volume.

Every chunk carries metadata.service naming the feature it came from
("destination", "accommodation", "activity", "budget", "itinerary", or
"shared" for repo-wide docs/index). One corpus.jsonl and one Chroma
collection hold all of them; scope="itinerary" / scope="activity" narrow
retrieval to one feature's slice.
"""
import hashlib
import json
import logging
import os
import re
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
import itinerary_context

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent
REPO_ROOT = BASE_DIR.parent.parent  # ai-services/rag-server -> ai-services -> repo root
DATA_DIR = Path(os.getenv("RAG_DATA_DIR", str(BASE_DIR)))
CORPUS_PATH = DATA_DIR / "corpus" / "corpus.jsonl"
AUDIT_PATH = DATA_DIR / "rag-audit.jsonl"
CHROMA_PATH = DATA_DIR / "chroma"

BUDGET_DB_URL = os.environ.get("BUDGET_DB_URL", "http://localhost:6004")
ACCOMMODATION_DB_URL = os.environ.get("ACCOMMODATION_DB_URL", "http://localhost:6002")
DESTINATION_DB_URL = os.environ.get("DESTINATION_DB_URL", "http://localhost:6001")

# Activity (Student 3), served with scope="activity"; see the Activity section below.
ACTIVITY_SERVICE = "activity"
ACTIVITY_DB_URL = os.environ.get("ACTIVITY_DB_URL", "http://localhost:6003").rstrip("/")
try:
    ACTIVITY_DB_TIMEOUT_SECONDS = float(os.environ.get("ACTIVITY_DB_TIMEOUT_SECONDS", "5"))
except ValueError:
    ACTIVITY_DB_TIMEOUT_SECONDS = 5.0

ACTIVITIES_SOURCE = "activity-db:/activities"
ASSIGNMENTS_SOURCE = "activity-db:/assignments"
ACTIVITY_DOCS_SUBDIR = Path("activity-service") / "docs"
ACTIVITY_GENERATED_REPORT_PREFIX = "rag-"
ACTIVITY_RUN_LOG_DOCS = {"run-report.md", "test-results.md"}
ACTIVITY_DOC_CHUNK_WORDS = 80
ACTIVITY_MAX_K = 20
ACTIVITY_MAX_QUERY_CHARS = 1000
ACTIVITY_IGNORED_DIRS = {".git", ".venv", "venv", "__pycache__", ".pytest_cache", "node_modules", "chroma"}

OLLAMA_GENERATE_URL = os.environ.get("OLLAMA_GENERATE_URL", "http://localhost:11434/api/generate")
# Ollama runs CPU-only on the shared AI host, so a single generation can take minutes.
# Env-configurable with a generous default; see docs/release-2/cloud/ai-timeouts.md for
# the full ladder (each hop must outwait the one it calls).
try:
    OLLAMA_TIMEOUT_SECONDS = float(os.environ.get("OLLAMA_TIMEOUT_SECONDS", "180"))
except ValueError:
    OLLAMA_TIMEOUT_SECONDS = 180.0
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

# Feature slices that only scoped retrieval searches. Activity's records and docs
# joined the shared corpus after the other features calibrated their unscoped
# queries, so keeping them out of unscoped ranking leaves those results unchanged
# ("street food" must not start matching the Street Food Night Walk activity in a
# destination answer). Activity's backend always sends scope="activity".
SCOPED_ONLY_SERVICES = {ACTIVITY_SERVICE}

# metadata.service for each repo document, by its first path component.
DOC_SERVICE_BY_DIR = {"budget-service": "budget", "accommodation-service": "accommodation",
                      "student-1": "destination", "itinerary-service": "itinerary",
                      "activity-service": "activity"}

IGNORED_DIRS = {".git", ".venv", "venv", "__pycache__", "node_modules", "chroma"}
COLLECTION_NAME = "voyageai_shared_context"
EMBED_VECTOR_SIZE = 256

_collection = None
_last_corpus_chunks: list[dict[str, Any]] = []
_itinerary_source_error = None
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
    import chromadb
    global _collection
    if _collection is None:
        client = chromadb.PersistentClient(path=str(CHROMA_PATH))
        _collection = client.get_or_create_collection(name=COLLECTION_NAME)
    return _collection


def reset_collection() -> None:
    import chromadb
    global _collection
    client = chromadb.PersistentClient(path=str(CHROMA_PATH))
    try:
        client.delete_collection(name=COLLECTION_NAME)
    except Exception as exc:  # first build: nothing to delete yet
        logger.info("reset_collection: no existing %s to delete (%s)", COLLECTION_NAME, exc)
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
        # Only real destination rows become evidence; anything else is never indexed.
        if not isinstance(record, dict) or record.get("destination_id") is None or not all(
            isinstance(record.get(key), str) and record[key].strip() for key in ("city", "country")
        ):
            continue
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


def tag_service(chunks: list[dict[str, Any]], service: str) -> list[dict[str, Any]]:
    """Set metadata.service on every chunk (keeping one a loader already set)."""
    for chunk in chunks:
        chunk.setdefault("metadata", {}).setdefault("service", service)
    return chunks


def doc_service(source_id: str) -> str:
    return DOC_SERVICE_BY_DIR.get(str(source_id).replace("\\", "/").split("/", 1)[0], "shared")


# ---------------------------------------------------------------- Activity (Student 3)
#
# Activity's slice of the shared corpus, served with scope="activity" by
# activity_operation() below.
#
# Ported from the retired activity-service/rag-server/rag_pipeline.py. What
# carries over, because the Activity backend and frontend rely on it:
#
# - tier_1 chunk text and ids (activity_<id>, assignment_<id>, activity_count,
#   activity_category_counts, activity_category_<slug>) and the per-category
#   summary chunks;
# - tier_2 chunks from activity-service/docs and a tier_3 file index. Generated
#   rag-*.md reports are excluded (metrics tables must never become evidence),
#   and so are the dated run logs (ACTIVITY_RUN_LOG_DOCS): they quote the sample records
#   of the day they were written ("Harbour Kayaking Tour, 45.00"), which would
#   otherwise ground confident answers about activities that no longer exist.
#   Record facts come from tier_1 only;
# - deterministic answers for count / per-category questions, computed from the
#   full activity record set rather than top-k similarity;
# - the query / k validation (1-1000 chars, integer k from 1 to 20).
#
# What does NOT carry over: reading the activity SQLite file. Like every other
# shared source, tier_1 data comes over the activity database-service HTTP API
# only (ACTIVITY_DB_URL), never another service's database file.

def _activity_tokenize(text):
    return _TOKEN_RE.findall((text or "").lower())


def _activity_slug(value):
    return "_".join(_activity_tokenize(value)) or "unknown"


def _activity_chunk(chunk_id, source_id, tier, text, metadata):
    return {"chunk_id": chunk_id, "source_id": source_id, "authority_tier": tier, "text": text,
            "metadata": {"service": ACTIVITY_SERVICE, **metadata}}


# Activity tier_1: records from the activity database API

def _activity_get_list(path):
    response = requests.get(f"{ACTIVITY_DB_URL}{path}", timeout=ACTIVITY_DB_TIMEOUT_SECONDS)
    response.raise_for_status()
    rows = response.json()
    if not isinstance(rows, list) or any(not isinstance(r, dict) for r in rows):
        raise ValueError(f"activity database {path} must return a list of objects")
    return rows


def _activity_is_id(value):
    return isinstance(value, int) and not isinstance(value, bool) and value >= 1


def _activity_format_price(value):
    return f"{float(value):.2f}" if isinstance(value, (int, float)) and not isinstance(value, bool) else "unknown"


def load_activity_records():
    """tier_1 activity, assignment and summary chunks from the activity DB API.
    Raises requests.RequestException / ValueError when the API is unusable."""
    # Only real rows become evidence: an id and a name, as the destination loader requires.
    activities = [a for a in _activity_get_list("/activities") if _activity_is_id(a.get("activity_id"))
                  and isinstance(a.get("activity_name"), str) and a["activity_name"].strip()]
    assignments = [s for s in _activity_get_list("/assignments") if _activity_is_id(s.get("assignment_id"))
                   and _activity_is_id(s.get("activity_id"))]
    names = {a["activity_id"]: a.get("activity_name") for a in activities}

    chunks = [
        _activity_chunk(
            f"activity_{a['activity_id']}", ACTIVITIES_SOURCE, "tier_1",
            f"Activity record: activity_id={a['activity_id']}, name={a.get('activity_name')}, "
            f"category={a.get('activity_type')}, price={_activity_format_price(a.get('activity_cost'))}, "
            f"duration={a.get('duration')}.",
            {"source_type": "activity_record", "activity_id": a["activity_id"],
             "name": a.get("activity_name"), "category": a.get("activity_type")},
        )
        for a in activities
    ]
    chunks += [
        _activity_chunk(
            f"assignment_{s['assignment_id']}", ASSIGNMENTS_SOURCE, "tier_1",
            f"Assignment record: assignment_id={s['assignment_id']}, activity_id={s.get('activity_id')}, "
            f"activity_name={names.get(s.get('activity_id'))}, scheduled_time={s.get('assignment_time')}.",
            {"source_type": "assignment_record", "activity_id": s.get("activity_id")},
        )
        for s in assignments
    ]
    return chunks + activity_summary_chunks(activities)


def activity_summary_chunks(activities):
    by_category = {}
    for a in activities:
        by_category.setdefault(a.get("activity_type") or "Uncategorised", []).append(a)

    counts = ", ".join(f"{cat}={len(items)}" for cat, items in sorted(by_category.items()))
    chunks = [
        _activity_chunk("activity_count", ACTIVITIES_SOURCE, "tier_1",
                        f"Activity summary: there are {len(activities)} activities in total in the activities database.",
                        {"source_type": "summary", "metric": "total_count", "value": len(activities)}),
        _activity_chunk("activity_category_counts", ACTIVITIES_SOURCE, "tier_1",
                        f"Activity summary by category: {counts}.",
                        {"source_type": "summary", "metric": "count_per_category"}),
    ]
    for category, items in sorted(by_category.items()):
        listed = ", ".join(f"{a.get('activity_name')} (activity_id={a['activity_id']})" for a in items)
        chunks.append(_activity_chunk(
            f"activity_category_{_activity_slug(category)}", ACTIVITIES_SOURCE, "tier_1",
            f"Activity category summary: category={category} has {len(items)} activities: {listed}.",
            {"source_type": "summary", "metric": "category", "category": category, "value": len(items)},
        ))
    return chunks


# Activity tier_2 / tier_3: activity-service docs and file index

def load_activity_doc_chunks(repo_root):
    """tier_2 chunks from activity-service/docs plus a tier_3 activity file index."""
    docs_dir = Path(repo_root) / ACTIVITY_DOCS_SUBDIR
    chunks = []
    if docs_dir.is_dir():
        paths = sorted(
            p for p in docs_dir.rglob("*")
            if p.suffix in (".md", ".json") and p.is_file() and not p.is_symlink()
            and not p.name.startswith(ACTIVITY_GENERATED_REPORT_PREFIX) and p.name not in ACTIVITY_RUN_LOG_DOCS
        )
        for path in paths:
            rel = path.relative_to(repo_root).as_posix()
            words = path.read_text(encoding="utf-8", errors="ignore").split()
            for i in range(0, len(words), ACTIVITY_DOC_CHUNK_WORDS):
                part = i // ACTIVITY_DOC_CHUNK_WORDS + 1
                chunks.append(_activity_chunk(f"activity_doc_{_activity_slug(path.stem)}_{part}", rel, "tier_2",
                                              " ".join(words[i:i + ACTIVITY_DOC_CHUNK_WORDS]),
                                              {"source_type": "doc", "file": rel, "part": part}))

    service_root = Path(repo_root) / "activity-service"
    files = []
    for root, dirs, filenames in os.walk(service_root, followlinks=False):
        dirs[:] = sorted(d for d in dirs if d not in ACTIVITY_IGNORED_DIRS)
        files += [(Path(root) / f).relative_to(service_root).as_posix() for f in sorted(filenames)]
    if files:
        chunks.append(_activity_chunk("activity_repo_index", "repository:activity-service", "tier_3",
                                      "Repository file index for activity-service: " + ", ".join(files),
                                      {"source_type": "repository", "file_count": len(files)}))
    return chunks


# Activity retrieval helpers

def validate_activity_query(query, k):
    if not isinstance(query, str) or not query.strip():
        raise ValueError("query is required and must be a non-empty string")
    if len(query) > ACTIVITY_MAX_QUERY_CHARS:
        raise ValueError(f"query must be at most {ACTIVITY_MAX_QUERY_CHARS} characters")
    if isinstance(k, bool) or not isinstance(k, int) or not 1 <= k <= ACTIVITY_MAX_K:
        raise ValueError(f"k must be an integer between 1 and {ACTIVITY_MAX_K}")
    return query.strip()


def select_activity_chunks(chunks):
    return [c for c in chunks if (c.get("metadata") or {}).get("service") == ACTIVITY_SERVICE]


def _activity_records(chunks):
    return [c for c in chunks if c["metadata"].get("source_type") == "activity_record"]


def activity_mentioned_category(query, records):
    tokens = set(_activity_tokenize(query))
    categories = {r["metadata"].get("category") for r in records if r["metadata"].get("category")}
    return next((c for c in sorted(categories) if set(_activity_tokenize(c)) <= tokens), None)


def activity_deterministic_answer(chunks, query):
    """Exact answers from the full tier_1 record set, not from top-k
    similarity. Returns (answer, cited_chunks) or None when no pattern applies."""
    text = query.lower()
    if "activit" not in text:
        return None
    records = _activity_records(chunks)
    category = activity_mentioned_category(query, records)
    if category:
        matches = [r for r in records if r["metadata"].get("category") == category]
        names = ", ".join(f"{r['metadata']['name']} (activity_id={r['metadata']['activity_id']})" for r in matches)
        return f"There are {len(matches)} {category} activities: {names}.", matches
    if re.search(r"\bhow many\b|\bnumber of\b|\btotal\b|\bcount\b", text):
        summary = [c for c in chunks if c["chunk_id"] == "activity_count"]
        return f"There are {len(records)} activities in total.", summary or records
    return None


def load_activity_chunks() -> list[dict[str, Any]]:
    """Activity tier_1 records (activity-db HTTP API) plus activity-service docs."""
    chunks: list[dict[str, Any]] = []
    try:
        chunks.extend(load_activity_records())
        _source_status["activity_db"] = "ok"
    except (requests.exceptions.RequestException, ValueError, TypeError, KeyError) as exc:
        _source_status["activity_db"] = f"unavailable: {exc}"
    chunks.extend(load_activity_doc_chunks(REPO_ROOT))
    for chunk in chunks:
        chunk["indexed_at"] = now_iso()
    return chunks


def build_corpus() -> list[dict[str, Any]]:
    global _itinerary_source_error
    _source_status.clear()
    _missing_docs.clear()
    chunks: list[dict[str, Any]] = []
    chunks.extend(tag_service(load_destination_chunks(), "destination"))
    chunks.extend(tag_service(load_budget_chunks(), "budget"))
    chunks.extend(tag_service(load_accommodation_chunks(), "accommodation"))
    for chunk in load_doc_chunks():
        chunks.extend(tag_service([chunk], doc_service(chunk["source_id"])))
    chunks.extend(tag_service(load_repository_chunks(), "shared"))
    chunks.extend(load_activity_chunks())
    try:
        itinerary_chunks = itinerary_context.load_chunks()
        for chunk in itinerary_chunks:
            chunk["indexed_at"] = now_iso()
        chunks.extend(tag_service(itinerary_chunks, "itinerary"))
        _itinerary_source_error = None
        _source_status["itinerary_db"] = "ok"
    except (requests.RequestException, ValueError, TypeError) as exc:
        _itinerary_source_error = "itinerary API unavailable or invalid; itinerary records were not indexed"
        _source_status["itinerary_db"] = f"unavailable: {exc}"
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


def _all_chunks() -> list[dict[str, Any]]:
    chunks = _last_corpus_chunks or read_corpus()
    return [c for c in chunks if str(c.get("source_id", "")).replace("\\", "/") not in EXCLUDED_SOURCE_IDS]


def _corpus_chunks() -> list[dict[str, Any]]:
    """The unscoped candidate set: everything except scoped-only feature slices."""
    return [c for c in _all_chunks()
            if (c.get("metadata") or {}).get("service") not in SCOPED_ONLY_SERVICES]


def _chroma_distances(query: str, k: int, service: str = None, n_results: int = None) -> dict[str, float]:
    collection = get_collection()
    if collection.count() == 0:
        if refresh_corpus(caller="auto_refresh").get("status") != "success":
            raise RuntimeError("empty_collection")
        collection = get_collection()  # refresh replaces the collection handle
    count = collection.count()
    if count == 0:
        raise RuntimeError("empty_collection")
    kwargs = {"where": {"service": service}} if service else {}
    results = collection.query(query_embeddings=embed_texts([query]),
                               n_results=min(count, n_results or max(4 * k, 25)), **kwargs)
    ids = (results.get("ids") or [[]])[0]
    distances = (results.get("distances") or [[]])[0]
    return {chunk_id: distances[i] for i, chunk_id in enumerate(ids) if i < len(distances)}


def rank_accepted(
    query: str, corpus: list[dict[str, Any]], distances: dict[str, float], k: int,
    extra_entities: set[str] = None,
) -> tuple[list[dict[str, Any]], int]:
    """Relevance-first acceptance and ranking. Returns (accepted top-k, accepted total).

    extra_entities lets a scoped caller name more single-term "entity" matches
    (Activity passes its categories) alongside Destination cities/countries.
    """
    q_terms = query_terms(query)
    if not q_terms:
        return [], 0
    entities: set[str] = set(extra_entities or ())
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


def _service_counts(chunks: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for c in chunks:
        service = (c.get("metadata") or {}).get("service", "shared")
        counts[service] = counts.get(service, 0) + 1
    return dict(sorted(counts.items()))


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
                    {"source_id": c["source_id"], "authority_tier": c["authority_tier"], "indexed_at": c["indexed_at"],
                     "service": (c.get("metadata") or {}).get("service", "shared")}
                    for c in chunks
                ]
                collection.add(ids=ids, documents=docs, metadatas=metas, embeddings=embed_texts(docs))
        except Exception as exc:
            vector_store_status, vector_store_error = "degraded", str(exc)

        output = {
            "status": "success",
            "caller": caller,
            "chunk_count": len(chunks),
            "itinerary_chunk_count": sum(c.get("source_id") == itinerary_context.SOURCE for c in chunks),
            "itinerary_source_error": _itinerary_source_error,
            "activity_chunk_count": sum((c.get("metadata") or {}).get("service") == ACTIVITY_SERVICE
                                        for c in chunks),
            "tier_counts": {t: sum(c.get("authority_tier") == t for c in chunks) for t in TIER_ORDER},
            "service_counts": _service_counts(chunks),
            "embedding_mode": "hash",
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


def retrieve_context(query: str, k: int = 5, caller: str = "system", scope: str = None,
                     trip_reference: str = None, day: int = None) -> dict[str, Any]:
    if scope == "itinerary":
        return itinerary_operation(query, k, caller, trip_reference, day, answer=False)
    if scope == ACTIVITY_SERVICE:
        return activity_operation(query, k, caller, answer=False)
    if scope is not None:
        return {"status": "error", "error": "unknown scope"}
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
Answer in complete, natural, conversational sentences for a traveller that name the relevant
destinations or facts; keep it to one or two sentences unless the question asks for a list.
If the question asks for a list (e.g. "what accommodation options" or "what
expenses"), list every matching record present in the context -- do not stop
after the first one you find.
Do not copy raw "Accommodation record: name=X, ..." or "Expense record: ..." lines
from the context -- rephrase each fact in plain language while keeping every
name and number exact.
If evidence is missing, return exactly: Insufficient evidence.
Do not return Insufficient evidence merely because the context is about a
different part of the app than you expected.

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
            timeout=OLLAMA_TIMEOUT_SECONDS,
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


def answer_question(query: str, k: int = 5, caller: str = "system", scope: str = None,
                    trip_reference: str = None, day: int = None) -> dict[str, Any]:
    if scope == "itinerary":
        return itinerary_operation(query, k, caller, trip_reference, day, answer=True)
    if scope == ACTIVITY_SERVICE:
        return activity_operation(query, k, caller, answer=True)
    if scope is not None:
        return {"status": "error", "error": "unknown scope"}
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
        "top_score": results[0].get("relevance_score") if results else None,
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

    # Strip the "Answer:" scaffold BEFORE checking for an abstention: the model
    # often replies "Answer: Insufficient evidence." (often across two lines),
    # which must not pass as a cited, confident answer.
    if answer.lower().startswith("answer:"):
        answer = answer[len("answer:"):].strip()
    if answer.lower().startswith("insufficient evidence"):
        return insufficient("llm_declined")

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


def activity_operation(query, k, caller, answer):
    """scope="activity": retrieve / answer over the activity slice of the shared corpus.

    Ranking and generation are the shared ones (rank_accepted, generate_with_ollama);
    candidates are restricted to metadata.service == "activity", and count /
    per-category questions get Activity's deterministic answer from the full record
    set. Every row carries a numeric distance (Chroma's, or 1 - relevance_score in
    lexical fallback) because Activity's RAG card renders it.
    """
    start = time.time()
    tool_name = "answer_question" if answer else "retrieve_context"
    audit_input = {"query": query, "k": k, "caller": caller, "scope": ACTIVITY_SERVICE}

    def finish(output, validation_status, outcome):
        append_audit(tool_name, audit_input,
                     {key: output.get(key) for key in ("status", "error", "error_type", "confidence_category",
                                                       "answer_source", "retrieval_mode") if key in output}
                     | {"chunk_ids": [r["chunk_id"] for r in output.get("results", output.get("citations", []))]},
                     validation_status, outcome, start)
        return output

    try:
        query = validate_activity_query(query, k)
    except ValueError as exc:
        return finish({"status": "error", "error_type": "invalid_input", "error": str(exc), "query": query},
                      "fail", "invalid_input")

    try:
        chunks = select_activity_chunks(_all_chunks())
        if not chunks and refresh_corpus(caller="auto_refresh").get("status") == "success":
            chunks = select_activity_chunks(_all_chunks())
        if not chunks:
            return finish({"status": "error", "error_type": "retrieval_failed",
                           "error": "activity corpus unavailable", "query": query}, "fail", "error")

        retrieval_mode = "hybrid"
        try:
            distances = _chroma_distances(query, k, service=ACTIVITY_SERVICE, n_results=len(chunks))
        except Exception:
            distances, retrieval_mode = {}, "lexical_fallback"
        # An activity category ("Food", "Water Sports") is to Activity what a city is to
        # Destination: one matching term is enough ("food walk" -> the Food activities).
        categories = {term for c in chunks if c["metadata"].get("source_type") == "activity_record"
                      for term in query_terms(c["metadata"].get("category") or "")}
        ranked, accepted_total = rank_accepted(query, chunks, distances, k, extra_entities=categories)
        for row in ranked:
            if not isinstance(row["distance"], (int, float)):
                row["distance"] = round(1 - row["relevance_score"], 4)
    except Exception as exc:
        return finish({"status": "error", "error_type": "retrieval_failed", "error": str(exc), "query": query},
                      "fail", "error")

    retrieval = {"status": "success", "query": query, "caller": caller, "k": k,
                 "scope": ACTIVITY_SERVICE, "retrieval_mode": retrieval_mode,
                 "candidate_count": len(chunks), "rejected_count": len(chunks) - accepted_total,
                 "results": ranked}
    if not answer:
        return finish(retrieval, "pass", "context_retrieved" if ranked else "no_relevant_context")

    summary = {"k": k, "retrieved_count": len(ranked), "candidate_count": len(chunks),
               "rejected_count": retrieval["rejected_count"], "retrieval_mode": retrieval_mode,
               "top_score": ranked[0]["relevance_score"] if ranked else None,
               "top_chunk": ranked[0]["chunk_id"] if ranked else None}
    base = {"query": query, "scope": ACTIVITY_SERVICE, "retrieval_summary": summary}

    def cite(rows):
        return [{key: r.get(key) for key in ("chunk_id", "source_id", "authority_tier")} for r in rows]

    exact = activity_deterministic_answer(chunks, query)
    if exact:
        text, cited = exact
        return finish({"status": "success", **base, "answer": text, "answer_source": "deterministic",
                       "citations": cite(cited), "confidence_category": "High"}, "pass", "deterministic_answer")

    def insufficient(answer_source, outcome):
        return finish({"status": "insufficient_context", **base, "answer": INSUFFICIENT_ANSWER,
                       "answer_source": answer_source, "citations": [], "confidence_category": "Insufficient"},
                      "pass", outcome)

    if not ranked:  # nothing relevant: refuse without asking the model to guess
        return insufficient("retrieval_guard", "insufficient_context")

    context = "\n\n".join(f"[{r['chunk_id']}] {r['text']}" for r in ranked)
    try:
        generated = generate_with_ollama(query, context)
    except LLMUnavailable as exc:
        return finish(_failure(query, "llm_unavailable", str(exc), scope=ACTIVITY_SERVICE,
                               retrieval_summary=summary), "fail", "llm_unavailable")
    if generated.lower().startswith("answer:"):  # strip first; see answer_question
        generated = generated[len("answer:"):].strip()
    if generated.lower().startswith("insufficient evidence"):
        return insufficient("llm", "llm_declined")
    return finish({"status": "success", **base, "answer": generated, "answer_source": "llm",
                   "citations": cite(ranked), "confidence_category": confidence_from_results(ranked),
                   "model": OLLAMA_MODEL}, "pass", "answer_generated")


class ItineraryModelError(Exception):
    """The scoped RAG model is unavailable or returned an unusable response."""


def generate_itinerary_answer(prompt):
    """Strict local Ollama generation for scoped answers; no deterministic fallback."""
    try:
        response = requests.post(
            OLLAMA_GENERATE_URL,
            json={"model": OLLAMA_MODEL, "prompt": prompt, "stream": False,
                  "options": {"temperature": 0, "num_predict": 256}},
            timeout=OLLAMA_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        body = response.json()
        if not isinstance(body, dict) or body.get("error"):
            raise ValueError("invalid model response")
        answer = body.get("response")
        if not isinstance(answer, str) or not answer.strip():
            raise ValueError("empty or invalid model answer")
        return answer.strip()
    except (requests.RequestException, ValueError) as exc:
        raise ItineraryModelError("RAG answer model is unavailable or returned an invalid response") from exc


def itinerary_operation(query, k, caller, trip_reference, day, answer):
    start = time.time()
    try:
        itinerary_context.validate(query, k, trip_reference, day)
        if not CORPUS_PATH.exists() and not _last_corpus_chunks:
            refreshed = refresh_corpus(caller=caller)
            if refreshed.get("status") != "success":
                raise ValueError("corpus unavailable; refresh required")
        chunks = _last_corpus_chunks or read_corpus()
        retrieval, evidence = itinerary_context.retrieve(chunks, query, k, trip_reference, day)
        output = itinerary_context.answer(retrieval, evidence, generate_itinerary_answer) if answer else retrieval
        if output.get("answer_source") == "llm":
            output["model"] = OLLAMA_MODEL
    except ItineraryModelError as exc:
        output = {"status": "error", "error": str(exc), "error_type": "llm_unavailable"}
    except (ValueError, TypeError, OSError) as exc:
        output = {"status": "error", "error": str(exc)}
    append_audit("answer_question" if answer else "retrieve_context",
                 {"query": query, "scope": "itinerary", "trip_reference": trip_reference, "day": day},
                 {"status": output["status"], "confidence_category": output.get("confidence_category")},
                 "pass" if output["status"] == "success" else "fail", "itinerary_context", start)
    return output


if __name__ == "__main__":
    print(json.dumps(refresh_corpus(), indent=2))
    print(json.dumps(retrieve_context("budget expenses over estimate", 5), indent=2))
    print(json.dumps(answer_question("Which destination is known for street food?", 5), indent=2))
