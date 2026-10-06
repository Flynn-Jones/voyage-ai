"""Focused tests for the shared RAG repair (R1-P02).

Everything runs against tmp_path (corpus, audit, Chroma); Destination/Budget/
Accommodation HTTP and Ollama are faked. Run from ai-services/rag-server/:
    .venv/bin/python -m pytest tests
"""
import json
import sys
import threading
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import rag_http_server  # noqa: E402
import rag_pipeline as rp  # noqa: E402

DESTINATIONS = [
    {"destination_id": 1, "city": "Tokyo", "country": "Japan",
     "description": "Neon-lit metropolis mixing ultramodern tech with historic temples.",
     "average_daily_cost": 150.0, "recommended_trip_length": 6, "travel_style": "City break",
     "categories": ["food", "culture", "nightlife", "shopping"]},
    {"destination_id": 3, "city": "Osaka", "country": "Japan",
     "description": "Japan's kitchen, famous for street food and lively nightlife.",
     "average_daily_cost": 125.0, "recommended_trip_length": 3, "travel_style": "Food and nightlife",
     "categories": ["food", "nightlife"]},
    {"destination_id": 5, "city": "Bangkok", "country": "Thailand",
     "description": "Bustling capital known for street food, temples and river life.",
     "average_daily_cost": 70.0, "recommended_trip_length": 5, "travel_style": "Budget",
     "categories": ["food", "culture", "budget"]},
    {"destination_id": 7, "city": "Sydney", "country": "Australia",
     "description": "Harbour city with iconic beaches and the famous Opera House.",
     "average_daily_cost": 190.0, "recommended_trip_length": 5, "travel_style": "Coastal",
     "categories": ["beach", "nature", "family"]},
]
EXPENSES = [{"id": 1, "trip_reference": "Lisbon", "expense": "Zzyzx", "category": "misc",
             "estimated_cost": 5, "actual_cost": 6, "status": "paid"}]

REAL_DOC_SOURCES = list(rp.DOC_SOURCES)  # the production list, captured before fixtures replace it
SUPPORTED = "Which destination is known for street food and nightlife?"


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
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "NOTES.md").write_text("Ports: the Zephyr gateway listens on a dedicated port for routing.")
    monkeypatch.setattr(rp, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(rp, "DOC_SOURCES", [docs / "NOTES.md", tmp_path / "missing.md"])
    monkeypatch.setattr(rp, "CORPUS_PATH", tmp_path / "corpus" / "corpus.jsonl")
    monkeypatch.setattr(rp, "AUDIT_PATH", tmp_path / "audit.jsonl")
    monkeypatch.setattr(rp, "CHROMA_PATH", tmp_path / "chroma")
    monkeypatch.setattr(rp, "_collection", None)
    monkeypatch.setattr(rp, "_last_corpus_chunks", [])
    monkeypatch.setattr(rp, "load_repository_chunks", lambda: [])
    state = {"destinations": DESTINATIONS, "expenses": [], "ollama": [], "ollama_calls": []}

    def fake_get(url, timeout=None):
        if url.endswith("/destinations") and state["destinations"] is not None:
            return FakeResponse(200, state["destinations"])
        if url.endswith("/expenses") and state["expenses"] is not None:
            return FakeResponse(200, state["expenses"])
        raise requests.exceptions.ConnectionError(f"refused: {url}")

    def fake_post(url, json=None, timeout=None):
        state["ollama_calls"].append(json)
        action = state["ollama"]
        if isinstance(action, Exception):
            raise action
        return action or FakeResponse(200, {"response": "Answer:\nOsaka is known for street food and nightlife."})

    monkeypatch.setattr(rp.requests, "get", fake_get)
    monkeypatch.setattr(rp.requests, "post", fake_post)
    state["tmp"] = tmp_path
    return state


def force_lexical(monkeypatch):
    def boom():
        raise RuntimeError("chroma down")
    monkeypatch.setattr(rp, "get_collection", boom)


@pytest.fixture(params=["hybrid", "lexical_fallback"])
def mode(request, env, monkeypatch):
    if request.param == "lexical_fallback":
        force_lexical(monkeypatch)
    return request.param


def audit_records(env):
    path = env["tmp"] / "audit.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


# 1. supported
def test_supported_query_is_grounded_with_destination_citations(env, mode):
    out = rp.answer_question(SUPPORTED, caller="test")
    assert out["status"] == "success"
    assert out["confidence_category"] in ("High", "Medium")
    assert out["answer"].startswith("Osaka")
    cited = {c["chunk_id"]: c for c in out["citations"]}
    assert "destination_3" in cited
    assert cited["destination_3"]["source_id"] == "destination-db:/destinations"
    assert out["retrieval_summary"]["retrieval_mode"] == mode
    assert len(env["ollama_calls"]) == 1
    assert "lively nightlife" in env["ollama_calls"][0]["prompt"]


# 2. unsupported
@pytest.mark.parametrize("query", ["What is the capital of Mars?", "quantum physics homework help"])
def test_unsupported_query_is_insufficient_without_llm(env, mode, query):
    out = rp.answer_question(query)
    assert out["status"] == "insufficient_context"
    assert out["citations"] == []
    assert out["confidence_category"] == "Insufficient"
    assert out["answer"] == rp.INSUFFICIENT_ANSWER
    assert env["ollama_calls"] == []


# 3 + 4. zero overlap / relevance beats authority
def test_retrieve_returns_no_zero_overlap_rows(env, mode):
    assert rp.retrieve_context("quantum physics homework help")["results"] == []
    for q in (SUPPORTED, "temples in Japan", "Zephyr gateway port"):
        for row in rp.retrieve_context(q)["results"]:
            assert row["relevance_score"] > 0 and row["matched_terms"]


def test_relevance_beats_authority(env, mode):
    env["expenses"] = EXPENSES  # tier_1 chunk sharing no words with the query
    out = rp.retrieve_context("Zephyr gateway port routing")
    ids = [r["chunk_id"] for r in out["results"]]
    assert all(not i.startswith("budget_expense") for i in ids)
    assert out["results"][0]["authority_tier"] == "tier_2"
    assert out["results"][0]["source_id"] == "docs/NOTES.md"


# 5. confidence
def row(score, matched, tier="tier_1"):
    return {"relevance_score": score, "matched_terms": matched, "authority_tier": tier}


def test_confidence_levels():
    assert rp.confidence_from_results([row(1.0, ["a", "b"]), row(0.75, ["a", "b", "c"])]) == "High"
    assert rp.confidence_from_results([row(0.6, ["a", "b"])]) == "Medium"
    assert rp.confidence_from_results([row(0.5, ["bangkok"])]) == "Low"
    assert rp.confidence_from_results([row(1.0, ["a", "b"], "tier_3")]) == "Low"
    assert rp.confidence_from_results([]) == "Insufficient"


def test_entity_only_match_is_low_confidence(env, mode):
    out = rp.answer_question("How expensive is Bangkok?")
    assert out["status"] == "success"
    assert out["confidence_category"] == "Low"


# 6. citations
def test_citations_match_accepted_results(env, mode):
    retrieval = rp.retrieve_context(SUPPORTED)
    out = rp.answer_question(SUPPORTED)
    assert out["citations"]
    assert [c["chunk_id"] for c in out["citations"]] == [r["chunk_id"] for r in retrieval["results"]]


# 7. Ollama failure
@pytest.mark.parametrize("failure", [
    requests.exceptions.ConnectionError("refused"),
    FakeResponse(404, {"error": "model not found"}),
    FakeResponse(200, {"response": "   "}),
    FakeResponse(200, {"error": "model not found"}),
])
def test_ollama_failure_is_explicit_error(env, mode, failure):
    env["ollama"] = failure
    out = rp.answer_question(SUPPORTED)
    assert out["status"] == "error"
    assert out["error_type"] == "llm_unavailable"
    assert out["answer"] is None and out["citations"] == [] and out["confidence_category"] is None


# 8. LLM declines
def test_llm_decline_becomes_insufficient_context(env, mode):
    env["ollama"] = FakeResponse(200, {"response": "Insufficient evidence."})
    out = rp.answer_question(SUPPORTED)
    assert out["status"] == "insufficient_context"
    assert out["citations"] == []


# 9. Destination API down
def test_refresh_with_destination_api_down(env):
    env["destinations"] = None
    env["expenses"] = None
    out = rp.refresh_corpus()
    assert out["status"] == "success"
    assert out["source_status"]["destination_db"].startswith("unavailable")
    assert out["source_status"]["budget_db"].startswith("unavailable")
    assert out["missing_docs"] == ["missing.md"]
    ids = [c["chunk_id"] for c in rp.read_corpus()]
    assert not any(i.startswith("destination_") or "unavailable" in i for i in ids)
    assert rp.answer_question(SUPPORTED)["status"] == "insufficient_context"


def test_refresh_reports_destination_ok(env):
    out = rp.refresh_corpus()
    assert out["source_status"]["destination_db"] == "ok"
    assert sum(c["chunk_id"].startswith("destination_") for c in rp.read_corpus()) == len(DESTINATIONS)


# 10. audit
def test_audit_outcomes(env, monkeypatch):
    rp.answer_question(SUPPORTED)
    rp.answer_question("What is the capital of Mars?")
    env["ollama"] = FakeResponse(200, {"response": "Insufficient evidence."})
    rp.answer_question(SUPPORTED)
    env["ollama"] = requests.exceptions.ConnectionError("refused")
    rp.answer_question(SUPPORTED)
    outcomes = [r["outcome"] for r in audit_records(env) if r["tool_name"] == "answer_question"]
    assert outcomes == ["answer_generated", "insufficient_context", "llm_declined", "llm_unavailable"]
    retrieve = [r for r in audit_records(env) if r["tool_name"] == "retrieve_context"][0]
    assert "chunk_ids" in retrieve["tool_output"] and "rejected_count" in retrieve["tool_output"]


def test_audit_write_failure_does_not_break_call(env, monkeypatch):
    monkeypatch.setattr(rp, "AUDIT_PATH", env["tmp"] / "audit.jsonl" / "not-a-dir" / "x.jsonl")
    (env["tmp"] / "audit.jsonl").write_text("file, not a directory")
    assert rp.answer_question(SUPPORTED)["status"] == "success"


# 11. HTTP mapping
def test_http_status_mapping(env, monkeypatch):
    results = iter([
        {"status": "success", "answer": "x"},
        {"status": "insufficient_context", "answer": "x"},
        {"status": "error", "error_type": "llm_unavailable", "error": "down"},
        {"status": "error", "error_type": "retrieval_failed", "error": "bad"},
    ])
    monkeypatch.setattr(rag_http_server, "answer_question", lambda **kw: next(results))
    server = ThreadingHTTPServer(("127.0.0.1", 0), rag_http_server.RAGHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    codes = []
    try:
        for _ in range(4):
            req = urllib.request.Request(
                f"http://127.0.0.1:{server.server_port}/answer",
                data=json.dumps({"query": "q"}).encode(), headers={"Content-Type": "application/json"})
            try:
                codes.append(urllib.request.urlopen(req).status)
            except urllib.error.HTTPError as exc:
                codes.append(exc.code)
    finally:
        server.shutdown()
    assert codes == [200, 200, 503, 500]


# Regression: real repository docs must not become destination evidence (Codex blocker).
@pytest.fixture
def real_docs_env(env, monkeypatch):
    repo = Path(__file__).resolve().parents[3]
    assert (repo / "student-1" / "README.md").exists() and (repo / "README.md").exists()
    monkeypatch.setattr(rp, "REPO_ROOT", repo)
    monkeypatch.setattr(rp, "DOC_SOURCES", REAL_DOC_SOURCES)
    monkeypatch.setattr(rp, "load_repository_chunks", lambda: [])
    env["destinations"] = None
    env["expenses"] = None
    return env


@pytest.mark.parametrize("query", [
    "Which destination is known for street food and nightlife?",
    "street food and nightlife",
    "Compare Tokyo and Kyoto for nightlife and food",
])
def test_real_readmes_are_not_evidence_when_destination_api_down(real_docs_env, mode, query):
    out = rp.answer_question(query)
    assert out["status"] == "insufficient_context"
    assert out["citations"] == []
    assert out["confidence_category"] == "Insufficient"
    assert real_docs_env["ollama_calls"] == []
    sources = {c["source_id"] for c in rp.read_corpus()}
    assert "README.md" not in sources and "student-1/README.md" not in sources


def test_real_readmes_do_not_inflate_confidence_or_citations_with_destination_data(real_docs_env, mode):
    real_docs_env["destinations"] = DESTINATIONS
    out = rp.answer_question(SUPPORTED)
    assert out["status"] == "success"
    assert {c["source_id"] for c in out["citations"]} <= {"destination-db:/destinations"}
    assert "destination_3" in {c["chunk_id"] for c in out["citations"]}


# Upgrade regression: a stale persisted corpus/index from before the README exclusion.
LEGACY_README_CHUNKS = [
    {"chunk_id": "README_2", "source_id": "README.md", "authority_tier": "tier_2",
     "text": "A travel planner focused on food, nightlife and culture. Try: Compare Tokyo and Kyoto for someone into nightlife and food.",
     "metadata": {"source_type": "doc", "file": "README.md"}, "indexed_at": "2020-01-01T00:00:00+00:00"},
    {"chunk_id": "README_4", "source_id": "README.md", "authority_tier": "tier_2",
     "text": "Destination Manager Cities countries street food nightlife demo prompt Compare Tokyo and Kyoto nightlife and food.",
     "metadata": {"source_type": "doc", "file": "README.md"}, "indexed_at": "2020-01-01T00:00:00+00:00"},
    {"chunk_id": "README_5", "source_id": "student-1/README.md", "authority_tier": "tier_2",
     "text": "categories is a JSON array e.g. food, nightlife; preferences nightlife and food; street food example.",
     "metadata": {"source_type": "doc", "file": "README.md"}, "indexed_at": "2020-01-01T00:00:00+00:00"},
]


@pytest.fixture
def stale_env(env, monkeypatch):
    """Persisted-style state: corpus.jsonl and Chroma already populated with legacy README chunks."""
    env["destinations"] = None
    env["expenses"] = None
    # an unrelated non-README chunk keeps the persisted corpus non-empty after filtering, so no auto-refresh runs
    persisted = LEGACY_README_CHUNKS + [
        {"chunk_id": "accommodation_9", "source_id": "accommodation-db:/accommodations", "authority_tier": "tier_1",
         "text": "Accommodation record: name=Harbour Hotel, price_per_night=120, rating=4.", "metadata": {},
         "indexed_at": "2020-01-01T00:00:00+00:00"}]
    rp.write_corpus(persisted)
    collection = rp.get_collection()
    docs = [c["text"] for c in persisted]
    collection.add(ids=[c["chunk_id"] for c in persisted], documents=docs,
                   metadatas=[{"source_id": c["source_id"], "authority_tier": c["authority_tier"],
                               "indexed_at": c["indexed_at"]} for c in persisted],
                   embeddings=rp.embed_texts(docs))
    monkeypatch.setattr(rp, "_last_corpus_chunks", [])  # as after a restart: only persisted data exists
    return env


def test_stale_corpus_would_pass_acceptance_without_the_filter():
    q = rp.query_terms(SUPPORTED)
    assert all(len(q & rp.query_terms(c["text"])) >= 2 for c in LEGACY_README_CHUNKS[:2])


@pytest.mark.parametrize("persisted", ["hybrid", "lexical_fallback"])
def test_stale_readme_chunks_are_rejected(stale_env, monkeypatch, persisted):
    if persisted == "lexical_fallback":
        force_lexical(monkeypatch)
    retrieval = rp.retrieve_context(SUPPORTED)
    assert retrieval["status"] == "success"
    assert not (stale_env["tmp"] / "audit.jsonl").read_text().count("corpus_refreshed")
    assert not [r for r in retrieval["results"] if r["source_id"] in ("README.md", "student-1/README.md")]
    out = rp.answer_question(SUPPORTED)
    assert out["status"] == "insufficient_context"
    assert out["citations"] == []
    assert out["confidence_category"] == "Insufficient"
    assert stale_env["ollama_calls"] == []
