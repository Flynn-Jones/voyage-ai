"""Frontend tests for the RAG Mode tab (/rag-mode) against a mocked backend.

Run with the rest of the frontend suite:
    python -m pytest activity-service/frontend/tests
"""
from conftest import make_recording_backend


def post_rag_call(app_module, monkeypatch, form, status_code, payload):
    calls = []
    monkeypatch.setattr(app_module.requests, "request", make_recording_backend(calls, status_code, payload))
    with app_module.create_app().test_client() as test_client:
        resp = test_client.post("/rag-mode/call", data=form)
    return resp, calls


def test_rag_mode_200(client):
    resp = client.get("/rag-mode")
    assert resp.status_code == 200
    assert b"RAG Mode" in resp.data and b"Refresh Corpus" in resp.data
    assert b'id="rag-toggle"' in resp.data


def test_home_page_links_to_rag_mode(client):
    resp = client.get("/")
    assert b'href="/rag-mode">RAG Mode</a>' in resp.data


def test_rag_answer_card_renders_answer_confidence_and_citations(app_module, monkeypatch):
    resp, calls = post_rag_call(app_module, monkeypatch, {"kind": "answer", "query": "How many?", "k": "5",
                                                          "rag_mode": "on"}, 200, {
        "status": "success", "answer": "There are 10 activities in total.", "answer_source": "deterministic",
        "confidence_category": "High",
        "citations": [{"chunk_id": "activity_count", "source_id": "database-service", "authority_tier": "tier_1"}],
    })
    assert resp.status_code == 200
    assert b"There are 10 activities in total." in resp.data
    assert b"Confidence: High" in resp.data and b"activity_count" in resp.data
    assert calls[0]["url"].endswith("/api/activity/rag/answer")
    assert calls[0]["json"] == {"query": "How many?", "k": "5"}
    assert calls[0]["headers"] == {"X-RAG-Mode": "on"}


def test_rag_retrieve_card_lists_ranked_chunks(app_module, monkeypatch):
    resp, calls = post_rag_call(app_module, monkeypatch, {"kind": "retrieve", "query": "kayak", "k": "1"}, 200, {
        "status": "success", "retrieval_mode": "vector", "results": [
            {"rank": 1, "chunk_id": "activity_1", "source_id": "database-service", "authority_tier": "tier_1",
             "distance": 0.197, "text": "Activity record: activity_id=1, name=Harbour Kayaking Tour."},
        ],
    })
    assert b"Top 1 chunks (vector)" in resp.data
    assert b"activity_1" in resp.data and b"distance 0.197" in resp.data
    assert calls[0]["url"].endswith("/api/activity/rag/retrieve")


def test_rag_refresh_card_shows_corpus_summary(app_module, monkeypatch):
    resp, _ = post_rag_call(app_module, monkeypatch, {"kind": "refresh"}, 200, {
        "status": "success", "chunk_count": 111, "tier_counts": {"tier_1": 28, "tier_2": 82, "tier_3": 1},
        "vector_store_status": "ready", "embedding_mode": "ollama", "tier_1_source": "database-service",
    })
    assert b"111 chunks" in resp.data and b"tier_1: 28" in resp.data and b"ollama embeddings" in resp.data


def test_rag_toggle_off_forwards_header_and_shows_backend_error(app_module, monkeypatch):
    resp, calls = post_rag_call(app_module, monkeypatch, {"kind": "refresh", "rag_mode": "off"}, 403,
                                {"status": "error", "error": "RAG mode is disabled."})
    assert resp.status_code == 200  # card swapped in; the 403 is shown on it
    assert b"RAG mode is disabled." in resp.data and b"HTTP 403" in resp.data
    assert calls[0]["headers"] == {"X-RAG-Mode": "off"}


def test_rag_empty_query_is_not_forwarded(app_module, monkeypatch):
    resp, calls = post_rag_call(app_module, monkeypatch, {"kind": "answer", "query": "  "}, 200, {})
    assert resp.status_code == 204
    assert calls == []


def test_rag_unknown_action_is_not_forwarded(app_module, monkeypatch):
    resp, calls = post_rag_call(app_module, monkeypatch, {"kind": "delete"}, 200, {})
    assert b"Unknown RAG action" in resp.data
    assert calls == []


def test_rag_backend_unreachable_shows_error_card(app_module, monkeypatch):
    def boom(method, url, params=None, json=None, timeout=None, headers=None):
        raise app_module.requests.exceptions.RequestException("connection refused")

    monkeypatch.setattr(app_module.requests, "request", boom)
    with app_module.create_app().test_client() as test_client:
        resp = test_client.post("/rag-mode/call", data={"kind": "refresh"})
    assert resp.status_code == 200
    assert b"no response" in resp.data and b"connection refused" in resp.data
