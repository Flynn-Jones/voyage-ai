"""Activity's slice of the shared RAG corpus (scope="activity").

Ported from activity-service/tests/test_rag_pipeline.py when activity-service's
own RAG server was retired. Everything runs against tmp_path (corpus, audit,
Chroma, activity docs); the feature database APIs and Ollama are faked.
"""
import json
import sys
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import rag_http_server  # noqa: E402
import rag_pipeline as rp  # noqa: E402

ACTIVITIES = [
    {"activity_id": 1, "activity_name": "Harbour Kayaking Tour", "activity_type": "Adventure",
     "activity_cost": 45.0, "duration": "2 hours"},
    {"activity_id": 2, "activity_name": "Aquarium Behind-the-Scenes", "activity_type": "Sightseeing",
     "activity_cost": 38.0, "duration": "3 hours"},
    {"activity_id": 3, "activity_name": "Street Food Night Walk", "activity_type": "Food",
     "activity_cost": 60.0, "duration": "3 hours"},
    {"activity_id": 4, "activity_name": "Old Town Cycling Tour", "activity_type": "Adventure",
     "activity_cost": 40.0, "duration": "half day"},
]
ASSIGNMENTS = [{"assignment_id": 1, "activity_id": 1, "assignment_time": "2026-10-02 09:00"},
               {"assignment_id": 2, "activity_id": 3, "assignment_time": "2026-10-03 18:30"}]
DESTINATIONS = [
    {"destination_id": 5, "city": "Bangkok", "country": "Thailand",
     "description": "Bustling capital known for street food, temples and river life.",
     "average_daily_cost": 70.0, "recommended_trip_length": 5, "travel_style": "Budget",
     "categories": ["food", "culture", "budget"]},
]
EXPENSES = [{"id": 1, "trip_reference": "Lisbon", "expense": "Kayak hire", "category": "activity",
             "estimated_cost": 45, "actual_cost": 50, "status": "paid"}]
ACTIVITY_CHUNK_PREFIXES = ("activity_", "assignment_")


class FakeResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code, self._payload = status_code, payload
        self.text = json.dumps(payload)

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.exceptions.HTTPError(f"{self.status_code} error")


@pytest.fixture
def env(tmp_path, monkeypatch):
    reports = tmp_path / "activity-service" / "docs" / "reports"
    reports.mkdir(parents=True)
    (reports / "tool-review.md").write_text(
        "# Tool review\nThe MCP tool review found that every read-only tool returns structured JSON errors "
        "and never raises exceptions to the caller.", encoding="utf-8")
    (reports / "rag-report.md").write_text("Generated report: there are 99 activities.", encoding="utf-8")
    (reports / "run-report.md").write_text("Run log: Harbour Kayaking Tour costs 45.00.", encoding="utf-8")
    (reports.parent / "test-results.md").write_text("Test log: 131 passed.", encoding="utf-8")
    monkeypatch.setattr(rp, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(rp, "DOC_SOURCES", [])
    monkeypatch.setattr(rp, "CORPUS_PATH", tmp_path / "corpus" / "corpus.jsonl")
    monkeypatch.setattr(rp, "AUDIT_PATH", tmp_path / "audit.jsonl")
    monkeypatch.setattr(rp, "CHROMA_PATH", tmp_path / "chroma")
    monkeypatch.setattr(rp, "_collection", None)
    monkeypatch.setattr(rp, "_last_corpus_chunks", [])
    monkeypatch.setattr(rp, "load_repository_chunks", lambda: [])
    state = {"activity_up": True, "ollama": [], "ollama_calls": []}
    activity_api = {"/activities": ACTIVITIES, "/assignments": ASSIGNMENTS}

    def fake_get(url, params=None, timeout=None):
        if url.startswith(rp.ACTIVITY_DB_URL):
            path = url[len(rp.ACTIVITY_DB_URL):]
            if state["activity_up"] and path in activity_api:
                return FakeResponse(200, activity_api[path])
        elif url.endswith("/destinations"):
            return FakeResponse(200, DESTINATIONS)
        elif url.endswith("/expenses"):
            return FakeResponse(200, EXPENSES)
        raise requests.exceptions.ConnectionError(f"refused: {url}")

    def fake_post(url, json=None, timeout=None):
        state["ollama_calls"].append(json)
        reply = state["ollama"].pop(0) if state["ollama"] else requests.exceptions.ConnectionError("ollama down")
        if isinstance(reply, Exception):
            raise reply
        return FakeResponse(200, {"response": reply})

    monkeypatch.setattr(rp.requests, "get", fake_get)
    monkeypatch.setattr(rp.requests, "post", fake_post)
    return state


def corpus_by_id():
    return {c["chunk_id"]: c for c in rp.read_corpus()}


# ---------------------------------------------------------------- refresh / corpus

def test_refresh_adds_activity_chunks_to_the_one_shared_corpus(env):
    result = rp.refresh_corpus()

    assert result["status"] == "success" and result["vector_store_status"] == "ready"
    assert result["source_status"]["activity_db"] == "ok"
    # 4 activities + 2 assignments + total + per-category counts + 3 categories, 1 doc, 1 file index
    assert result["activity_chunk_count"] == 4 + 2 + 2 + 3 + 1 + 1
    assert result["service_counts"]["activity"] == result["activity_chunk_count"]
    assert {"destination", "budget"} <= set(result["service_counts"])
    assert sum(result["tier_counts"].values()) == result["chunk_count"]
    assert rp.get_collection().count() == result["chunk_count"]


def test_every_chunk_is_tagged_with_a_service_in_corpus_and_chroma(env):
    rp.refresh_corpus()
    chunks = rp.read_corpus()
    assert all(c["metadata"].get("service") for c in chunks)
    services = {c["chunk_id"]: c["metadata"]["service"] for c in chunks}
    assert services["activity_1"] == "activity" and services["assignment_2"] == "activity"
    assert services["destination_5"] == "destination" and services["budget_expense_1"] == "budget"

    stored = rp.get_collection().get(ids=["activity_1", "destination_5"], include=["metadatas"])
    assert {m["service"] for m in stored["metadatas"]} == {"activity", "destination"}


def test_record_text_and_ids_match_the_activity_pipeline(env):
    rp.refresh_corpus()
    chunks = corpus_by_id()
    assert chunks["activity_1"]["text"] == (
        "Activity record: activity_id=1, name=Harbour Kayaking Tour, category=Adventure, "
        "price=45.00, duration=2 hours."
    )
    assert chunks["assignment_2"]["text"] == (
        "Assignment record: assignment_id=2, activity_id=3, activity_name=Street Food Night Walk, "
        "scheduled_time=2026-10-03 18:30."
    )
    assert "there are 4 activities" in chunks["activity_count"]["text"]
    assert "Adventure=2" in chunks["activity_category_counts"]["text"]
    assert chunks["activity_1"]["source_id"] == "activity-db:/activities"
    assert chunks["activity_1"]["authority_tier"] == "tier_1"


def test_activity_docs_are_tier_2_and_generated_rag_reports_are_excluded(env):
    rp.refresh_corpus()
    sources = {c["source_id"]: c for c in rp.read_corpus()}
    assert "activity-service/docs/reports/tool-review.md" in sources
    assert sources["activity-service/docs/reports/tool-review.md"]["authority_tier"] == "tier_2"
    assert "activity-service/docs/reports/rag-report.md" not in sources
    # dated run logs quote sample records that may no longer exist
    assert "activity-service/docs/reports/run-report.md" not in sources
    assert "activity-service/docs/test-results.md" not in sources


def test_activity_db_down_degrades_refresh_not_fails_it(env):
    env["activity_up"] = False
    result = rp.refresh_corpus()
    assert result["status"] == "success"
    assert result["source_status"]["activity_db"].startswith("unavailable")
    assert "activity_1" not in corpus_by_id()
    assert result["source_status"]["destination_db"] == "ok"


# ---------------------------------------------------------------- unscoped callers unaffected

@pytest.mark.parametrize("query", ["street food", "kayak hire expense", "Bangkok temples", "tour adventure"])
def test_unscoped_results_are_identical_with_or_without_activity_data(env, monkeypatch, query):
    # Budget / Accommodation / Destination send unscoped queries; adding Activity's
    # slice to the shared corpus must not change a single result they get.
    real_docs = rp.load_activity_doc_chunks
    env["activity_up"] = False
    monkeypatch.setattr(rp, "load_activity_doc_chunks", lambda root: [])
    rp.refresh_corpus()
    assert rp.refresh_corpus()["activity_chunk_count"] == 0
    before = rp.retrieve_context(query, 5)["results"]

    env["activity_up"] = True
    monkeypatch.setattr(rp, "load_activity_doc_chunks", real_docs)
    assert rp.refresh_corpus()["activity_chunk_count"] > 0
    after = rp.retrieve_context(query, 5)["results"]

    strip = lambda rows: [(r["chunk_id"], r["relevance_score"], r["matched_terms"]) for r in rows]  # noqa: E731
    assert strip(after) == strip(before)


def test_unscoped_retrieval_never_returns_activity_chunks(env):
    rp.refresh_corpus()
    for query in ("street food night walk", "harbour kayaking tour", "how many activities", "adventure"):
        ids = [r["chunk_id"] for r in rp.retrieve_context(query, 10)["results"]]
        assert not [i for i in ids if i.startswith(ACTIVITY_CHUNK_PREFIXES) or i.startswith("activity_doc")], ids


# ---------------------------------------------------------------- scoped retrieve

def test_scoped_retrieve_returns_only_activity_chunks_with_numeric_distances(env):
    rp.refresh_corpus()
    result = rp.retrieve_context("harbour kayaking tour", k=3, scope="activity")

    assert result["status"] == "success" and result["scope"] == "activity"
    assert result["retrieval_mode"] == "hybrid"
    assert 0 < len(result["results"]) <= 3
    assert result["results"][0]["chunk_id"] == "activity_1"
    stored = corpus_by_id()
    for i, row in enumerate(result["results"], start=1):
        assert row["rank"] == i
        assert isinstance(row["distance"], float)
        assert stored[row["chunk_id"]]["metadata"]["service"] == "activity"


def test_scoped_retrieve_excludes_other_features_even_on_shared_terms(env):
    rp.refresh_corpus()
    ids = [r["chunk_id"] for r in rp.retrieve_context("street food", k=10, scope="activity")["results"]]
    assert "activity_3" in ids
    assert "destination_5" not in ids


def test_scoped_retrieve_auto_refreshes_empty_corpus(env):
    result = rp.retrieve_context("street food", k=2, scope="activity")
    assert result["status"] == "success"
    assert rp.CORPUS_PATH.exists()


def test_scoped_retrieve_lexical_fallback_still_has_distances(env, monkeypatch):
    rp.refresh_corpus()

    def broken(*args, **kwargs):
        raise RuntimeError("chroma down")
    monkeypatch.setattr(rp, "_chroma_distances", broken)

    result = rp.retrieve_context("street food night walk", k=3, scope="activity")
    assert result["retrieval_mode"] == "lexical_fallback"
    assert result["results"][0]["chunk_id"] == "activity_3"
    assert result["results"][0]["distance"] == 0.0
    assert all(isinstance(r["distance"], float) for r in result["results"])


@pytest.mark.parametrize("query, k", [("", 5), ("   ", 5), (None, 5), ("food", 0), ("food", 21), ("food", "5"),
                                      ("food", True), ("x" * 1001, 5)])
def test_scoped_invalid_input_is_structured_invalid_input(env, query, k):
    for fn in (rp.retrieve_context, rp.answer_question):
        result = fn(query, k, scope="activity")
        assert result["status"] == "error" and result["error_type"] == "invalid_input"


# ---------------------------------------------------------------- scoped answer

def test_count_question_is_deterministic_from_the_full_record_set(env):
    rp.refresh_corpus()
    result = rp.answer_question("How many activities are there?", scope="activity")

    assert result["status"] == "success"
    assert result["answer_source"] == "deterministic"
    assert result["answer"] == f"There are {len(ACTIVITIES)} activities in total."
    assert result["citations"] == [{"chunk_id": "activity_count", "source_id": "activity-db:/activities",
                                    "authority_tier": "tier_1"}]
    assert result["confidence_category"] == "High"
    assert env["ollama_calls"] == []


def test_category_question_lists_every_matching_record(env):
    rp.refresh_corpus()
    result = rp.answer_question("Which Adventure activities are available?", k=1, scope="activity")

    assert result["answer_source"] == "deterministic"
    assert result["answer"].startswith("There are 2 Adventure activities:")
    assert [c["chunk_id"] for c in result["citations"]] == ["activity_1", "activity_4"]


def test_llm_answer_is_grounded_in_activity_chunks_only(env):
    rp.refresh_corpus()
    env["ollama"] = ["Answer: The Harbour Kayaking Tour costs 45.00."]

    result = rp.answer_question("What does the harbour kayaking tour cost?", scope="activity")

    assert result["status"] == "success" and result["answer_source"] == "llm"
    assert result["answer"] == "The Harbour Kayaking Tour costs 45.00."
    # assignment_1 names the same tour, so it ties with activity_1 on relevance
    assert "activity_1" in [c["chunk_id"] for c in result["citations"]]
    assert result["confidence_category"] in ("High", "Medium", "Low")
    prompt = env["ollama_calls"][0]["prompt"]
    assert "[activity_1]" in prompt and "destination_" not in prompt and "budget_" not in prompt
    assert env["ollama_calls"][0]["model"] == rp.OLLAMA_MODEL


def test_model_decline_is_insufficient_with_no_citations(env):
    rp.refresh_corpus()
    env["ollama"] = ["Insufficient evidence."]

    result = rp.answer_question("What does the harbour kayaking tour include?", scope="activity")

    assert result["status"] == "insufficient_context"
    assert result["confidence_category"] == "Insufficient"
    assert result["citations"] == []


def test_irrelevant_question_refuses_without_calling_model(env):
    rp.refresh_corpus()
    result = rp.answer_question("zebra quantum volcano?", scope="activity")

    assert result["status"] == "insufficient_context"
    assert result["answer"] == rp.INSUFFICIENT_ANSWER
    assert result["answer_source"] == "retrieval_guard"
    assert env["ollama_calls"] == []


def test_ollama_unavailable_is_structured_llm_unavailable(env):
    rp.refresh_corpus()  # env["ollama"] is empty, so the fake raises ConnectionError
    result = rp.answer_question("What does the harbour kayaking tour cost?", scope="activity")

    assert result["status"] == "error" and result["error_type"] == "llm_unavailable"
    assert result["retrieval_summary"]["retrieved_count"] > 0


def test_scoped_calls_are_audited(env):
    rp.refresh_corpus()
    rp.retrieve_context("street food", 2, caller="test", scope="activity")
    rp.answer_question("How many activities are there?", caller="test", scope="activity")
    rp.retrieve_context("", 2, caller="test", scope="activity")

    records = [json.loads(line) for line in rp.AUDIT_PATH.read_text(encoding="utf-8").splitlines()]
    scoped = [r for r in records if r["tool_input"].get("scope") == "activity"]
    assert [r["tool_name"] for r in scoped] == ["retrieve_context", "answer_question", "retrieve_context"]
    assert scoped[1]["outcome"] == "deterministic_answer"
    assert scoped[-1]["validation_status"] == "fail" and scoped[-1]["outcome"] == "invalid_input"
    assert "text" not in json.dumps(scoped[0]["tool_output"])


# ---------------------------------------------------------------- HTTP (:7002 contract)

@pytest.fixture
def http(env):
    server = ThreadingHTTPServer(("127.0.0.1", 0), rag_http_server.RAGHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def post(path, body):
        request = urllib.request.Request(f"http://127.0.0.1:{server.server_port}{path}",
                                         data=json.dumps(body).encode(), method="POST",
                                         headers={"Content-Type": "application/json"})
        try:
            # S310: fixed http://127.0.0.1 URL to the in-process test server, no user-controlled scheme.
            with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())

    yield post
    server.shutdown()


def test_http_activity_scope_status_codes(http, env):
    status, body = http("/refresh", {"caller": "backend"})
    assert status == 200 and body["service_counts"]["activity"] > 0 and "tier_counts" in body

    status, body = http("/answer", {"query": "How many activities are there?", "k": 5,
                                    "caller": "backend", "scope": "activity"})
    assert status == 200 and body["answer_source"] == "deterministic"

    status, body = http("/retrieve", {"query": "kayaking", "k": 3, "caller": "backend", "scope": "activity"})
    assert status == 200 and {r["chunk_id"] for r in body["results"]} >= {"activity_1", "assignment_1"}

    status, body = http("/answer", {"query": "zebra quantum", "k": 5, "scope": "activity"})
    assert status == 200 and body["confidence_category"] == "Insufficient"

    status, body = http("/retrieve", {"query": "kayaking", "k": 0, "scope": "activity"})
    assert status == 400 and body["error_type"] == "invalid_input"

    status, body = http("/answer", {"query": "What does the harbour kayaking tour cost?", "scope": "activity"})
    assert status == 503 and body["error_type"] == "llm_unavailable"


def test_rows_without_a_real_id_and_name_are_not_indexed(env, monkeypatch):
    junk = {"/activities": [{"activity_id": 1}, {"activity_id": True, "activity_name": "x"},
                            {"activity_id": 2, "activity_name": "  "}, ACTIVITIES[0]],
            "/assignments": [{"assignment_id": 7}, {"assignment_id": "3", "activity_id": 1}, ASSIGNMENTS[0]]}

    def fake_get(url, params=None, timeout=None):
        path = url[len(rp.ACTIVITY_DB_URL):]
        if path in junk:
            return FakeResponse(200, junk[path])
        raise requests.exceptions.ConnectionError(url)
    monkeypatch.setattr(rp.requests, "get", fake_get)

    ids = {c["chunk_id"] for c in rp.load_activity_records()}
    assert {"activity_1", "assignment_1"} <= ids
    assert "activity_2" not in ids and "assignment_7" not in ids and "assignment_3" not in ids


def test_one_category_term_is_enough_in_activity_scope(env):
    # "food walk": one shared term per record, below the 2-term rule; "food" is a category.
    rp.refresh_corpus()
    ids = [r["chunk_id"] for r in rp.retrieve_context("food walk", k=5, scope="activity")["results"]]
    assert "activity_3" in ids
    # Unscoped acceptance is unchanged: categories are not entities there.
    assert rp.rank_accepted("food walk", rp._corpus_chunks(), {}, 5)[0] == []


@pytest.mark.parametrize("reply", ["Answer:\nInsufficient evidence.", "Answer: Insufficient evidence"])
def test_scaffolded_abstention_is_insufficient_in_both_paths(env, reply):
    rp.refresh_corpus()
    env["ollama"] = [reply]
    scoped = rp.answer_question("What does the harbour kayaking tour cost?", scope="activity")
    assert scoped["status"] == "insufficient_context" and scoped["citations"] == []

    env["ollama"] = [reply]
    unscoped = rp.answer_question("Bangkok street food temples")
    assert unscoped["status"] == "insufficient_context"
    assert unscoped["confidence_category"] == "Insufficient" and unscoped["citations"] == []
