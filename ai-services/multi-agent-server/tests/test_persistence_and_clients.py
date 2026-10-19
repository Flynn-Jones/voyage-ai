"""Runs survive a restart; RAG/MCP client response handling."""
import json

import requests

from clients import mcp_client, rag_client
from tests.conftest import Client


def test_runs_survive_restart(make_app, paths):
    first = Client(make_app())
    run = first.start()

    restarted = Client(make_app())  # new Store + Registry + Runner over the same files
    status, body = restarted.get(f"/runs/{run['run_id']}")
    assert status == 200
    assert body["result"]["state"] == "awaiting_human_review"
    assert body["result"]["work"] == run["work"]
    assert restarted.post(f"/runs/{run['run_id']}/review", {"decision": "approve"})[0] == 200
    assert len(restarted.get(f"/runs/{run['run_id']}/history")[1]["result"]) == 6
    assert paths["audit"].exists()


def test_interrupted_run_is_failed_on_restart(make_app):
    first = Client(make_app())
    run_id = first.app.store.create_run("example_readonly", {}, "pytest")  # crashed mid-run

    restarted = Client(make_app())
    run = restarted.get(f"/runs/{run_id}")[1]["result"]
    assert run["state"] == "failed"
    assert "restarted" in run["error"]


class FakeResponse:
    def __init__(self, status_code, body):
        self.status_code = status_code
        self._body = body
        self.text = json.dumps(body)
        self.ok = status_code < 400

    def json(self):
        return self._body


def test_rag_client_posts_contract_payload(monkeypatch):
    sent = {}

    def fake_post(url, json, timeout):
        sent.update(url=url, json=json)
        return FakeResponse(200, {"status": "insufficient_context", "confidence_category": "Insufficient"})

    monkeypatch.setattr(rag_client.requests, "post", fake_post)
    monkeypatch.setenv("RAG_SERVICE_URL", "http://rag.test:7002/")
    result = rag_client.call("answer", "What is planned?", k=3, scope="itinerary", trip_reference="T1")
    assert sent["url"] == "http://rag.test:7002/answer"
    assert sent["json"] == {"query": "What is planned?", "k": 3, "caller": "multi-agent-server",
                            "scope": "itinerary", "trip_reference": "T1"}
    assert result["http_status"] == 200


def test_rag_client_returns_error_bodies_and_raises_when_down(monkeypatch):
    monkeypatch.setattr(rag_client.requests, "post",
                        lambda *a, **k: FakeResponse(503, {"status": "error", "error_type": "llm_unavailable"}))
    assert rag_client.call("answer", "q")["error_type"] == "llm_unavailable"

    def down(*a, **k):
        raise requests.ConnectionError("refused")
    monkeypatch.setattr(rag_client.requests, "post", down)
    try:
        rag_client.call("retrieve", "q")
    except rag_client.RAGUnavailableError:
        pass
    else:
        raise AssertionError("expected RAGUnavailableError")


def test_mcp_legacy_shim_envelope_handling(monkeypatch):
    monkeypatch.setenv("MCP_SERVICE_URL", "http://mcp.test:7001/mcp")
    assert mcp_client.legacy_base_url() == "http://mcp.test:7001"
    responses = iter([
        FakeResponse(200, {"status": "success", "result": {"count": 1}}),
        FakeResponse(502, {"status": "error", "result": {"error": "budget-db unavailable"}}),
        FakeResponse(404, {"status": "error", "error": "unknown tool: x"}),
    ])
    monkeypatch.setattr(mcp_client.requests, "post", lambda *a, **k: next(responses))
    assert mcp_client._legacy_call("list_expenses", {}) == {"count": 1}
    assert mcp_client._legacy_call("list_expenses", {}) == {"error": "budget-db unavailable"}
    assert mcp_client._legacy_call("x", {}) == {"error": "unknown tool: x"}


def test_mcp_result_normalisation():
    assert mcp_client._normalise({"count": 2}, []) == {"count": 2}
    assert mcp_client._normalise({"result": [1, 2]}, []) == [1, 2]
    assert mcp_client._normalise(None, ['{"count": 3}']) == {"count": 3}
    assert mcp_client._normalise(None, ["plain"]) == {"text": "plain"}
