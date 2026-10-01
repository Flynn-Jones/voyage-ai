"""Offline tests for the frontend /mcp-lookup and /ask pages."""
import importlib.util
import os
import sys

import pytest
import requests

MODULE_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "frontend", "app.py")


def _load():
    spec = importlib.util.spec_from_file_location("destination_frontend_mcp_rag_app", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules["destination_frontend_mcp_rag_app"] = module
    spec.loader.exec_module(module)
    return module


fe = _load()
fe.app.testing = True

TOKYO = {"destination_id": 1, "city": "Tokyo", "country": "Japan", "travel_style": "City break",
         "average_daily_cost": 150.0, "recommended_trip_length": 6}
HTMX = {"HX-Request": "true"}


class Resp:
    def __init__(self, status=200, payload=None):
        self.status_code = status
        self._payload = payload

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


@pytest.fixture
def client():
    return fe.app.test_client()


@pytest.fixture
def backend(monkeypatch):
    calls = []
    state = {"resp": Resp(200, {}), "exc": None, "calls": calls}

    def fake_post(url, json=None, timeout=None, **kw):
        calls.append({"url": url, "json": json})
        if state["exc"]:
            raise state["exc"]
        return state["resp"]

    def fake_request(method, url, **kw):
        calls.append({"url": url, "json": kw.get("json")})
        return Resp(200, [])

    monkeypatch.setattr(fe.requests, "post", fake_post)
    monkeypatch.setattr(fe.requests, "request", fake_request)
    return state


def test_forms_and_nav(client):
    for path, marker in (("/mcp-lookup", 'name="travel_style"'), ("/ask", 'name="question"')):
        r = client.get(path)
        html = r.get_data(as_text=True)
        assert r.status_code == 200 and marker in html
        for link in ("Destinations", "Add destination", "Compare", "Data lookup (MCP)", "Ask (RAG)"):
            assert link in html


def test_mcp_success_renders_rows(client, backend):
    backend["resp"] = Resp(200, {"status": "success", "tool": "list_destinations", "count": 1,
                                  "filters": {"country": "Japan"}, "destinations": [TOKYO]})
    r = client.post("/mcp-lookup", data={"country": "Japan"}, headers=HTMX)
    html = r.get_data(as_text=True)
    assert "Tokyo" in html and "list_destinations" in html and "<nav" not in html
    assert backend["calls"][-1]["json"] == {"country": "Japan"}
    assert backend["calls"][-1]["url"].endswith("/api/destinations/mcp-search")


def test_mcp_plain_post_full_page_and_empty(client, backend):
    backend["resp"] = Resp(200, {"status": "success", "tool": "list_destinations", "count": 0, "filters": {}, "destinations": []})
    html = client.post("/mcp-lookup", data={"city": "Nowhere"}).get_data(as_text=True)
    assert "<nav" in html and "No destinations matched" in html


@pytest.mark.parametrize("status,code,text", [
    ("disabled", 503, "disabled"), ("tool_error", 502, "Destination data is unavailable"),
    ("timeout", 504, "timed out"), ("malformed", 502, "unexpected response"),
    ("invalid_request", 400, "city must be"),
])
def test_mcp_states(client, backend, status, code, text):
    errors = {"tool_error": "Destination data is unavailable to the shared MCP service.",
              "timeout": "The shared MCP service timed out.", "invalid_request": "city must be a string"}
    backend["resp"] = Resp(code, {"status": status, "error": errors.get(status, "x")})
    assert text in client.post("/mcp-lookup", data={"city": "x"}, headers=HTMX).get_data(as_text=True)


def test_mcp_malformed_row_renders_safe_alert(client, backend):
    # Backend response for an MCP row with average_daily_cost="expensive" (Codex R1-P03 blocker).
    backend["resp"] = Resp(502, {"status": "malformed", "error": "The shared service returned an unexpected response."})
    r = client.post("/mcp-lookup", data={"country": "Japan"}, headers=HTMX)
    html = r.get_data(as_text=True)
    assert r.status_code == 200
    assert "The shared service returned an unexpected response." in html
    assert "expensive" not in html and "Traceback" not in html


def test_mcp_backend_down_or_non_json(client, backend):
    backend["exc"] = requests.exceptions.ConnectionError("secret-host")
    html = client.post("/mcp-lookup", data={}, headers=HTMX).get_data(as_text=True)
    assert fe.UNAVAILABLE_MESSAGE in html and "secret-host" not in html
    backend["exc"] = None
    backend["resp"] = Resp(500, None)
    assert fe.UNAVAILABLE_MESSAGE in client.post("/mcp-lookup", data={}, headers=HTMX).get_data(as_text=True)


def test_rag_grounded_renders_answer_confidence_citations(client, backend):
    backend["resp"] = Resp(200, {"status": "success", "answer": "Osaka <script>x</script>", "confidence_category": "High",
                                  "citations": [{"chunk_id": "destination_3", "source_id": "destination-db:/destinations", "authority_tier": "tier_1"}]})
    html = client.post("/ask", data={"question": "street food?"}, headers=HTMX).get_data(as_text=True)
    assert "Osaka" in html and "Confidence: High" in html and "destination_3" in html and "destination-db:/destinations" in html
    assert "<script>x" not in html and "&lt;script&gt;" in html
    assert backend["calls"][-1]["json"] == {"question": "street food?"}
    assert backend["calls"][-1]["url"].endswith("/api/destinations/rag-answer")


def test_rag_insufficient(client, backend):
    backend["resp"] = Resp(200, {"status": "insufficient_context", "answer": "Insufficient evidence", "citations": [],
                                  "confidence_category": "Insufficient"})
    html = client.post("/ask", data={"question": "capital of Mars"}, headers=HTMX).get_data(as_text=True)
    assert "Not enough grounded evidence" in html and "Confidence: Insufficient" in html and "Citations" not in html


@pytest.mark.parametrize("status,code,text", [
    ("disabled", 503, "disabled"), ("timeout", 504, "timed out"), ("unavailable", 503, "unavailable"),
    ("malformed", 502, "unexpected response"),
    ("infrastructure_error", 502, "Infrastructure failure: The shared RAG service could not reach its language model."),
])
def test_rag_states(client, backend, status, code, text):
    errors = {"timeout": "The shared RAG service timed out.", "unavailable": "The shared RAG service is unavailable.",
              "infrastructure_error": "The shared RAG service could not reach its language model."}
    backend["resp"] = Resp(code, {"status": status, "error": errors.get(status, "x")})
    assert text in client.post("/ask", data={"question": "hello there"}, headers=HTMX).get_data(as_text=True)


def test_rag_backend_down(client, backend):
    backend["exc"] = requests.exceptions.Timeout("slow")
    assert "timed out" in client.post("/ask", data={"question": "hello"}, headers=HTMX).get_data(as_text=True)
    backend["exc"] = requests.exceptions.ConnectionError("x")
    assert fe.UNAVAILABLE_MESSAGE in client.post("/ask", data={"question": "hello"}, headers=HTMX).get_data(as_text=True)


def test_frontend_only_calls_destination_backend(client, backend):
    backend["resp"] = Resp(200, {"status": "disabled", "error": "x"})
    client.post("/mcp-lookup", data={})
    client.post("/ask", data={"question": "hello"})
    assert backend["calls"]
    assert all(c["url"].startswith(fe.BACKEND_SERVICE_URL) for c in backend["calls"])
    assert not any(":7001" in c["url"] or ":7002" in c["url"] for c in backend["calls"])
