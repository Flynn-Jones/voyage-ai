import sys
from pathlib import Path
from unittest.mock import Mock

import pytest
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import create_app
from services import release1_api, database_api, enrichment


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("MCP_ENABLED", "true")
    monkeypatch.setenv("RAG_ENABLED", "true")
    return create_app().test_client()


@pytest.mark.parametrize("kind,path", [("MCP", "mcp/itinerary"), ("RAG", "rag/answer"), ("RAG", "rag/refresh"), ("RAG", "rag/retrieve")])
def test_disabled(client, monkeypatch, kind, path):
    monkeypatch.setenv(f"{kind}_ENABLED", "false")
    call = Mock()
    monkeypatch.setattr(release1_api.requests, "post", call)
    assert client.post(f"/api/itinerary/{path}", json={}).status_code == 403
    call.assert_not_called()


@pytest.mark.parametrize("path", ["mcp/itinerary", "rag/answer"])
@pytest.mark.parametrize("body", [[], {}, {"trip_reference": ""}, {"trip_reference": "T", "day": True}, {"trip_reference": "T", "day": 0}])
def test_bad_scope(client, path, body):
    assert client.post(f"/api/itinerary/{path}", json=body).status_code == 400


@pytest.mark.parametrize("query", ["", None, 3, "x" * 1001])
def test_invalid_question(client, query):
    assert client.post("/api/itinerary/rag/answer", json={"trip_reference": "T", "query": query}).status_code == 400


def test_mcp_forwarding(client, monkeypatch):
    result = {"status": "success", "result": {"trip_reference": "T", "count": 1, "items": [{"trip_reference": "T", "day": 2}]}}
    post = Mock(return_value=Mock(status_code=200, json=Mock(return_value=result)))
    monkeypatch.setattr(release1_api.requests, "post", post)
    monkeypatch.setenv("MCP_SERVICE_URL", "http://example:7001")
    response = client.post("/api/itinerary/mcp/itinerary", json={"trip_reference": "T", "day": 2, "tool": "delete"})
    assert response.json == result
    assert post.call_args.args == ("http://example:7001/get_itinerary",)
    assert post.call_args.kwargs["json"] == {"trip_reference": "T", "day": 2}


@pytest.mark.parametrize("confidence,citations", [("High", [{"chunk_id": "itinerary_1", "source_id": "itinerary-db:/itinerary-items"}]), ("Insufficient", [])])
def test_rag_preserves_evidence(client, monkeypatch, confidence, citations):
    result = {"status": "success", "answer": "Evidence answer", "confidence_category": confidence, "citations": citations}
    post = Mock(return_value=Mock(status_code=200, json=Mock(return_value=result)))
    monkeypatch.setattr(release1_api.requests, "post", post)
    response = client.post("/api/itinerary/rag/answer", json={"trip_reference": "T", "query": "What activities are planned?"})
    assert response.json == result
    assert post.call_args.kwargs["json"]["scope"] == "itinerary"


@pytest.mark.parametrize("path", ["mcp/itinerary", "rag/answer"])
@pytest.mark.parametrize("failure", [requests.Timeout(), requests.ConnectionError(), ValueError()])
def test_unavailable(client, monkeypatch, path, failure):
    monkeypatch.setattr(release1_api.requests, "post", Mock(side_effect=failure))
    assert client.post(f"/api/itinerary/{path}", json={"trip_reference": "T", "query": "activities"}).status_code == 502


@pytest.mark.parametrize("path", ["mcp/itinerary", "rag/answer"])
@pytest.mark.parametrize("status,body", [(500, {"status": "error"}), (200, []), (200, {"status": "success"}), (200, {"status": "error"})])
def test_invalid_upstream(client, monkeypatch, path, status, body):
    monkeypatch.setattr(release1_api.requests, "post", Mock(return_value=Mock(status_code=status, json=Mock(return_value=body))))
    assert client.post(f"/api/itinerary/{path}", json={"trip_reference": "T", "query": "activities"}).status_code == 502


def test_release0_independent(client, monkeypatch):
    monkeypatch.setenv("MCP_ENABLED", "false")
    monkeypatch.setenv("RAG_ENABLED", "false")
    monkeypatch.setattr(database_api, "get_items", lambda: [])
    monkeypatch.setattr(enrichment, "enrich_items", lambda items: items)
    assert client.get("/api/itinerary").json == []
    assert client.get("/health").status_code == 200
    assert client.post("/api/itinerary/ai-review", json={}).status_code == 400


def test_reject_cross_trip_mcp(client, monkeypatch):
    monkeypatch.setattr(release1_api, "post", lambda *args: {"status": "success", "result": {
        "trip_reference": "T", "count": 1, "items": [{"trip_reference": "OTHER", "day": 1}]}})
    assert client.post("/api/itinerary/mcp/itinerary", json={"trip_reference": "T"}).status_code == 502


@pytest.mark.parametrize("k", [True, "5", 0, 21])
def test_invalid_top_k(client, k):
    assert client.post("/api/itinerary/rag/answer", json={"trip_reference": "T", "query": "activities", "k": k}).status_code == 400


@pytest.mark.parametrize("path", ["mcp/itinerary", "rag/refresh", "rag/answer", "rag/retrieve"])
def test_malformed_json(client, path):
    assert client.post(f"/api/itinerary/{path}", data="{", content_type="application/json").status_code == 400


def test_refresh_success(client, monkeypatch):
    result = {"status": "success", "itinerary_chunk_count": 12, "itinerary_source_error": None}
    monkeypatch.setattr(release1_api, "post", lambda *args: result)
    assert client.post("/api/itinerary/rag/refresh", json={}).json == result


def test_refresh_source_failure(client, monkeypatch):
    monkeypatch.setattr(release1_api, "post", lambda *args: {"status": "success", "itinerary_chunk_count": 0, "itinerary_source_error": "offline"})
    assert client.post("/api/itinerary/rag/refresh", json={}).status_code == 502


def test_ai_review_independent_of_disabled_services(client, monkeypatch):
    from services import llm_client
    monkeypatch.setenv("MCP_ENABLED", "false")
    monkeypatch.setenv("RAG_ENABLED", "false")
    monkeypatch.setattr(database_api, "get_items_by_day", lambda day: [])
    monkeypatch.setattr(llm_client, "review_itinerary", lambda *args: {"model": "mock", "recommendation": "No records"})
    optional = Mock(side_effect=AssertionError("Release 0 must not use optional clients"))
    monkeypatch.setattr(release1_api, "post", optional)
    assert client.post("/api/itinerary/ai-review", json={"prompt": "Review Day 1"}).status_code == 200
    optional.assert_not_called()


def test_rag_model_failure_is_clear_at_backend(client, monkeypatch):
    monkeypatch.setattr(release1_api.requests, "post", Mock(return_value=Mock(
        status_code=500, json=Mock(return_value={"status": "error", "error_type": "llm_unavailable"}))))
    result = client.post("/api/itinerary/rag/answer", json={"trip_reference": "T", "query": "activities"})
    assert result.status_code == 502
    assert "model is unavailable" in result.json["error"] and "answer" not in result.json
