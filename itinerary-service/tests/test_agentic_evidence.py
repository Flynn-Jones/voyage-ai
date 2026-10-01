import importlib.util
from pathlib import Path
from unittest.mock import Mock

import pytest
import requests

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("itinerary_evidence_test", ROOT / "ai-services/agentic_loop/collectors/itinerary_evidence.py")
collector = importlib.util.module_from_spec(spec)
spec.loader.exec_module(collector)


@pytest.fixture(autouse=True)
def config(monkeypatch):
    monkeypatch.setenv("AGENTIC_ITINERARY_TRIP", "T")
    monkeypatch.setattr(collector.requests, "get", Mock(return_value=Mock(json=Mock(return_value={"tools": ["get_itinerary"]}))))


def response(code, body):
    return Mock(status_code=code, json=Mock(return_value=body))


def test_mcp_actual_results_required(monkeypatch):
    monkeypatch.setattr(collector.requests, "post", Mock(side_effect=[
        response(200, {"result": {"trip_reference": "T", "count": 1, "items": [{"trip_reference": "T"}]}}),
        response(400, {"error": "invalid day"})]))
    assert collector.collect("MCP")[0]


@pytest.mark.parametrize("kind", ["MCP", "RAG"])
def test_tool_error_not_success(monkeypatch, kind):
    monkeypatch.setattr(collector.requests, "post", Mock(return_value=response(502, {"error": "unavailable"})))
    ok, evidence = collector.collect(kind)
    assert not ok and "unavailable" in evidence


def test_rag_grounding_and_insufficient_required(monkeypatch):
    monkeypatch.setattr(collector.requests, "post", Mock(side_effect=[
        response(200, {"itinerary_chunk_count": 1}),
        response(200, {"results": [{"chunk_id": "itinerary_1"}]}),
        response(200, {"citations": [{"chunk_id": "itinerary_1"}], "confidence_category": "High"}),
        response(200, {"citations": [], "confidence_category": "Insufficient"})]))
    assert collector.collect("RAG")[0]
    calls = collector.requests.post.call_args_list
    for call in calls[1:3]:
        assert call.kwargs["json"] == {
            "trip_reference": "T", "query": "What is the estimated itinerary cost?"
        }
    assert calls[3].kwargs["json"] == {
        "trip_reference": "T", "query": "What is tomorrow's weather?"
    }


def test_unavailable_health_recorded(monkeypatch):
    monkeypatch.setattr(collector.requests, "get", Mock(side_effect=requests.ConnectionError("offline")))
    ok, evidence = collector.collect("MCP")
    assert not ok and "offline" in evidence
