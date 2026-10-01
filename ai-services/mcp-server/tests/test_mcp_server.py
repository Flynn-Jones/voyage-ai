"""Tests for the shared MCP server: registry, list_destinations, /mcp, compat shim.

The network is mocked by patching tools.requests.get.
"""
import pytest
import requests
from mcp.shared.memory import create_connected_server_and_client_session
from starlette.testclient import TestClient

import mcp_http_server
import tools
from server import mcp

REQUIRED_TOOLS = {
    "list_expenses",
    "get_accommodation_by_destination",
    "project_files",
    "ci_report",
    "list_destinations",
}
ROWS = [
    {"destination_id": 1, "city": "Tokyo", "country": "Japan", "description": "d",
     "average_daily_cost": 150.0, "recommended_trip_length": 7, "travel_style": "city",
     "categories": ["food"]},
]
MCP_HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.exceptions.HTTPError(str(self.status_code))


@pytest.fixture
def fake_get(monkeypatch):
    calls = []
    state = {"response": FakeResponse(ROWS), "exc": None}

    def _get(url, params=None, timeout=None):
        calls.append({"url": url, "params": params, "timeout": timeout})
        if state["exc"]:
            raise state["exc"]
        return state["response"]

    monkeypatch.setattr(tools.requests, "get", _get)
    return calls, state


async def call(name, args):
    async with create_connected_server_and_client_session(mcp) as session:
        return await session.call_tool(name, args)


async def test_registry_lists_required_tools():
    async with create_connected_server_and_client_session(mcp) as session:
        names = {t.name for t in (await session.list_tools()).tools}
    assert REQUIRED_TOOLS <= names


async def test_list_destinations_happy_path(fake_get):
    calls, _ = fake_get
    result = await call("list_destinations", {"country": "Japan"})
    assert not result.isError
    sc = result.structuredContent
    assert sc["source"] == "destination-database"
    assert sc["count"] == len(ROWS) and sc["destinations"] == ROWS
    assert sc["filters"] == {"country": "Japan"}
    assert calls[0]["url"] == "http://localhost:6001/destinations"
    assert calls[0]["params"] == {"country": "Japan"}


async def test_list_destinations_no_filters_and_empty(fake_get):
    calls, state = fake_get
    state["response"] = FakeResponse([])
    result = await call("list_destinations", {"city": "  "})
    assert not result.isError
    assert result.structuredContent["count"] == 0
    assert calls[0]["params"] == {}


@pytest.mark.parametrize("args", [
    {"city": "x" * 101},
    {"city": "Tok\x00yo"},
    {"country": "Ja\npan"},
    {"travel_style": 5},
])
async def test_list_destinations_validation_rejects_before_http(fake_get, args):
    calls, _ = fake_get
    result = await call("list_destinations", args)
    assert result.isError
    assert calls == []


async def test_list_destinations_accepts_non_ascii(fake_get):
    calls, _ = fake_get
    result = await call("list_destinations", {"city": "São Paulo"})
    assert not result.isError
    assert calls[0]["params"] == {"city": "São Paulo"}


@pytest.mark.parametrize("setup,message", [
    (lambda s: s.update(exc=requests.exceptions.ConnectionError("down")), "destination-db unavailable"),
    (lambda s: s.update(response=FakeResponse({}, 500)), "destination-db returned 500"),
    (lambda s: s.update(response=FakeResponse({"a": 1})), "unexpected payload"),
    (lambda s: s.update(response=FakeResponse(ValueError("bad"))), "unexpected payload"),
])
async def test_list_destinations_upstream_failures(fake_get, setup, message):
    _, state = fake_get
    setup(state)
    result = await call("list_destinations", {})
    assert result.isError
    assert message in result.content[0].text


async def test_project_files_regression():
    result = await call("project_files", {"directory_path": "."})
    assert not result.isError
    assert "ai-services" in result.structuredContent["items"]


@pytest.fixture(scope="module")
def client():
    with TestClient(mcp_http_server.create_app(), base_url="http://localhost:7001") as c:
        yield c


def rpc(client, method, params=None, id_=1):
    return client.post("/mcp", headers=MCP_HEADERS,
                       json={"jsonrpc": "2.0", "id": id_, "method": method, "params": params or {}})


def test_streamable_http_tools_list_and_call(client, fake_get):
    listed = rpc(client, "tools/list")
    assert listed.status_code == 200
    names = {t["name"] for t in listed.json()["result"]["tools"]}
    assert REQUIRED_TOOLS <= names

    called = rpc(client, "tools/call", {"name": "list_destinations", "arguments": {"city": "Tokyo"}})
    assert called.status_code == 200
    result = called.json()["result"]
    assert result["isError"] is False
    assert result["structuredContent"]["count"] == 1


def test_streamable_http_accepts_docker_host_header(client):
    resp = client.post("/mcp", headers={**MCP_HEADERS, "Host": "host.docker.internal:7001"},
                       json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})
    assert resp.status_code == 200


def test_health(client):
    body = client.get("/health").json()
    assert body["status"] == "ok" and body["mcp_path"] == "/mcp"
    assert REQUIRED_TOOLS <= set(body["tools"])


def test_shim_success_unknown_badjson_error(client, monkeypatch):
    monkeypatch.setattr(tools.requests, "get", lambda *a, **k: FakeResponse([{"expense_id": 1}]))
    ok = client.post("/list_expenses", json={})
    assert ok.status_code == 200
    assert ok.json()["status"] == "success" and ok.json()["result"]["count"] == 1

    assert client.post("/nope", json={}).status_code == 404
    assert client.post("/list_expenses", content=b"{not json").status_code == 400
    assert client.post("/list_expenses", json=[1]).status_code == 400

    bad = client.post("/get_accommodation_by_destination", json={"destination": ""})
    assert bad.status_code == 502 and bad.json()["status"] == "error"


def test_shim_destination_error_is_502(client, fake_get):
    _, state = fake_get
    state["exc"] = requests.exceptions.ConnectionError("down")
    resp = client.post("/list_destinations", json={})
    assert resp.status_code == 502
    assert "destination-db unavailable" in resp.json()["error"]
