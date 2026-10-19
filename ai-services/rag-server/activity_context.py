"""Activity (Student 3) slice of the shared RAG corpus, served with scope="activity".

Ported from the retired activity-service/rag-server/rag_pipeline.py. What
carries over, because the Activity backend and frontend rely on it:

- tier_1 chunk text and ids (activity_<id>, assignment_<id>, activity_count,
  activity_category_counts, activity_category_<slug>) and the per-category
  summary chunks;
- tier_2 chunks from activity-service/docs and a tier_3 file index. Generated
  rag-*.md reports are excluded (metrics tables must never become evidence),
  and so are the dated run logs (RUN_LOG_DOCS): they quote the sample records
  of the day they were written ("Harbour Kayaking Tour, 45.00"), which would
  otherwise ground confident answers about activities that no longer exist.
  Record facts come from tier_1 only;
- deterministic answers for count / per-category questions, computed from the
  full activity record set rather than top-k similarity;
- the query / k validation (1-1000 chars, integer k from 1 to 20).

What does NOT carry over: reading the activity SQLite file. Like every other
shared source, tier_1 data comes over the activity database-service HTTP API
only (ACTIVITY_DB_URL), never another service's database file.
"""
import os
import re
from pathlib import Path

import requests

SERVICE = "activity"
ACTIVITY_DB_URL = os.environ.get("ACTIVITY_DB_URL", "http://localhost:6003").rstrip("/")
try:
    DB_TIMEOUT_SECONDS = float(os.environ.get("ACTIVITY_DB_TIMEOUT_SECONDS", "5"))
except ValueError:
    DB_TIMEOUT_SECONDS = 5.0

ACTIVITIES_SOURCE = "activity-db:/activities"
ASSIGNMENTS_SOURCE = "activity-db:/assignments"
DOCS_SUBDIR = Path("activity-service") / "docs"
GENERATED_REPORT_PREFIX = "rag-"
RUN_LOG_DOCS = {"run-report.md", "test-results.md"}
DOC_CHUNK_WORDS = 80
MAX_K = 20
MAX_QUERY_CHARS = 1000
IGNORED_DIRS = {".git", ".venv", "venv", "__pycache__", ".pytest_cache", "node_modules", "chroma"}

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def tokenize(text):
    return _TOKEN_RE.findall((text or "").lower())


def slug(value):
    return "_".join(tokenize(value)) or "unknown"


def chunk(chunk_id, source_id, tier, text, metadata):
    return {"chunk_id": chunk_id, "source_id": source_id, "authority_tier": tier, "text": text,
            "metadata": {"service": SERVICE, **metadata}}


# ---------------------------------------------------------------- tier_1

def _get_list(path):
    response = requests.get(f"{ACTIVITY_DB_URL}{path}", timeout=DB_TIMEOUT_SECONDS)
    response.raise_for_status()
    rows = response.json()
    if not isinstance(rows, list) or any(not isinstance(r, dict) for r in rows):
        raise ValueError(f"activity database {path} must return a list of objects")
    return rows


def _is_id(value):
    return isinstance(value, int) and not isinstance(value, bool) and value >= 1


def format_price(value):
    return f"{float(value):.2f}" if isinstance(value, (int, float)) and not isinstance(value, bool) else "unknown"


def load_chunks():
    """tier_1 activity, assignment and summary chunks from the activity DB API.
    Raises requests.RequestException / ValueError when the API is unusable."""
    # Only real rows become evidence: an id and a name, as the destination loader requires.
    activities = [a for a in _get_list("/activities") if _is_id(a.get("activity_id"))
                  and isinstance(a.get("activity_name"), str) and a["activity_name"].strip()]
    assignments = [s for s in _get_list("/assignments") if _is_id(s.get("assignment_id"))
                   and _is_id(s.get("activity_id"))]
    names = {a["activity_id"]: a.get("activity_name") for a in activities}

    chunks = [
        chunk(
            f"activity_{a['activity_id']}", ACTIVITIES_SOURCE, "tier_1",
            f"Activity record: activity_id={a['activity_id']}, name={a.get('activity_name')}, "
            f"category={a.get('activity_type')}, price={format_price(a.get('activity_cost'))}, "
            f"duration={a.get('duration')}.",
            {"source_type": "activity_record", "activity_id": a["activity_id"],
             "name": a.get("activity_name"), "category": a.get("activity_type")},
        )
        for a in activities
    ]
    chunks += [
        chunk(
            f"assignment_{s['assignment_id']}", ASSIGNMENTS_SOURCE, "tier_1",
            f"Assignment record: assignment_id={s['assignment_id']}, activity_id={s.get('activity_id')}, "
            f"activity_name={names.get(s.get('activity_id'))}, scheduled_time={s.get('assignment_time')}.",
            {"source_type": "assignment_record", "activity_id": s.get("activity_id")},
        )
        for s in assignments
    ]
    return chunks + summary_chunks(activities)


def summary_chunks(activities):
    by_category = {}
    for a in activities:
        by_category.setdefault(a.get("activity_type") or "Uncategorised", []).append(a)

    counts = ", ".join(f"{cat}={len(items)}" for cat, items in sorted(by_category.items()))
    chunks = [
        chunk("activity_count", ACTIVITIES_SOURCE, "tier_1",
              f"Activity summary: there are {len(activities)} activities in total in the activities database.",
              {"source_type": "summary", "metric": "total_count", "value": len(activities)}),
        chunk("activity_category_counts", ACTIVITIES_SOURCE, "tier_1",
              f"Activity summary by category: {counts}.",
              {"source_type": "summary", "metric": "count_per_category"}),
    ]
    for category, items in sorted(by_category.items()):
        listed = ", ".join(f"{a.get('activity_name')} (activity_id={a['activity_id']})" for a in items)
        chunks.append(chunk(
            f"activity_category_{slug(category)}", ACTIVITIES_SOURCE, "tier_1",
            f"Activity category summary: category={category} has {len(items)} activities: {listed}.",
            {"source_type": "summary", "metric": "category", "category": category, "value": len(items)},
        ))
    return chunks


# ---------------------------------------------------------------- tier_2 / tier_3

def load_doc_chunks(repo_root):
    """tier_2 chunks from activity-service/docs plus a tier_3 activity file index."""
    docs_dir = Path(repo_root) / DOCS_SUBDIR
    chunks = []
    if docs_dir.is_dir():
        paths = sorted(
            p for p in docs_dir.rglob("*")
            if p.suffix in (".md", ".json") and p.is_file() and not p.is_symlink()
            and not p.name.startswith(GENERATED_REPORT_PREFIX) and p.name not in RUN_LOG_DOCS
        )
        for path in paths:
            rel = path.relative_to(repo_root).as_posix()
            words = path.read_text(encoding="utf-8", errors="ignore").split()
            for i in range(0, len(words), DOC_CHUNK_WORDS):
                part = i // DOC_CHUNK_WORDS + 1
                chunks.append(chunk(f"activity_doc_{slug(path.stem)}_{part}", rel, "tier_2",
                                    " ".join(words[i:i + DOC_CHUNK_WORDS]),
                                    {"source_type": "doc", "file": rel, "part": part}))

    service_root = Path(repo_root) / "activity-service"
    files = []
    for root, dirs, filenames in os.walk(service_root, followlinks=False):
        dirs[:] = sorted(d for d in dirs if d not in IGNORED_DIRS)
        files += [(Path(root) / f).relative_to(service_root).as_posix() for f in sorted(filenames)]
    if files:
        chunks.append(chunk("activity_repo_index", "repository:activity-service", "tier_3",
                            "Repository file index for activity-service: " + ", ".join(files),
                            {"source_type": "repository", "file_count": len(files)}))
    return chunks


# ---------------------------------------------------------------- retrieval helpers

def validate(query, k):
    if not isinstance(query, str) or not query.strip():
        raise ValueError("query is required and must be a non-empty string")
    if len(query) > MAX_QUERY_CHARS:
        raise ValueError(f"query must be at most {MAX_QUERY_CHARS} characters")
    if isinstance(k, bool) or not isinstance(k, int) or not 1 <= k <= MAX_K:
        raise ValueError(f"k must be an integer between 1 and {MAX_K}")
    return query.strip()


def select(chunks):
    return [c for c in chunks if (c.get("metadata") or {}).get("service") == SERVICE]


def _records(chunks):
    return [c for c in chunks if c["metadata"].get("source_type") == "activity_record"]


def mentioned_category(query, records):
    tokens = set(tokenize(query))
    categories = {r["metadata"].get("category") for r in records if r["metadata"].get("category")}
    return next((c for c in sorted(categories) if set(tokenize(c)) <= tokens), None)


def deterministic_answer(chunks, query):
    """Exact answers from the full tier_1 record set, not from top-k
    similarity. Returns (answer, cited_chunks) or None when no pattern applies."""
    text = query.lower()
    if "activit" not in text:
        return None
    records = _records(chunks)
    category = mentioned_category(query, records)
    if category:
        matches = [r for r in records if r["metadata"].get("category") == category]
        names = ", ".join(f"{r['metadata']['name']} (activity_id={r['metadata']['activity_id']})" for r in matches)
        return f"There are {len(matches)} {category} activities: {names}.", matches
    if re.search(r"\bhow many\b|\bnumber of\b|\btotal\b|\bcount\b", text):
        summary = [c for c in chunks if c["chunk_id"] == "activity_count"]
        return f"There are {len(records)} activities in total.", summary or records
    return None
