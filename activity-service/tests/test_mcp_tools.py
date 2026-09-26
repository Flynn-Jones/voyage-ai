"""Tests for activity-service's MCP integration.

Most tests call the functions in mcp-server/tools.py directly (not through
any MCP transport), with requests.get replaced by a fake database-service.
The remaining groups check that both transports register exactly those
tools, and that backend/routes/mcp_mode.py validates, gates, and forwards
calls correctly — including /ask's handling of bad model output.

Run from activity-service/ with: python -m pytest tests/test_mcp_tools.py
"""
import asyncio
import json
import sys
from pathlib import Path

import pytest
import requests

ACTIVITY_SERVICE_ROOT = Path(__file__).resolve().parent.parent
for path in (ACTIVITY_SERVICE_ROOT / "mcp-server", ACTIVITY_SERVICE_ROOT / "backend"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import mcp_http_server  # noqa: E402
import tools as mcp_tools  # noqa: E402

TOOL_NAMES = ["list_activities", "get_activity", "get_activity_assignments", "list_assignments", "get_assignment"]
WRITE_TOOL_NAMES = ["add_activity", "edit_activity", "delete_activity"]

ACTIVITIES = [
    {"activity_id": 4, "activity_name": "Harbour Kayaking", "activity_type": "Adventure",
     "activity_cost": 45.0, "duration": "2 hours"},
    {"activity_id": 5, "activity_name": "Aquarium Visit", "activity_type": "Sightseeing",
     "activity_cost": 30.0, "duration": "3 hours"},
]
ASSIGNMENTS = [{"assignment_id": 9, "activity_id": 4, "assignment_time": "2026-10-02 09:00"}]


class DummyResponse:
    """Minimal stand-in for requests.Response, matching the pattern already
    used in backend/tests/conftest.py."""

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
    """A fake database-service keyed on URL path, mirroring db_routes.py's
    real responses (bare lists/objects, 404 + {"error"} when missing)."""
    calls = []

    def fake_get(url, timeout=None):
        path = url.replace(mcp_tools.ACTIVITY_DB_URL, "")
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
    monkeypatch.setattr(mcp_tools.requests, "get", fake)
    return fake


@pytest.fixture
def empty_db(monkeypatch):
    fake = fake_database(activities=[], assignments=[])
    monkeypatch.setattr(mcp_tools.requests, "get", fake)
    return fake


# ---------------------------------------------------------------------------
# tools.py: success paths
# ---------------------------------------------------------------------------

def test_list_activities_success(db):
    result = mcp_tools.list_activities()
    assert result == {"count": 2, "activities": ACTIVITIES}


def test_get_activity_success(db):
    result = mcp_tools.get_activity(4)
    assert result == {"activity": ACTIVITIES[0]}
    assert db.calls == ["/activities/4"]


def test_get_activity_assignments_success(db):
    result = mcp_tools.get_activity_assignments(4)
    assert result == {"activity_id": 4, "count": 1, "assignments": ASSIGNMENTS}


def test_list_assignments_success(db):
    result = mcp_tools.list_assignments()
    assert result == {"count": 1, "assignments": ASSIGNMENTS}


def test_get_assignment_success(db):
    result = mcp_tools.get_assignment(9)
    assert result == {"assignment": ASSIGNMENTS[0]}


def test_numeric_string_id_is_accepted(db):
    # MCP/HTTP clients may send "4" — it's an unambiguous positive integer.
    assert mcp_tools.get_activity("4") == {"activity": ACTIVITIES[0]}


# ---------------------------------------------------------------------------
# tools.py: not-found / empty paths
# ---------------------------------------------------------------------------

def test_list_activities_empty(empty_db):
    assert mcp_tools.list_activities() == {"count": 0, "activities": []}


def test_list_assignments_empty(empty_db):
    assert mcp_tools.list_assignments() == {"count": 0, "assignments": []}


def test_get_activity_not_found(db):
    assert mcp_tools.get_activity(999) == {"error": "activity not found", "activity_id": 999}


def test_get_activity_assignments_not_found(db):
    assert mcp_tools.get_activity_assignments(999) == {"error": "activity not found", "activity_id": 999}


def test_get_activity_assignments_existing_activity_with_none_scheduled(db):
    # An activity that exists but has no schedule is an empty result, not an error.
    assert mcp_tools.get_activity_assignments(5) == {"activity_id": 5, "count": 0, "assignments": []}


def test_get_assignment_not_found(db):
    assert mcp_tools.get_assignment(999) == {"error": "assignment not found", "assignment_id": 999}


# ---------------------------------------------------------------------------
# tools.py: invalid input rejected before any database call
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("tool_name,argument", [
    ("get_activity", "activity_id"),
    ("get_activity_assignments", "activity_id"),
    ("get_assignment", "assignment_id"),
])
@pytest.mark.parametrize("bad_id", [None, "", "abc", 0, -1, "1.5", 2.5, True, "4; DROP TABLE"])
def test_id_tools_reject_invalid_ids_without_calling_database(db, tool_name, argument, bad_id):
    result = getattr(mcp_tools, tool_name)(bad_id)
    assert result == {"error": f"{argument} must be a positive integer"}
    assert db.calls == []


# ---------------------------------------------------------------------------
# tools.py: database failures come back structured, never raised
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("tool_name,args", [
    ("list_activities", ()), ("get_activity", (4,)), ("get_activity_assignments", (4,)),
    ("list_assignments", ()), ("get_assignment", (9,)),
])
def test_tools_report_database_unreachable(monkeypatch, tool_name, args):
    def unreachable(url, timeout=None):
        raise requests.exceptions.ConnectionError("connection refused")

    monkeypatch.setattr(mcp_tools.requests, "get", unreachable)
    result = getattr(mcp_tools, tool_name)(*args)
    assert result["error"].startswith("activity database unavailable")


def test_tools_report_database_500(monkeypatch):
    monkeypatch.setattr(mcp_tools.requests, "get", lambda url, timeout=None: DummyResponse(500, {}))
    assert mcp_tools.list_activities()["error"].startswith("activity database unavailable")


# ---------------------------------------------------------------------------
# Transports: both register exactly the read-only tool set
# ---------------------------------------------------------------------------

def test_http_wrapper_registers_exactly_the_read_only_tools():
    assert sorted(mcp_http_server.TOOLS) == sorted(TOOL_NAMES)
    for name in WRITE_TOOL_NAMES:
        assert name not in mcp_http_server.TOOLS


def test_http_wrapper_dispatch_passes_arguments_through(db):
    assert mcp_http_server.TOOLS["get_assignment"]({"assignment_id": 9}) == {"assignment": ASSIGNMENTS[0]}
    assert mcp_http_server.TOOLS["get_activity"]({})["error"] == "activity_id must be a positive integer"


def test_stdio_server_registers_exactly_the_read_only_tools():
    pytest.importorskip("mcp")
    import server

    tools = asyncio.run(server.mcp.list_tools())
    assert sorted(t.name for t in tools) == sorted(TOOL_NAMES)
    assert sorted(server.AVAILABLE_TOOLS) == sorted(TOOL_NAMES)
    schemas = {t.name: t.inputSchema for t in tools}
    assert schemas["get_activity"]["required"] == ["activity_id"]
    assert schemas["get_activity"]["properties"]["activity_id"]["type"] == "integer"
    assert all("Read-only" in t.description for t in tools)


# ---------------------------------------------------------------------------
# backend/routes/mcp_mode.py: the Flask bridge
# ---------------------------------------------------------------------------

@pytest.fixture
def bridge(monkeypatch):
    """Flask test client for the backend, with the MCP HTTP server replaced by
    a recorder that answers from the real tools.py over a fake database."""
    from routes import mcp_mode

    monkeypatch.setattr(mcp_tools.requests, "get", fake_database())
    forwarded = []

    def fake_post(url, json=None, timeout=None):
        tool_name = url.rsplit("/", 1)[1]
        forwarded.append((tool_name, json))
        if tool_name not in mcp_http_server.TOOLS:
            return DummyResponse(404, {"status": "error", "error": f"unknown tool: {tool_name}"})
        result = mcp_http_server.TOOLS[tool_name](json)
        ok = "error" not in result
        return DummyResponse(200 if ok else 502, {"status": "success" if ok else "error", "result": result})

    monkeypatch.setattr(mcp_mode.requests, "post", fake_post)
    monkeypatch.setattr(mcp_mode, "MCP_ENABLED", True)

    import app as app_module
    with app_module.create_app().test_client() as client:
        client.forwarded = forwarded
        client.mcp_mode = mcp_mode
        yield client


def test_bridge_success_forwards_validated_integer_arguments(bridge):
    resp = bridge.post("/api/activity/mcp/activity", json={"activity_id": "4", "extra": "dropped"})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "success"
    assert body["result"] == {"activity": ACTIVITIES[0]}
    assert body["tool"] == "get_activity" and body["arguments"] == {"activity_id": 4}
    assert bridge.forwarded == [("get_activity", {"activity_id": 4})]


def test_bridge_list_route_success(bridge):
    resp = bridge.post("/api/activity/mcp/list-assignments")
    assert resp.status_code == 200
    assert resp.get_json()["result"]["count"] == 1


def test_bridge_not_found_is_404(bridge):
    resp = bridge.post("/api/activity/mcp/assignment", json={"assignment_id": 999})
    assert resp.status_code == 404
    assert resp.get_json()["result"]["error"] == "assignment not found"


@pytest.mark.parametrize("body", [{}, {"activity_id": "abc"}, {"activity_id": 0}, {"activity_id": -2}])
def test_bridge_invalid_id_is_400_and_not_forwarded(bridge, body):
    resp = bridge.post("/api/activity/mcp/activity-assignments", json=body)
    assert resp.status_code == 400
    assert "activity_id" in resp.get_json()["error"]
    assert bridge.forwarded == []


def test_bridge_disabled_by_header_is_403(bridge):
    resp = bridge.post("/api/activity/mcp/list-activities", headers={"X-MCP-Mode": "off"})
    assert resp.status_code == 403
    assert bridge.forwarded == []


def test_bridge_disabled_by_env_is_403(bridge, monkeypatch):
    monkeypatch.setattr(bridge.mcp_mode, "MCP_ENABLED", False)
    for route in ("list-activities", "activity", "activity-assignments", "list-assignments", "assignment", "ask"):
        assert bridge.post(f"/api/activity/mcp/{route}", json={}).status_code == 403


def test_bridge_mcp_server_unreachable_is_502(bridge, monkeypatch):
    def unreachable(url, json=None, timeout=None):
        raise requests.exceptions.ConnectionError("connection refused")

    monkeypatch.setattr(bridge.mcp_mode.requests, "post", unreachable)
    resp = bridge.post("/api/activity/mcp/list-activities")
    assert resp.status_code == 502
    assert resp.get_json()["error"].startswith("MCP server unavailable")


def test_bridge_has_no_write_routes(bridge):
    for route in ("add-activity", "edit-activity", "delete-activity"):
        assert bridge.post(f"/api/activity/mcp/{route}", json={"activity_id": 4}).status_code == 404
    assert sorted(bridge.mcp_mode.TOOL_ARGUMENTS) == sorted(TOOL_NAMES)


# ---------------------------------------------------------------------------
# /api/activity/mcp/ask: LLM tool selection
# ---------------------------------------------------------------------------

def use_model_output(monkeypatch, bridge, raw):
    from services import ai_client

    prompts = []

    def fake_generate(prompt, temperature=None, model=None):
        prompts.append(prompt)
        return raw

    monkeypatch.setattr(ai_client, "_generate", fake_generate)
    return prompts


def test_ask_valid_selection_calls_the_tool(bridge, monkeypatch):
    prompts = use_model_output(monkeypatch, bridge, '{"tool": "get_activity_assignments", "arguments": {"activity_id": 4}}')
    resp = bridge.post("/api/activity/mcp/ask", json={"message": "When is activity 4 scheduled?"})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["tool"] == "get_activity_assignments"
    assert body["arguments"] == {"activity_id": 4}
    assert body["result"]["assignments"] == ASSIGNMENTS
    assert bridge.forwarded == [("get_activity_assignments", {"activity_id": 4})]
    assert prompts[0].endswith("User request: When is activity 4 scheduled?")


def test_ask_accepts_markdown_fenced_json(bridge, monkeypatch):
    use_model_output(monkeypatch, bridge, '```json\n{"tool": "list_activities", "arguments": {}}\n```')
    resp = bridge.post("/api/activity/mcp/ask", json={"message": "show everything"})
    assert resp.status_code == 200
    assert resp.get_json()["tool"] == "list_activities"


@pytest.mark.parametrize("raw,reason", [
    ("Sure! You should use list_activities.", "could not be read as JSON"),
    ('["list_activities"]', "not a JSON object"),
    ('{"tool": "delete_activity", "arguments": {"activity_id": 3}}', "unknown tool"),
    ('{"tool": "run_sql", "arguments": {}}', "unknown tool"),
    ('{"tool": "none", "arguments": {}}', "no registered tool matches"),
    ('{"tool": "get_activity", "arguments": {}}', "activity_id is required"),
    ('{"tool": "get_activity", "arguments": {"activity_id": "the kayaking one"}}', "activity_id is required"),
    ('{"tool": "get_activity", "arguments": "4"}', "arguments were not a JSON object"),
])
def test_ask_bad_model_output_calls_no_tool(bridge, monkeypatch, raw, reason):
    use_model_output(monkeypatch, bridge, raw)
    resp = bridge.post("/api/activity/mcp/ask", json={"message": "anything"})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "no_tool_called"
    assert reason in body["error"]
    assert body["model_output"] == raw
    assert bridge.forwarded == []


def test_ask_missing_message_is_400(bridge):
    assert bridge.post("/api/activity/mcp/ask", json={"message": "  "}).status_code == 400


def test_ask_model_unreachable_is_502(bridge, monkeypatch):
    from services import ai_client

    def unreachable(prompt, temperature=None, model=None):
        raise ai_client.AIServiceError("connection refused")

    monkeypatch.setattr(ai_client, "_generate", unreachable)
    resp = bridge.post("/api/activity/mcp/ask", json={"message": "list activities"})
    assert resp.status_code == 502
    assert resp.get_json()["error"].startswith("AI tool selection unavailable")
    assert bridge.forwarded == []


def test_tool_selection_prompt_lists_every_registered_tool(bridge):
    prompt = bridge.mcp_mode.load_tool_selection_prompt()
    for name in TOOL_NAMES:
        assert name in prompt
