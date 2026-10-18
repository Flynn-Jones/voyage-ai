"""Activity's five read-only tools on the shared MCP server.

Ported from activity-service/tests/test_mcp_tools.py when activity-service's
own MCP server was retired. The activity database-service is faked by
patching tools.requests.get. Covers the tool functions, the FastMCP registry,
and BOTH request formats served on :7001 at once: JSON-RPC over /mcp
(Destination's mcp_client.py: MCP_PATH + MCP_HEADERS) and the POST /<tool>
compatibility shim (Activity's and Budget's clients).
"""
import json

import pytest
import requests
from mcp.shared.memory import create_connected_server_and_client_session

import tools
from server import mcp

TOOL_NAMES = ["list_activities", "get_activity", "get_activity_assignments", "list_assignments", "get_assignment"]
WRITE_TOOL_NAMES = ["add_activity", "edit_activity", "delete_activity"]
# Exactly what student-1/backend/mcp_client.py sends.
MCP_PATH = "/mcp"
MCP_HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}

ACTIVITIES = [
    {"activity_id": 4, "activity_name": "Harbour Kayaking", "activity_type": "Adventure",
     "activity_cost": 45.0, "duration": "2 hours"},
    {"activity_id": 5, "activity_name": "Aquarium Visit", "activity_type": "Sightseeing",
     "activity_cost": 30.0, "duration": "3 hours"},
]
ASSIGNMENTS = [{"assignment_id": 9, "activity_id": 4, "assignment_time": "2026-10-02 09:00"}]


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


def fake_database(activities=ACTIVITIES, assignments=ASSIGNMENTS):
    """A fake activity database-service keyed on URL path, mirroring
    db_routes.py (bare lists/objects, 404 + {"error"} when missing)."""
    calls = []

    def fake_get(url, params=None, timeout=None):
        assert url.startswith(tools.ACTIVITY_DB_URL), f"activity tool left ACTIVITY_DB_URL: {url}"
        path = url[len(tools.ACTIVITY_DB_URL):]
        calls.append(path)
        parts = path.strip("/").split("/")
        if path == "/activities":
            return DummyResponse(200, activities)
        if path == "/assignments":
            return DummyResponse(200, assignments)
        if parts[0] == "activities":
            activity = next((a for a in activities if a["activity_id"] == int(parts[1])), None)
            if activity is None:
                return DummyResponse(404, {"error": "activity not found"})
            if len(parts) == 3:
                return DummyResponse(200, [s for s in assignments if s["activity_id"] == activity["activity_id"]])
            return DummyResponse(200, activity)
        if parts[0] == "assignments":
            assignment = next((s for s in assignments if s["assignment_id"] == int(parts[1])), None)
            if assignment is None:
                return DummyResponse(404, {"error": "assignment not found"})
            return DummyResponse(200, assignment)
        raise AssertionError(f"unexpected database call: {path}")

    fake_get.calls = calls
    return fake_get


@pytest.fixture
def db(monkeypatch):
    fake = fake_database()
    monkeypatch.setattr(tools.requests, "get", fake)
    return fake


@pytest.fixture
def empty_db(monkeypatch):
    fake = fake_database(activities=[], assignments=[])
    monkeypatch.setattr(tools.requests, "get", fake)
    return fake


# ---------------------------------------------------------------- tool functions

def test_activity_db_url_defaults_to_activity_database_port():
    assert tools.ACTIVITY_DB_URL == "http://localhost:6003"


def test_list_activities_success(db):
    assert tools.list_activities() == {"count": 2, "activities": ACTIVITIES}


def test_get_activity_success(db):
    assert tools.get_activity(4) == {"activity": ACTIVITIES[0]}
    assert db.calls == ["/activities/4"]


def test_get_activity_assignments_success(db):
    assert tools.get_activity_assignments(4) == {"activity_id": 4, "count": 1, "assignments": ASSIGNMENTS}


def test_list_assignments_success(db):
    assert tools.list_assignments() == {"count": 1, "assignments": ASSIGNMENTS}


def test_get_assignment_success(db):
    assert tools.get_assignment(9) == {"assignment": ASSIGNMENTS[0]}


def test_numeric_string_id_is_accepted(db):
    assert tools.get_activity("4") == {"activity": ACTIVITIES[0]}


def test_empty_lists(empty_db):
    assert tools.list_activities() == {"count": 0, "activities": []}
    assert tools.list_assignments() == {"count": 0, "assignments": []}


def test_not_found(db):
    assert tools.get_activity(999) == {"error": "activity not found", "activity_id": 999}
    assert tools.get_activity_assignments(999) == {"error": "activity not found", "activity_id": 999}
    assert tools.get_assignment(999) == {"error": "assignment not found", "assignment_id": 999}


def test_existing_activity_with_none_scheduled_is_empty_not_error(db):
    assert tools.get_activity_assignments(5) == {"activity_id": 5, "count": 0, "assignments": []}


@pytest.mark.parametrize("tool_name,argument", [
    ("get_activity", "activity_id"),
    ("get_activity_assignments", "activity_id"),
    ("get_assignment", "assignment_id"),
])
@pytest.mark.parametrize("bad_id", [None, "", "abc", 0, -1, "1.5", 2.5, True, "4; DROP TABLE"])
def test_id_tools_reject_invalid_ids_without_calling_database(db, tool_name, argument, bad_id):
    assert getattr(tools, tool_name)(bad_id) == {"error": f"{argument} must be a positive integer"}
    assert db.calls == []


@pytest.mark.parametrize("tool_name,args", [
    ("list_activities", ()), ("get_activity", (4,)), ("get_activity_assignments", (4,)),
    ("list_assignments", ()), ("get_assignment", (9,)),
])
def test_tools_report_database_unreachable(monkeypatch, tool_name, args):
    def unreachable(url, params=None, timeout=None):
        raise requests.exceptions.ConnectionError("connection refused")

    monkeypatch.setattr(tools.requests, "get", unreachable)
    assert getattr(tools, tool_name)(*args)["error"].startswith("activity database unavailable")


def test_tools_report_database_500(monkeypatch):
    monkeypatch.setattr(tools.requests, "get", lambda url, params=None, timeout=None: DummyResponse(500, {}))
    assert tools.list_activities()["error"].startswith("activity database unavailable")


# ---------------------------------------------------------------- registry

async def test_registry_has_exactly_the_read_only_activity_tools():
    async with create_connected_server_and_client_session(mcp) as session:
        listed = {t.name: t for t in (await session.list_tools()).tools}
    assert set(TOOL_NAMES) <= set(listed)
    assert not set(WRITE_TOOL_NAMES) & set(listed)
    for name in TOOL_NAMES:
        assert "Read-only" in listed[name].description
        assert listed[name].annotations.readOnlyHint is True
    schema = listed["get_activity"].inputSchema
    assert schema["required"] == ["activity_id"]
    assert schema["properties"]["activity_id"]["type"] == "integer"


async def test_registry_call_get_activity_assignments(db):
    async with create_connected_server_and_client_session(mcp) as session:
        result = await session.call_tool("get_activity_assignments", {"activity_id": 4})
    assert not result.isError
    assert result.structuredContent == {"activity_id": 4, "count": 1, "assignments": ASSIGNMENTS}


# ---------------------------------------------------------------- both HTTP formats at once

def rpc(client, name, arguments, host=None):
    headers = dict(MCP_HEADERS, **({"Host": host} if host else {}))
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": arguments}}
    return client.post(MCP_PATH, headers=headers, json=body)


def test_health_lists_activity_tools(client):
    assert set(TOOL_NAMES) <= set(client.get("/health").json()["tools"])


def test_destination_style_jsonrpc_and_activity_style_shim_on_same_server(client, db, monkeypatch):
    # Destination's format: JSON-RPC tools/call on /mcp, via host.docker.internal.
    resp = rpc(client, "get_activity", {"activity_id": 4}, host="host.docker.internal:7001")
    assert resp.status_code == 200
    rpc_result = resp.json()["result"]
    assert rpc_result["isError"] is False
    assert rpc_result["structuredContent"] == {"activity": ACTIVITIES[0]}

    # Activity's/Budget's format: POST /<tool> on the same app, same registry.
    shim = client.post("/get_activity", json={"activity_id": 4})
    assert shim.status_code == 200
    assert shim.json() == {"status": "success", "result": {"activity": ACTIVITIES[0]}}

    # And an existing tool is still served through /mcp next to the new ones.
    monkeypatch.setattr(tools.requests, "get", lambda url, params=None, timeout=None: DummyResponse(
        200, [{"destination_id": 1, "city": "Tokyo", "country": "Japan"}]))
    dest = rpc(client, "list_destinations", {})
    assert dest.status_code == 200 and dest.json()["result"]["structuredContent"]["count"] == 1


def test_shim_activity_not_found_is_502_with_result_error(client, db):
    # activity-service/backend/routes/mcp_mode.py turns this into its own 404.
    resp = client.post("/get_assignment", json={"assignment_id": 999})
    assert resp.status_code == 502
    assert resp.json() == {"status": "error",
                           "result": {"error": "assignment not found", "assignment_id": 999}}


def test_shim_activity_db_down_is_502(client, monkeypatch):
    def unreachable(url, params=None, timeout=None):
        raise requests.exceptions.ConnectionError("connection refused")

    monkeypatch.setattr(tools.requests, "get", unreachable)
    resp = client.post("/list_activities", json={})
    assert resp.status_code == 502
    assert resp.json()["result"]["error"].startswith("activity database unavailable")


def test_mcp_rejects_non_integer_id_before_tool_runs(client, db):
    resp = rpc(client, "get_activity", {"activity_id": "the kayaking one"})
    assert resp.status_code == 200
    assert resp.json()["result"]["isError"] is True
    assert db.calls == []
