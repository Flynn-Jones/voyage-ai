"""Offline tests for backend POST /api/destinations/mcp-search (shared MCP client)."""
import importlib.util
import os
import sys

import pytest
import requests

BACKEND_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend")
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)


def _load():
    spec = importlib.util.spec_from_file_location("destination_backend_mcp_app", os.path.join(BACKEND_DIR, "app.py"))
    module = importlib.util.module_from_spec(spec)
    sys.modules["destination_backend_mcp_app"] = module
    spec.loader.exec_module(module)
    return module


backend_app = _load()
mcp_client = backend_app.mcp_client
URL = "/api/destinations/mcp-search"

TOKYO = {"destination_id": 1, "city": "Tokyo", "country": "Japan", "travel_style": "City break",
         "average_daily_cost": 150.0, "recommended_trip_length": 6, "description": "d", "categories": []}


class Resp:
    def __init__(self, status=200, payload=None, text=""):
        self.status_code = status
        self._payload = payload
        self.text = text

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


def rpc_ok(destinations=(TOKYO,), filters=None, count=None):
    destinations = list(destinations)
    return {"jsonrpc": "2.0", "id": 1, "result": {"isError": False, "content": [], "structuredContent": {
        "source": "destination-database", "filters": filters or {}, "count": len(destinations) if count is None else count,
        "destinations": destinations}}}


@pytest.fixture
def client():
    backend_app.app.testing = True
    return backend_app.app.test_client()


@pytest.fixture
def post(monkeypatch):
    calls = []
    state = {"resp": Resp(200, rpc_ok()), "exc": None}

    def fake_post(url, json=None, headers=None, timeout=None, **kw):
        calls.append({"url": url, "json": json, "headers": headers, "timeout": timeout})
        if state["exc"]:
            raise state["exc"]
        return state["resp"]

    monkeypatch.setattr(mcp_client.requests, "post", fake_post)
    monkeypatch.setattr(mcp_client, "MCP_ENABLED", True)
    state["calls"] = calls
    return state


def test_success_sends_tools_call_and_returns_envelope(client, post):
    post["resp"] = Resp(200, rpc_ok(filters={"country": "Japan"}))
    r = client.post(URL, json={"country": " Japan ", "city": "  "})
    assert r.status_code == 200
    body = r.get_json()
    assert body["status"] == "success" and body["count"] == 1 and body["destinations"][0]["city"] == "Tokyo"
    call = post["calls"][0]
    assert call["url"].endswith("/mcp")
    assert call["json"]["method"] == "tools/call"
    assert call["json"]["params"] == {"name": "list_destinations", "arguments": {"country": "Japan"}}
    assert "text/event-stream" in call["headers"]["Accept"]


@pytest.mark.parametrize("body", [
    {"city": "x" * 101}, {"city": "To\x00kyo"}, {"city": 5}, {"planet": "Mars"}, ["Tokyo"],
])
def test_validation_rejected_without_outbound_call(client, post, body):
    r = client.post(URL, json=body)
    assert r.status_code == 400 and r.get_json()["status"] == "invalid_request"
    assert post["calls"] == []


def test_disabled(client, post, monkeypatch):
    monkeypatch.setattr(mcp_client, "MCP_ENABLED", False)
    r = client.post(URL, json={})
    assert r.status_code == 503 and r.get_json()["status"] == "disabled"
    assert post["calls"] == []


@pytest.mark.parametrize("exc,code,status", [
    (requests.exceptions.Timeout("slow"), 504, "timeout"),
    (requests.exceptions.ConnectionError("refused"), 503, "unavailable"),
])
def test_timeout_and_unavailable(client, post, exc, code, status):
    post["exc"] = exc
    r = client.post(URL, json={})
    assert r.status_code == code and r.get_json()["status"] == status
    assert "refused" not in r.get_data(as_text=True) and "slow" not in r.get_data(as_text=True)


@pytest.mark.parametrize("code", [421, 500])
def test_http_error_is_unavailable(client, post, code):
    post["resp"] = Resp(code, {"x": 1})
    r = client.post(URL, json={})
    assert r.status_code == 503 and r.get_json()["status"] == "unavailable"


def test_tool_error_is_safe(client, post):
    raw = "destination-db unavailable: HTTPConnectionPool(host='10.0.0.5')"
    post["resp"] = Resp(200, {"result": {"isError": True, "content": [{"type": "text", "text": raw}]}})
    r = client.post(URL, json={})
    assert r.status_code == 502 and r.get_json()["status"] == "tool_error"
    assert "10.0.0.5" not in r.get_data(as_text=True)


def test_jsonrpc_error_is_tool_error(client, post):
    post["resp"] = Resp(200, {"error": {"code": -32602, "message": "boom"}})
    r = client.post(URL, json={})
    assert r.status_code == 502 and r.get_json()["status"] == "tool_error"
    assert "boom" not in r.get_data(as_text=True)


@pytest.mark.parametrize("resp", [
    Resp(200, None),
    Resp(200, ["x"]),
    Resp(200, {"nothing": 1}),
    Resp(200, {"result": {"isError": False}}),
    Resp(200, {"result": {"isError": False, "structuredContent": {"destinations": "no", "count": 0, "filters": {}}}}),
    Resp(200, rpc_ok(count=5)),
])
def test_malformed(client, post, resp):
    post["resp"] = resp
    r = client.post(URL, json={})
    assert r.status_code == 502 and r.get_json()["status"] == "malformed"


@pytest.mark.parametrize("field,value", [
    ("average_daily_cost", "expensive"), ("average_daily_cost", True),
    ("destination_id", "1"), ("destination_id", True), ("destination_id", None),
    ("city", 5), ("city", None), ("country", ["Japan"]), ("travel_style", 3),
    ("recommended_trip_length", "6"), ("recommended_trip_length", 6.5), ("recommended_trip_length", False),
    ("description", {}), ("categories", "food"), ("categories", [1, 2]), ("categories", None),
])
def test_malformed_destination_row_is_not_success(client, post, field, value):
    bad = dict(TOKYO, **{field: value})
    post["resp"] = Resp(200, rpc_ok(destinations=[TOKYO, bad]))
    r = client.post(URL, json={})
    assert r.status_code == 502 and r.get_json()["status"] == "malformed"
    assert "destinations" not in r.get_json() and "expensive" not in r.get_data(as_text=True)


@pytest.mark.parametrize("key", [
    "destination_id", "city", "country", "description", "average_daily_cost",
    "recommended_trip_length", "travel_style", "categories",
])
def test_missing_destination_key_is_malformed(client, post, key):
    bad = {k: v for k, v in TOKYO.items() if k != key}
    post["resp"] = Resp(200, rpc_ok(destinations=[TOKYO, bad]))
    r = client.post(URL, json={})
    assert r.status_code == 502 and r.get_json()["status"] == "malformed"
    assert "destinations" not in r.get_json()


@pytest.mark.parametrize("cost", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_cost_is_malformed(client, post, cost):
    post["resp"] = Resp(200, rpc_ok(destinations=[dict(TOKYO, average_daily_cost=cost)]))
    r = client.post(URL, json={})
    assert r.status_code == 502 and r.get_json()["status"] == "malformed"


HUGE_INT = 10 ** 400  # float(HUGE_INT) raises OverflowError


def test_huge_int_cost_is_malformed(client, post):
    post["resp"] = Resp(200, rpc_ok(destinations=[dict(TOKYO, average_daily_cost=HUGE_INT)]))
    r = client.post(URL, json={})
    assert r.status_code == 502 and r.get_json()["status"] == "malformed"
    assert "destinations" not in r.get_json()


@pytest.mark.parametrize("source", ["other-service", None, 7])
def test_unexpected_source_is_malformed(client, post, source):
    resp = rpc_ok()
    resp["result"]["structuredContent"]["source"] = source
    post["resp"] = Resp(200, resp)
    assert client.post(URL, json={}).get_json()["status"] == "malformed"


def test_missing_source_is_malformed(client, post):
    resp = rpc_ok()
    del resp["result"]["structuredContent"]["source"]
    post["resp"] = Resp(200, resp)
    assert client.post(URL, json={}).get_json()["status"] == "malformed"


@pytest.mark.parametrize("bad_cost", ["missing", HUGE_INT])
def test_frontend_to_backend_bad_cost_renders_controlled_alert(client, monkeypatch, bad_cost):
    """Real frontend -> real backend route -> real mcp_client; only the shared MCP HTTP call is faked."""
    fe_path = os.path.join(os.path.dirname(BACKEND_DIR), "frontend", "app.py")
    spec = importlib.util.spec_from_file_location("destination_frontend_integration_app", fe_path)
    fe = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fe)
    fe.app.testing = True

    if bad_cost == "missing":
        bad = {k: v for k, v in TOKYO.items() if k != "average_daily_cost"}
    else:
        bad = dict(TOKYO, average_daily_cost=bad_cost)

    def fake_post(url, json=None, headers=None, timeout=None, **kw):
        if url.endswith(mcp_client.MCP_PATH):
            return Resp(200, rpc_ok(destinations=[bad]))
        backend_resp = client.post(url[url.index("/api/"):], json=json)
        return Resp(backend_resp.status_code, backend_resp.get_json())

    monkeypatch.setattr(requests, "post", fake_post)
    monkeypatch.setattr(mcp_client, "MCP_ENABLED", True)
    r = fe.app.test_client().post("/mcp-lookup", data={"country": "Japan"}, headers={"HX-Request": "true"})
    html = r.get_data(as_text=True)
    assert r.status_code == 200
    assert "The shared service returned an unexpected response." in html
    assert "Tokyo" not in html and "UndefinedError" not in html and "OverflowError" not in html and "Traceback" not in html


def test_default_url_is_shared_mcp_port():
    assert mcp_client.MCP_SERVICE_URL.endswith(":7001") or "MCP_SERVICE_URL" in os.environ
    assert "7003" not in mcp_client.MCP_SERVICE_URL


def _fresh_mcp_client(monkeypatch, value):
    monkeypatch.setenv("MCP_ENABLED", value)
    spec = importlib.util.spec_from_file_location(
        "destination_mcp_client_env", os.path.join(BACKEND_DIR, "mcp_client.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("value", ["false", "0", "no", "off", "FALSE", "Off", "", "garbage"])
def test_enabled_flag_false_spellings_disable(monkeypatch, value):
    assert _fresh_mcp_client(monkeypatch, value).MCP_ENABLED is False


@pytest.mark.parametrize("value", ["true", "1", "yes", "on", "TRUE", " true "])
def test_enabled_flag_true_spellings_enable(monkeypatch, value):
    assert _fresh_mcp_client(monkeypatch, value).MCP_ENABLED is True


def test_enabled_flag_unset_defaults_to_enabled(monkeypatch):
    monkeypatch.delenv("MCP_ENABLED", raising=False)
    spec = importlib.util.spec_from_file_location(
        "destination_mcp_client_env", os.path.join(BACKEND_DIR, "mcp_client.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.MCP_ENABLED is True
