"""Activity backend -> the ONE shared RAG server (ai-services/rag-server, :7002).

Replaces the retired tests/test_rag_pipeline.py: the pipeline half now lives in
ai-services/rag-server/tests/test_activity_scope.py. Here, the backend's RAG
routes are checked on their own (gating, validation, error mapping) and then
end-to-end against a real shared rag_http_server on a loopback port, with the
corpus/Chroma/audit under tmp_path and the activity database API faked.

Run from activity-service/ with: python -m pytest tests/test_shared_rag_bridge.py
"""
import json
import sys
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest
import requests

pytest.importorskip("chromadb")

REPO_ROOT = Path(__file__).resolve().parents[2]
SHARED_RAG = REPO_ROOT / "ai-services" / "rag-server"
for path in (SHARED_RAG, REPO_ROOT / "activity-service" / "backend"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import activity_context  # noqa: E402  (shared server)
import rag_http_server  # noqa: E402  (shared server)
import rag_pipeline as rp  # noqa: E402  (shared server)

ACTIVITIES = [
    {"activity_id": 1, "activity_name": "Harbour Kayaking Tour", "activity_type": "Adventure",
     "activity_cost": 45.0, "duration": "2 hours"},
    {"activity_id": 3, "activity_name": "Street Food Night Walk", "activity_type": "Food",
     "activity_cost": 60.0, "duration": "3 hours"},
    {"activity_id": 4, "activity_name": "Old Town Cycling Tour", "activity_type": "Adventure",
     "activity_cost": 40.0, "duration": "half day"},
]
ASSIGNMENTS = [{"assignment_id": 2, "activity_id": 3, "assignment_time": "2026-10-03 18:30"}]


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


# ---------------------------------------------------------------- routes in isolation

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


def test_backend_targets_the_shared_rag_port_by_default():
    from services import rag_api
    assert rag_api.RAG_SERVICE_URL.endswith(":7002")
    assert rag_api.RAG_SCOPE == "activity"


@pytest.mark.parametrize("path", ["/api/activity/rag/refresh", "/api/activity/rag/retrieve",
                                  "/api/activity/rag/answer"])
def test_routes_403_when_header_off(backend, path):
    client, forwarded, _, _ = backend
    response = client.post(path, json={"query": "food"}, headers={"X-RAG-Mode": "off"})
    assert response.status_code == 403
    assert response.get_json() == {"status": "error", "error": "RAG mode is disabled."}
    assert forwarded == []


def test_routes_403_when_disabled_by_env(backend):
    client, forwarded, rag_api, monkeypatch = backend
    monkeypatch.setattr(rag_api, "RAG_ENABLED", False)
    assert client.post("/api/activity/rag/answer", json={"query": "food"}).status_code == 403
    assert forwarded == []


@pytest.mark.parametrize("body", [{}, {"query": ""}, {"query": "   "}, {"query": 5},
                                  {"query": "food", "k": 0}, {"query": "food", "k": "abc"}])
@pytest.mark.parametrize("path", ["/api/activity/rag/retrieve", "/api/activity/rag/answer"])
def test_routes_400_on_bad_input(backend, path, body):
    client, forwarded, _, _ = backend
    assert client.post(path, json=body).status_code == 400
    assert forwarded == []


def test_forwards_validated_payload_with_activity_scope(backend):
    client, forwarded, rag_api, _ = backend
    response = client.post("/api/activity/rag/answer", json={"query": "  food tours ", "k": "3"})
    assert response.status_code == 200
    assert forwarded == [(f"{rag_api.RAG_SERVICE_URL}/answer",
                          {"query": "food tours", "k": 3, "caller": "backend", "scope": "activity"})]


def test_refresh_is_forwarded_unscoped(backend):
    client, forwarded, rag_api, _ = backend
    assert client.post("/api/activity/rag/refresh").status_code == 200
    assert forwarded == [(f"{rag_api.RAG_SERVICE_URL}/refresh", {"caller": "backend"})]


def test_503_when_rag_server_unreachable(backend):
    client, _, rag_api, monkeypatch = backend

    def unreachable(url, json=None, timeout=None):
        raise requests.exceptions.ConnectionError("refused")
    monkeypatch.setattr(rag_api.requests, "post", unreachable)

    response = client.post("/api/activity/rag/refresh")
    assert response.status_code == 503
    assert "RAG server unavailable" in response.get_json()["error"]


@pytest.mark.parametrize("upstream", [500, 503])
def test_rag_tool_error_maps_to_502(backend, upstream):
    client, _, rag_api, monkeypatch = backend
    monkeypatch.setattr(rag_api.requests, "post", lambda url, json=None, timeout=None: DummyResponse(
        upstream, {"status": "error", "error": "LLM unavailable", "error_type": "llm_unavailable"}))
    response = client.post("/api/activity/rag/answer", json={"query": "food"})
    assert response.status_code == 502
    assert response.get_json()["error_type"] == "llm_unavailable"


# ---------------------------------------------------------------- end to end: backend -> real :7002 handler

@pytest.fixture
def live(tmp_path, monkeypatch):
    monkeypatch.setattr(rp, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(rp, "DOC_SOURCES", [])
    monkeypatch.setattr(rp, "CORPUS_PATH", tmp_path / "corpus" / "corpus.jsonl")
    monkeypatch.setattr(rp, "AUDIT_PATH", tmp_path / "audit.jsonl")
    monkeypatch.setattr(rp, "CHROMA_PATH", tmp_path / "chroma")
    monkeypatch.setattr(rp, "_collection", None)
    monkeypatch.setattr(rp, "_last_corpus_chunks", [])
    monkeypatch.setattr(rp, "load_repository_chunks", lambda: [])

    api = {"/activities": ACTIVITIES, "/assignments": ASSIGNMENTS,
           "/destinations": [{"destination_id": 5, "city": "Bangkok", "country": "Thailand",
                              "description": "Known for street food."}]}

    def fake_db(url, params=None, timeout=None):
        for path, payload in api.items():
            if url.endswith(path):
                return DummyResponse(200, payload)
        raise requests.exceptions.ConnectionError(f"refused: {url}")

    # The shared pipeline reads every feature DB with requests.get; the backend
    # only ever uses requests.post, which stays real and goes over loopback.
    monkeypatch.setattr(rp.requests, "get", fake_db)
    generated = []

    def fake_llm(query, context):
        generated.append(context)
        return "Answer: The Street Food Night Walk is scheduled for 2026-10-03 18:30."
    monkeypatch.setattr(rp, "generate_with_ollama", fake_llm)

    server = ThreadingHTTPServer(("127.0.0.1", 0), rag_http_server.RAGHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    from services import rag_api
    monkeypatch.setattr(rag_api, "RAG_SERVICE_URL", f"http://127.0.0.1:{server.server_port}")
    monkeypatch.setattr(rag_api, "RAG_ENABLED", True)

    import app as app_module
    with app_module.create_app().test_client() as client:
        client.generated = generated
        yield client
    server.shutdown()


def test_end_to_end_refresh_then_deterministic_answer(live):
    refreshed = live.post("/api/activity/rag/refresh")
    assert refreshed.status_code == 200
    body = refreshed.get_json()
    assert body["source_status"]["activity_db"] == "ok"
    assert body["service_counts"]["activity"] > 0 and body["service_counts"]["destination"] == 1

    answer = live.post("/api/activity/rag/answer", json={"query": "How many activities are there?"})
    assert answer.status_code == 200
    body = answer.get_json()
    assert body["scope"] == "activity" and body["answer_source"] == "deterministic"
    assert body["answer"] == "There are 3 activities in total."
    assert live.generated == []


def test_end_to_end_retrieve_is_activity_only_with_numeric_distances(live):
    live.post("/api/activity/rag/refresh")
    body = live.post("/api/activity/rag/retrieve", json={"query": "street food", "k": 5}).get_json()
    ids = [r["chunk_id"] for r in body["results"]]
    assert "activity_3" in ids and "destination_5" not in ids
    assert all(isinstance(r["distance"], float) for r in body["results"])


def test_end_to_end_llm_answer_cites_activity_chunks(live):
    live.post("/api/activity/rag/refresh")
    body = live.post("/api/activity/rag/answer",
                     json={"query": "When is the street food night walk scheduled?", "k": 3}).get_json()
    assert body["status"] == "success" and body["answer_source"] == "llm"
    assert body["answer"].startswith("The Street Food Night Walk")
    assert {c["chunk_id"] for c in body["citations"]} & {"activity_3", "assignment_2"}
    assert "destination_5" not in live.generated[0]


def test_end_to_end_irrelevant_question_is_insufficient(live):
    live.post("/api/activity/rag/refresh")
    response = live.post("/api/activity/rag/answer", json={"query": "zebra quantum volcano"})
    assert response.status_code == 200
    assert response.get_json()["confidence_category"] == "Insufficient"
    assert live.generated == []


def test_end_to_end_activity_db_down_still_refreshes(live, monkeypatch):
    monkeypatch.setattr(activity_context, "ACTIVITY_DB_URL", "http://activity-db-down.invalid:6003")
    monkeypatch.setattr(rp.requests, "get", lambda url, params=None, timeout=None: (_ for _ in ()).throw(
        requests.exceptions.ConnectionError("refused")))
    body = live.post("/api/activity/rag/refresh").get_json()
    assert body["status"] == "success"
    assert body["source_status"]["activity_db"].startswith("unavailable")
