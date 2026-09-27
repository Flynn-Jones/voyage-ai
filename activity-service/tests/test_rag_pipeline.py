"""Tests for activity-service's RAG pipeline, its HTTP/MCP wrappers, and the backend's RAG routes.

Every test builds its own SQLite database, docs folder, corpus file, audit log,
and Chroma directory under tmp_path, so the real data and index are never
touched. Ollama is always mocked (requests.post), never called.

Run from activity-service/ with: python -m pytest tests/test_rag_pipeline.py
"""
import asyncio
import json
import sqlite3
import sys
from pathlib import Path

import pytest
import requests

pytest.importorskip("chromadb")

ACTIVITY_SERVICE_ROOT = Path(__file__).resolve().parent.parent
for path in (ACTIVITY_SERVICE_ROOT / "rag-server", ACTIVITY_SERVICE_ROOT / "backend"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import rag_http_server  # noqa: E402
import rag_pipeline  # noqa: E402

ACTIVITIES = [
    (1, "Harbour Kayaking Tour", "Adventure", 45.0, "2 hours"),
    (2, "Aquarium Behind-the-Scenes", "Sightseeing", 38.0, "3 hours"),
    (3, "Street Food Night Walk", "Food", 60.0, "3 hours"),
    (4, "Old Town Cycling Tour", "Adventure", 40.0, "half day"),
]
ASSIGNMENTS = [(1, 1, "2026-10-02 09:00"), (2, 3, "2026-10-03 18:30")]
AUDIT_FIELDS = {"request_id", "trace_id", "tool_name", "tool_input", "tool_output",
                "timestamp", "duration_ms", "validation_status", "outcome"}


class DummyResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload
        self.text = json.dumps(payload)

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.exceptions.HTTPError(f"{self.status_code} error")


def create_db(path, activities=ACTIVITIES, assignments=ASSIGNMENTS):
    conn = sqlite3.connect(path)
    conn.executescript(
        "CREATE TABLE activities (activity_id INTEGER PRIMARY KEY, activity_name TEXT, activity_type TEXT,"
        " activity_cost REAL, duration TEXT);"
        "CREATE TABLE activities_assignment (assignment_id INTEGER PRIMARY KEY, activity_id INTEGER,"
        " assignment_time TEXT);"
    )
    conn.executemany("INSERT INTO activities VALUES (?, ?, ?, ?, ?)", activities)
    conn.executemany("INSERT INTO activities_assignment VALUES (?, ?, ?)", assignments)
    conn.commit()
    conn.close()


@pytest.fixture
def rag(tmp_path, monkeypatch):
    """rag_pipeline pointed entirely at tmp_path, with network access disabled."""
    create_db(tmp_path / "activity.sqlite")
    docs = tmp_path / "docs" / "reports"
    docs.mkdir(parents=True)
    (docs / "tool-review.md").write_text(
        "# Tool review\nThe MCP tool review found that every read-only tool returns structured JSON errors "
        "and never raises exceptions to the caller.", encoding="utf-8")
    (docs / "rag-report.md").write_text("Generated report: there are 99 activities.", encoding="utf-8")

    monkeypatch.setattr(rag_pipeline, "SERVICE_ROOT", tmp_path)
    monkeypatch.setattr(rag_pipeline, "DOCS_DIR", tmp_path / "docs")
    monkeypatch.setattr(rag_pipeline, "ACTIVITY_DB_PATH", tmp_path / "activity.sqlite")
    monkeypatch.setattr(rag_pipeline, "CORPUS_PATH", tmp_path / "corpus" / "corpus.jsonl")
    monkeypatch.setattr(rag_pipeline, "AUDIT_PATH", tmp_path / "rag-audit.jsonl")
    monkeypatch.setattr(rag_pipeline, "CHROMA_PATH", tmp_path / "chroma")
    monkeypatch.setattr(rag_pipeline, "EMBEDDING_MODE", "hash")

    def no_network(*args, **kwargs):
        raise requests.exceptions.ConnectionError("network disabled in tests")

    monkeypatch.setattr(rag_pipeline.requests, "get", no_network)
    monkeypatch.setattr(rag_pipeline.requests, "post", no_network)
    return rag_pipeline


def audit_records(rag):
    return [json.loads(line) for line in rag.AUDIT_PATH.read_text(encoding="utf-8").splitlines()]


def llm_path(rag, monkeypatch):
    """Widen the relevance cut so these tests exercise the LLM step itself,
    independent of how well the embedding scores the test query."""
    monkeypatch.setitem(rag.DISTANCE_THRESHOLDS, "hash", (0.35, 0.95))


def fake_ollama(text, calls=None):
    def fake_post(url, json=None, timeout=None):
        if calls is not None:
            calls.append(json)
        return DummyResponse(200, {"response": text})
    return fake_post


# ---------------------------------------------------------------- REFRESH

def test_refresh_builds_corpus_and_index(rag):
    result = rag.refresh_corpus()

    assert result["status"] == "success"
    assert result["chunk_count"] > 0
    assert result["vector_store_status"] == "ready"
    assert result["embedding_mode"] == "hash"
    assert result["tier_1_source"] == "sqlite:activity.sqlite"
    assert result["tier_counts"]["tier_1"] > 0 and result["tier_counts"]["tier_3"] == 1
    lines = rag.CORPUS_PATH.read_text(encoding="utf-8").splitlines()
    assert len(lines) == result["chunk_count"]
    for chunk in map(json.loads, lines):
        assert {"chunk_id", "source_id", "authority_tier", "text", "metadata", "indexed_at"} <= set(chunk)
    assert rag.get_collection().count() == result["chunk_count"]


def test_refresh_record_text_uses_real_schema(rag):
    rag.refresh_corpus()
    chunks = {c["chunk_id"]: c for c in rag.read_corpus()}
    assert chunks["activity_1"]["text"] == (
        "Activity record: activity_id=1, name=Harbour Kayaking Tour, category=Adventure, "
        "price=45.00, duration=2 hours."
    )
    assert "there are 4 activities" in chunks["activity_count"]["text"]
    assert "Adventure=2" in chunks["activity_category_counts"]["text"]


def test_refresh_excludes_generated_rag_reports(rag):
    rag.refresh_corpus()
    sources = {c["source_id"] for c in rag.read_corpus()}
    assert "docs/reports/tool-review.md" in sources
    assert "docs/reports/rag-report.md" not in sources


def test_refresh_falls_back_to_http_when_sqlite_empty(rag, tmp_path, monkeypatch):
    create_db(tmp_path / "empty.sqlite", activities=[], assignments=[])
    monkeypatch.setattr(rag, "ACTIVITY_DB_PATH", tmp_path / "empty.sqlite")
    api = {"/activities": [{"activity_id": 7, "activity_name": "Jazz Night", "activity_type": "Nightlife",
                            "activity_cost": 30.0, "duration": "3 hours"}], "/assignments": []}
    monkeypatch.setattr(rag.requests, "get",
                        lambda url, timeout=None: DummyResponse(200, api[url.replace(rag.ACTIVITY_DB_URL, "")]))

    result = rag.refresh_corpus()

    assert result["tier_1_source"].startswith("database-service:")
    assert "activity_7" in {c["chunk_id"] for c in rag.read_corpus()}


def test_refresh_is_degraded_not_failed_when_chroma_breaks(rag, monkeypatch):
    def broken():
        raise RuntimeError("chroma exploded")
    monkeypatch.setattr(rag, "chroma_client", broken)

    result = rag.refresh_corpus()

    assert result["status"] == "success"
    assert result["vector_store_status"] == "degraded"
    assert "chroma exploded" in result["vector_store_error"]


def test_refresh_reports_structured_error_when_no_data_source(rag, tmp_path, monkeypatch):
    monkeypatch.setattr(rag, "ACTIVITY_DB_PATH", tmp_path / "missing.sqlite")

    result = rag.refresh_corpus()

    assert result["status"] == "error"
    assert result["error_type"] == "tool_error"
    assert "no activity data source reachable" in result["error"]


def test_ollama_embedding_failure_falls_back_to_hash(rag, monkeypatch):
    monkeypatch.setattr(rag, "EMBEDDING_MODE", "ollama")

    result = rag.refresh_corpus()

    assert result["embedding_mode"] == "hash"
    assert "ollama embeddings unavailable" in result["embedding_fallback"]
    assert rag.get_collection().metadata["embedding_mode"] == "hash"


# ---------------------------------------------------------------- RETRIEVE

def test_retrieve_returns_at_most_k_ranked_results(rag):
    rag.refresh_corpus()

    result = rag.retrieve_context("harbour kayaking tour", k=3)

    assert result["status"] == "success"
    assert result["retrieval_mode"] == "vector"
    assert 0 < len(result["results"]) <= 3
    for i, row in enumerate(result["results"], start=1):
        assert {"rank", "chunk_id", "source_id", "authority_tier", "distance", "text"} <= set(row)
        assert row["rank"] == i
    distances = [row["distance"] for row in result["results"]]
    assert distances == sorted(distances)
    assert "activity_1" in [row["chunk_id"] for row in result["results"]]


def test_retrieve_auto_refreshes_empty_index(rag):
    result = rag.retrieve_context("street food", k=2)

    assert result["status"] == "success"
    assert result["retrieval_mode"] == "vector"
    assert rag.CORPUS_PATH.exists()


@pytest.mark.parametrize("query, k", [("", 5), ("   ", 5), (None, 5), ("food", 0), ("food", 21), ("food", "5"),
                                      ("food", True)])
def test_retrieve_rejects_invalid_input(rag, query, k):
    result = rag.retrieve_context(query, k=k)

    assert result["status"] == "error"
    assert result["error_type"] == "invalid_input"


def test_retrieve_uses_lexical_fallback_when_chroma_unavailable(rag, monkeypatch):
    rag.refresh_corpus()

    def broken():
        raise RuntimeError("chroma down")
    monkeypatch.setattr(rag, "chroma_client", broken)

    result = rag.retrieve_context("street food night walk", k=3)

    assert result["retrieval_mode"] == "lexical_fallback"
    assert result["embedding_mode"] is None
    assert result["results"][0]["chunk_id"] == "activity_3"


def test_ranking_uses_tier_only_as_tie_breaker():
    rows = [
        {"chunk_id": "weak_tier_1", "authority_tier": "tier_1", "distance": 0.8},
        {"chunk_id": "strong_tier_2", "authority_tier": "tier_2", "distance": 0.2},
        {"chunk_id": "tied_tier_2", "authority_tier": "tier_2", "distance": 0.5},
        {"chunk_id": "tied_tier_1", "authority_tier": "tier_1", "distance": 0.5},
    ]
    ranked = rag_pipeline.rank(rows, k=4)
    assert [r["chunk_id"] for r in ranked] == ["strong_tier_2", "tied_tier_1", "tied_tier_2", "weak_tier_1"]


# ---------------------------------------------------------------- ANSWER

def test_count_question_is_deterministic_and_matches_db(rag, tmp_path):
    rag.refresh_corpus()

    result = rag.answer_question("How many activities are there?")

    conn = sqlite3.connect(tmp_path / "activity.sqlite")
    (db_count,) = conn.execute("SELECT COUNT(*) FROM activities").fetchone()
    conn.close()
    assert result["status"] == "success"
    assert result["answer_source"] == "deterministic"
    assert result["answer"] == f"There are {db_count} activities in total."
    assert result["citations"] == [{"chunk_id": "activity_count", "source_id": "sqlite:activity.sqlite",
                                    "authority_tier": "tier_1"}]
    assert result["confidence_category"] == "High"
    assert result["retrieval_summary"]["retrieved_count"] <= 5


def test_category_question_lists_matching_records(rag):
    rag.refresh_corpus()

    result = rag.answer_question("Which Adventure activities are available?")

    assert result["answer_source"] == "deterministic"
    assert result["answer"].startswith("There are 2 Adventure activities:")
    assert [c["chunk_id"] for c in result["citations"]] == ["activity_1", "activity_4"]


def test_llm_answer_is_grounded_with_cited_chunks(rag, monkeypatch):
    llm_path(rag, monkeypatch)
    rag.refresh_corpus()
    calls = []
    monkeypatch.setattr(rag.requests, "post", fake_ollama(
        "Answer: The Harbour Kayaking Tour costs 45.00.\nEvidence: [activity_1]", calls))

    result = rag.answer_question("What does the harbour kayaking tour cost?")

    assert result["status"] == "success"
    assert result["answer_source"] == "llm"
    assert result["answer"] == "The Harbour Kayaking Tour costs 45.00."
    assert result["citations"] == [{"chunk_id": "activity_1", "source_id": "sqlite:activity.sqlite",
                                    "authority_tier": "tier_1"}]
    assert result["confidence_category"] in ("High", "Medium", "Low")
    prompt = calls[0]["prompt"]
    assert "Use only the context below" in prompt and "Insufficient evidence" in prompt
    assert calls[0]["model"] == rag.OLLAMA_MODEL


def test_model_insufficient_evidence_gives_unknown_and_no_citations(rag, monkeypatch):
    llm_path(rag, monkeypatch)
    rag.refresh_corpus()
    monkeypatch.setattr(rag.requests, "post", fake_ollama("Insufficient evidence"))

    result = rag.answer_question("What does the harbour kayaking tour include?")

    assert result["answer"] == "Insufficient evidence"
    assert result["citations"] == []
    assert result["confidence_category"] == "Unknown"


def test_irrelevant_question_refuses_without_calling_model(rag, monkeypatch):
    rag.refresh_corpus()
    calls = []
    monkeypatch.setattr(rag.requests, "post", fake_ollama("Answer: made up", calls))

    result = rag.answer_question("zebra quantum volcano?")

    assert result["answer"] == "Insufficient evidence"
    assert result["confidence_category"] == "Unknown"
    assert calls == []


def test_answer_uses_thresholds_of_the_index_embedding_mode(rag, monkeypatch):
    monkeypatch.setattr(rag, "EMBEDDING_MODE", "ollama")
    monkeypatch.setattr(rag, "ollama_embed", rag.hash_embed)  # stand-in vectors, no Ollama needed
    monkeypatch.setitem(rag.DISTANCE_THRESHOLDS, "hash", (0.35, 0.95))
    monkeypatch.setitem(rag.DISTANCE_THRESHOLDS, "ollama", (0.0, 0.0))
    calls = []
    monkeypatch.setattr(rag.requests, "post", fake_ollama("Answer: made up", calls))
    assert rag.refresh_corpus()["embedding_mode"] == "ollama"

    result = rag.answer_question("What does the harbour kayaking tour cost?")

    assert result["retrieval_summary"]["embedding_mode"] == "ollama"
    assert result["answer"] == "Insufficient evidence"  # ollama's cut-off applied, not hash's
    assert calls == []


def test_ollama_unavailable_returns_structured_error(rag, monkeypatch):
    llm_path(rag, monkeypatch)
    rag.refresh_corpus()  # requests.post already raises ConnectionError in the fixture

    result = rag.answer_question("What does the harbour kayaking tour cost?")

    assert result["status"] == "error"
    assert result["error_type"] == "llm_unavailable"
    assert "LLM unavailable" in result["error"]
    assert result["retrieval_summary"]["retrieved_count"] > 0


def test_answer_propagates_invalid_input_as_structured_error(rag):
    result = rag.answer_question("", k=5)
    assert result["status"] == "error"
    assert result["error_type"] == "invalid_input"


@pytest.mark.parametrize("rows, expected", [
    ([], "Unknown"),
    ([{"authority_tier": "tier_1", "distance": 0.2}, {"authority_tier": "tier_1", "distance": 0.4}], "High"),
    ([{"authority_tier": "tier_1", "distance": 0.2}, {"authority_tier": "tier_2", "distance": 0.3}], "Medium"),
    ([{"authority_tier": "tier_1", "distance": 0.5}, {"authority_tier": "tier_1", "distance": 0.55}], "Medium"),
    ([{"authority_tier": "tier_3", "distance": 0.3}], "Low"),
    ([{"authority_tier": "tier_1", "distance": 0.9}, {"authority_tier": "tier_1", "distance": 0.95}], "Low"),
])
def test_confidence_needs_relevance_and_authority(rows, expected):
    assert rag_pipeline.confidence_category(rows, False, (0.35, 0.6)) == expected


# ---------------------------------------------------------------- audit

def test_every_tool_call_appends_valid_audit_json(rag):
    rag.refresh_corpus(caller="test")
    rag.retrieve_context("food", k=2, caller="test")
    rag.answer_question("How many activities are there?", caller="test")
    rag.retrieve_context("", k=2, caller="test")

    records = audit_records(rag)

    assert [r["tool_name"] for r in records] == [
        "refresh_corpus", "retrieve_context", "retrieve_context", "answer_question", "retrieve_context"]
    for record in records:
        assert AUDIT_FIELDS <= set(record)
    assert records[-1]["validation_status"] == "fail"
    assert records[-1]["outcome"] == "invalid_input"
    # answer_question's nested retrieval shares its trace; audit logs ids, not chunk text
    assert records[2]["trace_id"] == records[3]["trace_id"]
    assert "chunk_ids" in records[1]["tool_output"] and "results" not in records[1]["tool_output"]


# ---------------------------------------------------------------- HTTP + MCP wrappers

@pytest.mark.parametrize("result, expected", [
    ({"status": "success"}, 200),
    ({"status": "error", "error_type": "invalid_input"}, 400),
    ({"status": "error", "error_type": "tool_error"}, 500),
    ({"status": "error", "error_type": "llm_unavailable"}, 500),
])
def test_http_status_codes(result, expected):
    assert rag_http_server.status_code_for(result) == expected


def test_mcp_server_registers_exactly_the_three_tools():
    pytest.importorskip("mcp")
    import rag_server

    tools = asyncio.run(rag_server.mcp.list_tools())

    assert sorted(t.name for t in tools) == ["answer_question", "refresh_corpus", "retrieve_context"]
    assert rag_server.mcp.name == "Activity Manager RAG MCP"


# ---------------------------------------------------------------- backend routes

@pytest.fixture
def backend(monkeypatch):
    from services import rag_api

    forwarded = []

    def fake_post(url, json=None, timeout=None):
        forwarded.append((url, json))
        return DummyResponse(200, {"status": "success", "echo": json})

    monkeypatch.setattr(rag_api.requests, "post", fake_post)
    monkeypatch.setattr(rag_api, "RAG_ENABLED", True)

    import app as app_module
    with app_module.create_app().test_client() as client:
        yield client, forwarded, rag_api, monkeypatch


@pytest.mark.parametrize("path", ["/api/activity/rag/refresh", "/api/activity/rag/retrieve",
                                  "/api/activity/rag/answer"])
def test_backend_rag_routes_403_when_header_off(backend, path):
    client, forwarded, _, _ = backend

    response = client.post(path, json={"query": "food"}, headers={"X-RAG-Mode": "off"})

    assert response.status_code == 403
    assert response.get_json() == {"status": "error", "error": "RAG mode is disabled."}
    assert forwarded == []


def test_backend_rag_routes_403_when_disabled_by_env(backend):
    client, forwarded, rag_api, monkeypatch = backend
    monkeypatch.setattr(rag_api, "RAG_ENABLED", False)

    response = client.post("/api/activity/rag/answer", json={"query": "food"})

    assert response.status_code == 403
    assert forwarded == []


@pytest.mark.parametrize("body", [{}, {"query": ""}, {"query": "   "}, {"query": 5},
                                  {"query": "food", "k": 0}, {"query": "food", "k": "abc"}])
@pytest.mark.parametrize("path", ["/api/activity/rag/retrieve", "/api/activity/rag/answer"])
def test_backend_rag_routes_400_on_bad_input(backend, path, body):
    client, forwarded, _, _ = backend

    response = client.post(path, json=body)

    assert response.status_code == 400
    assert forwarded == []


def test_backend_forwards_validated_payload(backend):
    client, forwarded, rag_api, _ = backend

    response = client.post("/api/activity/rag/answer", json={"query": "  food tours ", "k": "3"})

    assert response.status_code == 200
    assert forwarded == [(f"{rag_api.RAG_SERVICE_URL}/answer", {"query": "food tours", "k": 3, "caller": "backend"})]


def test_backend_503_when_rag_server_unreachable(backend):
    client, _, rag_api, monkeypatch = backend

    def unreachable(url, json=None, timeout=None):
        raise requests.exceptions.ConnectionError("refused")
    monkeypatch.setattr(rag_api.requests, "post", unreachable)

    response = client.post("/api/activity/rag/refresh")

    assert response.status_code == 503
    assert "RAG server unavailable" in response.get_json()["error"]


def test_backend_maps_rag_tool_error_to_502(backend):
    client, _, rag_api, monkeypatch = backend
    monkeypatch.setattr(rag_api.requests, "post", lambda url, json=None, timeout=None: DummyResponse(
        500, {"status": "error", "error": "LLM unavailable", "error_type": "llm_unavailable"}))

    response = client.post("/api/activity/rag/answer", json={"query": "food"})

    assert response.status_code == 502
    assert response.get_json()["error_type"] == "llm_unavailable"
