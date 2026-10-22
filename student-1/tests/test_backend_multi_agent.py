"""Offline tests for the backend's /api/destinations/multi-agent/* routes and the
AI_ENABLED switch. The shared Multi-Agent Server is faked by replacing
multi_agent_client.requests.request; nothing touches the network."""
import importlib.util
import os
import sys

import pytest
import requests

BACKEND_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend")
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)


def _load():
    spec = importlib.util.spec_from_file_location("destination_backend_ma_app", os.path.join(BACKEND_DIR, "app.py"))
    module = importlib.util.module_from_spec(spec)
    sys.modules["destination_backend_ma_app"] = module
    spec.loader.exec_module(module)
    return module


backend_app = _load()
ma = backend_app.multi_agent_client
BASE = "/api/destinations/multi-agent/runs"
RUN = {"run_id": "abc123", "workflow": "destination_recommend", "state": "awaiting_human_review",
       "work": {"items": [{"id": 3, "city": "Osaka"}]}}


class Resp:
    def __init__(self, status=200, payload=None):
        self.status_code = status
        self._payload = payload

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


def ok(result, status=200):
    return Resp(status, {"status": "success", "result": result})


def err(message, status):
    return Resp(status, {"status": "error", "error": message})


class FakeMAS:
    """Routes requests.request(method, url, ...) by (method, path); records every call."""

    def __init__(self):
        self.calls = []
        self.routes = {}
        self.exc = None

    def __call__(self, method, url, json=None, params=None, timeout=None, **kw):
        path = url[len(ma.MULTI_AGENT_SERVICE_URL):]
        self.calls.append({"method": method, "path": path, "json": json, "params": params, "timeout": timeout})
        if self.exc:
            raise self.exc
        return self.routes[(method, path)]

    def paths(self):
        return [(c["method"], c["path"]) for c in self.calls]


@pytest.fixture
def client():
    backend_app.app.testing = True
    return backend_app.app.test_client()


@pytest.fixture
def mas(monkeypatch):
    fake = FakeMAS()
    monkeypatch.setattr(ma.requests, "request", fake)
    monkeypatch.setattr(ma, "MULTI_AGENT_ENABLED", True)
    return fake


# ---------------------------------------------------------------- disabled (cloud default)

@pytest.mark.parametrize("method,path,body", [
    ("post", BASE, {"interests": ["food"]}),
    ("get", BASE, None),
    ("get", f"{BASE}/abc123", None),
    ("get", f"{BASE}/abc123/history", None),
    ("post", f"{BASE}/abc123/review", {"decision": "approve"}),
])
def test_disabled_routes_return_503_without_outbound_call(client, mas, monkeypatch, method, path, body):
    monkeypatch.setattr(ma, "MULTI_AGENT_ENABLED", False)
    r = getattr(client, method)(path, **({"json": body} if body is not None else {}))
    assert r.status_code == 503 and r.get_json() == {"status": "disabled", "error": "Multi-Agent mode is disabled."}
    assert mas.calls == []


# ---------------------------------------------------------------- start

def test_start_forwards_only_input_to_the_destination_workflow(client, mas):
    mas.routes[("POST", "/workflows/destination_recommend/runs")] = ok(RUN, 201)
    r = client.post(BASE, json={"interests": ["food"], "max_daily_cost": 130, "country": "Japan"})
    assert r.status_code == 201 and r.get_json()["result"]["run_id"] == "abc123"
    call = mas.calls[0]
    assert call["json"] == {"input": {"interests": ["food"], "max_daily_cost": 130, "country": "Japan"},
                            "requested_by": "destination-backend"}
    assert call["timeout"] == ma.MULTI_AGENT_TIMEOUT_SECONDS


@pytest.mark.parametrize("body", [{"workflow": "budget_reconcile"}, {"interests": ["food"], "evil": 1}, ["food"]])
def test_start_rejects_unknown_fields_and_non_objects(client, mas, body):
    r = client.post(BASE, json=body)
    assert r.status_code == 400 and r.get_json()["status"] == "invalid_request"
    assert mas.calls == []


@pytest.mark.parametrize("mas_status,expected", [(400, 400), (500, 500)])
def test_start_passes_through_workflow_errors(client, mas, mas_status, expected):
    mas.routes[("POST", "/workflows/destination_recommend/runs")] = err("invalid input: nope", mas_status)
    r = client.post(BASE, json={"interests": ["food"]})
    assert r.status_code == expected and r.get_json() == {"status": "error", "error": "invalid input: nope"}


@pytest.mark.parametrize("exc,code,status", [
    (requests.exceptions.Timeout("slow"), 504, "timeout"),
    (requests.exceptions.ConnectionError("refused"), 503, "unavailable"),
])
def test_start_timeout_and_unavailable_hide_raw_detail(client, mas, exc, code, status):
    mas.exc = exc
    r = client.post(BASE, json={"interests": ["food"]})
    assert r.status_code == code and r.get_json()["status"] == status
    text = r.get_data(as_text=True)
    assert "refused" not in text and "slow" not in text


@pytest.mark.parametrize("resp", [Resp(200, None), Resp(200, {"nope": 1}), Resp(200, ["x"])])
def test_start_malformed_mas_reply_is_502(client, mas, resp):
    mas.routes[("POST", "/workflows/destination_recommend/runs")] = resp
    r = client.post(BASE, json={"interests": ["food"]})
    assert r.status_code == 502 and r.get_json()["status"] == "malformed"


# ---------------------------------------------------------------- list / get / history

def test_list_filters_to_the_destination_workflow_and_clamps_limit(client, mas):
    mas.routes[("GET", "/runs")] = ok([RUN])
    r = client.get(f"{BASE}?limit=9999")
    assert r.status_code == 200 and r.get_json()["result"] == [RUN]
    assert mas.calls[0]["params"] == {"workflow": "destination_recommend", "limit": 50}
    assert client.get(f"{BASE}?limit=abc").status_code == 400


def test_get_run_and_history(client, mas):
    mas.routes[("GET", "/runs/abc123")] = ok(RUN)
    mas.routes[("GET", "/runs/abc123/history")] = ok([{"seq": 1, "step": "created"}])
    assert client.get(f"{BASE}/abc123").get_json()["result"]["state"] == "awaiting_human_review"
    assert client.get(f"{BASE}/abc123/history").get_json()["result"][0]["step"] == "created"


def test_foreign_workflow_run_is_404_and_history_is_not_fetched(client, mas):
    mas.routes[("GET", "/runs/abc123")] = ok({**RUN, "workflow": "budget_reconcile"})
    assert client.get(f"{BASE}/abc123").status_code == 404
    assert client.get(f"{BASE}/abc123/history").status_code == 404
    assert ("GET", "/runs/abc123/history") not in mas.paths()


def test_unknown_run_passes_404_through(client, mas):
    mas.routes[("GET", "/runs/nope")] = err("unknown run: nope", 404)
    r = client.get(f"{BASE}/nope")
    assert r.status_code == 404 and r.get_json()["status"] == "error"


@pytest.mark.parametrize("run_id", ["a" * 65, "bad.id", "x%20y"])
def test_bad_run_id_is_400_without_outbound_call(client, mas, run_id):
    assert client.get(f"{BASE}/{run_id}").status_code in (400, 404)
    assert mas.calls == []


# ---------------------------------------------------------------- human review

def _review_setup(mas, state="approved", applied=ok({"state": "applied"})):
    mas.routes[("GET", "/runs/abc123")] = ok(RUN)
    mas.routes[("POST", "/runs/abc123/review")] = ok(
        {"run_id": "abc123", "state": state, "decision": "x", "approved_items": [{"id": 3}], "rejected_ids": []})
    mas.routes[("POST", "/runs/abc123/applied")] = applied


@pytest.mark.parametrize("decision,state", [("approve", "approved"), ("partial", "partially_accepted"),
                                            ("correct", "corrected")])
def test_accepting_decisions_forward_review_then_report_applied_with_no_created_ids(client, mas, decision, state):
    _review_setup(mas, state=state)
    r = client.post(f"{BASE}/abc123/review", json={
        "decision": decision, "approved_ids": [3], "edits": {"3": {"reviewer_correction": "x"}},
        "note": "ok", "reviewer": "flynn"})
    assert r.status_code == 200
    result = r.get_json()["result"]
    assert result["state"] == "applied" and result["applied_recorded"] is True
    assert result["approved_items"] == [{"id": 3}]
    review_call = next(c for c in mas.calls if c["path"] == "/runs/abc123/review")
    assert review_call["json"] == {"decision": decision, "approved_ids": [3], "rejected_ids": [],
                                   "edits": {"3": {"reviewer_correction": "x"}}, "note": "ok", "reviewer": "flynn"}
    applied_call = next(c for c in mas.calls if c["path"] == "/runs/abc123/applied")
    assert applied_call["json"] == {"created_ids": [], "target": "none (read-only destination_recommend)"}


def test_reject_does_not_report_applied(client, mas):
    _review_setup(mas, state="rejected")
    r = client.post(f"{BASE}/abc123/review", json={"decision": "reject"})
    result = r.get_json()["result"]
    assert r.status_code == 200 and result["state"] == "rejected" and result["applied_recorded"] is None
    assert ("POST", "/runs/abc123/applied") not in mas.paths()
    assert next(c for c in mas.calls if c["path"].endswith("/review"))["json"]["reviewer"] == "destination-ui"


def test_applied_report_failure_is_not_hidden(client, mas):
    _review_setup(mas, applied=err("boom", 409))
    result = client.post(f"{BASE}/abc123/review", json={"decision": "approve"}).get_json()["result"]
    assert result["state"] == "approved" and result["applied_recorded"] is False


@pytest.mark.parametrize("body", [
    {}, {"decision": "maybe"}, {"decision": "partial", "approved_ids": "3"},
    {"decision": "correct", "edits": [1]}, {"decision": "approve", "note": 5},
    {"decision": "approve", "note": "x" * 1001}, {"decision": "approve", "reviewer": "r" * 101}, ["approve"],
])
def test_invalid_review_is_400_without_outbound_call(client, mas, body):
    r = client.post(f"{BASE}/abc123/review", json=body)
    assert r.status_code == 400 and r.get_json()["status"] == "invalid_request"
    assert mas.calls == []


def test_already_decided_conflict_passes_through_and_nothing_is_applied(client, mas):
    mas.routes[("GET", "/runs/abc123")] = ok(RUN)
    mas.routes[("POST", "/runs/abc123/review")] = err("already reviewed", 409)
    r = client.post(f"{BASE}/abc123/review", json={"decision": "approve"})
    assert r.status_code == 409
    assert ("POST", "/runs/abc123/applied") not in mas.paths()


def test_review_of_foreign_run_never_submits(client, mas):
    mas.routes[("GET", "/runs/abc123")] = ok({**RUN, "workflow": "activity_schedule_check"})
    assert client.post(f"{BASE}/abc123/review", json={"decision": "approve"}).status_code == 404
    assert ("POST", "/runs/abc123/review") not in mas.paths()


# ---------------------------------------------------------------- AI switch + health + CRUD stays up

def test_ai_compare_disabled_returns_503_without_any_outbound_call(client, monkeypatch):
    calls = []
    monkeypatch.setattr(backend_app.requests, "request", lambda *a, **k: calls.append(a))
    monkeypatch.setattr(backend_app.llm_client, "AI_ENABLED", False)
    monkeypatch.setattr(backend_app.llm_client.requests, "post", lambda *a, **k: calls.append(a))
    r = client.post("/api/destinations/ai-compare", json={"city_a": "Tokyo", "city_b": "Kyoto"})
    assert r.status_code == 503 and r.get_json() == {"status": "disabled", "error": "AI comparison is disabled."}
    assert calls == []


def test_health_reports_feature_flags(client, monkeypatch):
    monkeypatch.setattr(backend_app, "_request", lambda *a, **k: Resp(200, {"status": "ok"}))
    for module, name in ((backend_app.llm_client, "AI_ENABLED"), (backend_app.mcp_client, "MCP_ENABLED"),
                         (backend_app.rag_client, "RAG_ENABLED"), (ma, "MULTI_AGENT_ENABLED")):
        monkeypatch.setattr(module, name, False)
    body = client.get("/health").get_json()
    assert body["status"] == "ok"
    assert body["features"] == {"ai": False, "mcp": False, "rag": False, "multi_agent": False}


def test_crud_still_works_with_every_ai_feature_off(client, monkeypatch):
    for module, name in ((backend_app.llm_client, "AI_ENABLED"), (backend_app.mcp_client, "MCP_ENABLED"),
                         (backend_app.rag_client, "RAG_ENABLED"), (ma, "MULTI_AGENT_ENABLED")):
        monkeypatch.setattr(module, name, False)
    seen = []

    def fake_request(method, url, **kw):
        seen.append((method, url.rsplit(":6001", 1)[-1]))
        return Resp(200, [{"destination_id": 1, "city": "Tokyo"}])

    monkeypatch.setattr(backend_app.requests, "request", fake_request)
    r = client.get("/api/destinations")
    assert r.status_code == 200 and r.get_json()[0]["city"] == "Tokyo"
    assert seen == [("GET", "/destinations")]
