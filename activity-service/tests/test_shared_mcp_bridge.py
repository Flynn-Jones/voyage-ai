"""Activity backend -> the ONE shared MCP server (ai-services/mcp-server, :7001).

Replaces the bridge half of the retired tests/test_mcp_tools.py (the tool
half now lives in ai-services/mcp-server/tests/test_activity_tools.py).
backend/routes/mcp_mode.py's requests.post is routed into the real shared
Starlette app (its POST /<tool> shim -> FastMCP registry -> tools.py), and
only the activity database-service underneath is faked. So these tests prove
the backend speaks the shared server's actual wire contract, not a copy of it.

Run from activity-service/ with: python -m pytest tests/test_shared_mcp_bridge.py
"""
import json
import sys
from pathlib import Path

import pytest
import requests

REPO_ROOT = Path(__file__).resolve().parents[2]
ACTIVITY_SERVICE_ROOT = REPO_ROOT / "activity-service"
SHARED_MCP = REPO_ROOT / "ai-services" / "mcp-server"
for path in (SHARED_MCP, ACTIVITY_SERVICE_ROOT / "backend"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

pytest.importorskip("mcp")
from starlette.testclient import TestClient  # noqa: E402

import mcp_http_server  # noqa: E402  (shared server)
import tools as shared_tools  # noqa: E402  (shared server)
from conftest import load_backend_app  # noqa: E402

TOOL_NAMES = ["list_activities", "get_activity", "get_activity_assignments", "list_assignments", "get_assignment"]

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


def fake_database(url, params=None, timeout=None):
    path = url[len(shared_tools.ACTIVITY_DB_URL):]
    parts = path.strip("/").split("/")
    if path == "/activities":
        return DummyResponse(200, ACTIVITIES)
    if path == "/assignments":
        return DummyResponse(200, ASSIGNMENTS)
    if parts[0] == "activities":
        activity = next((a for a in ACTIVITIES if a["activity_id"] == int(parts[1])), None)
        if activity is None:
            return DummyResponse(404, {"error": "activity not found"})
        if len(parts) == 3:
            return DummyResponse(200, [s for s in ASSIGNMENTS if s["activity_id"] == activity["activity_id"]])
        return DummyResponse(200, activity)
    if parts[0] == "assignments":
        assignment = next((s for s in ASSIGNMENTS if s["assignment_id"] == int(parts[1])), None)
        if assignment is None:
            return DummyResponse(404, {"error": "assignment not found"})
        return DummyResponse(200, assignment)
    raise AssertionError(f"unexpected database call: {url}")


@pytest.fixture(scope="session")
def shared_mcp():
    # One app per session: FastMCP's session manager can only run once per instance.
    with TestClient(mcp_http_server.create_app(), base_url="http://localhost:7001") as client:
        yield client


@pytest.fixture
def bridge(monkeypatch, shared_mcp):
    from routes import mcp_mode

    monkeypatch.setattr(shared_tools.requests, "get", fake_database)
    forwarded = []

    def to_shared_server(url, json=None, timeout=None):
        assert url.startswith(mcp_mode.MCP_SERVICE_URL + "/"), url
        path = url[len(mcp_mode.MCP_SERVICE_URL):]
        forwarded.append((path.lstrip("/"), json))
        response = shared_mcp.post(path, json=json)
        return DummyResponse(response.status_code, response.json())

    monkeypatch.setattr(mcp_mode.requests, "post", to_shared_server)
    monkeypatch.setattr(mcp_mode, "MCP_ENABLED", True)

    app_module = load_backend_app()
    with app_module.create_app().test_client() as client:
        client.forwarded = forwarded
        client.mcp_mode = mcp_mode
        yield client


def test_backend_targets_the_shared_mcp_port_by_default():
    from routes import mcp_mode
    assert mcp_mode.MCP_SERVICE_URL.endswith(":7001")


def test_every_bridge_tool_is_registered_on_the_shared_server(shared_mcp, bridge):
    registered = set(shared_mcp.get("/health").json()["tools"])
    assert set(bridge.mcp_mode.TOOL_ARGUMENTS) == set(TOOL_NAMES)
    assert set(TOOL_NAMES) <= registered


@pytest.mark.parametrize("route,tool,body,expected", [
    ("list-activities", "list_activities", {}, {"count": 2, "activities": ACTIVITIES}),
    ("activity", "get_activity", {"activity_id": "4", "extra": "dropped"}, {"activity": ACTIVITIES[0]}),
    ("activity-assignments", "get_activity_assignments", {"activity_id": 4},
     {"activity_id": 4, "count": 1, "assignments": ASSIGNMENTS}),
    ("list-assignments", "list_assignments", {}, {"count": 1, "assignments": ASSIGNMENTS}),
    ("assignment", "get_assignment", {"assignment_id": 9}, {"assignment": ASSIGNMENTS[0]}),
])
def test_each_route_round_trips_through_the_shared_server(bridge, route, tool, body, expected):
    resp = bridge.post(f"/api/activity/mcp/{route}", json=body)
    assert resp.status_code == 200
    payload = resp.get_json()
    assert payload["status"] == "success" and payload["result"] == expected
    assert payload["tool"] == tool
    assert bridge.forwarded[0][0] == tool
    assert "extra" not in bridge.forwarded[0][1]


def test_not_found_from_shared_server_is_404(bridge):
    resp = bridge.post("/api/activity/mcp/assignment", json={"assignment_id": 999})
    assert resp.status_code == 404
    assert resp.get_json()["result"]["error"] == "assignment not found"


def test_activity_db_down_behind_shared_server_is_502(bridge, monkeypatch):
    def unreachable(url, params=None, timeout=None):
        raise requests.exceptions.ConnectionError("connection refused")

    monkeypatch.setattr(shared_tools.requests, "get", unreachable)
    resp = bridge.post("/api/activity/mcp/list-activities")
    assert resp.status_code == 502
    assert resp.get_json()["result"]["error"].startswith("activity database unavailable")


@pytest.mark.parametrize("body", [{}, {"activity_id": "abc"}, {"activity_id": 0}, {"activity_id": -2}])
def test_invalid_id_is_400_and_not_forwarded(bridge, body):
    resp = bridge.post("/api/activity/mcp/activity-assignments", json=body)
    assert resp.status_code == 400
    assert "activity_id" in resp.get_json()["error"]
    assert bridge.forwarded == []


def test_disabled_by_header_is_403(bridge):
    assert bridge.post("/api/activity/mcp/list-activities", headers={"X-MCP-Mode": "off"}).status_code == 403
    assert bridge.forwarded == []


def test_disabled_by_env_is_403(bridge, monkeypatch):
    monkeypatch.setattr(bridge.mcp_mode, "MCP_ENABLED", False)
    for route in ("list-activities", "activity", "activity-assignments", "list-assignments", "assignment", "ask"):
        assert bridge.post(f"/api/activity/mcp/{route}", json={}).status_code == 403


def test_shared_server_unreachable_is_502(bridge, monkeypatch):
    def unreachable(url, json=None, timeout=None):
        raise requests.exceptions.ConnectionError("connection refused")

    monkeypatch.setattr(bridge.mcp_mode.requests, "post", unreachable)
    resp = bridge.post("/api/activity/mcp/list-activities")
    assert resp.status_code == 502
    assert resp.get_json()["error"].startswith("MCP server unavailable")


def test_bridge_never_forwards_other_features_tools(bridge, monkeypatch):
    # The shared server also registers create_accommodation (a write tool) etc.;
    # the activity bridge has no route for them and /ask rejects them as unknown.
    for route in ("add-activity", "delete-activity", "create-accommodation", "list-expenses"):
        assert bridge.post(f"/api/activity/mcp/{route}", json={}).status_code == 404

    from services import ai_client
    monkeypatch.setattr(ai_client, "_generate",
                        lambda prompt, temperature=None, model=None: '{"tool": "create_accommodation", "arguments": {}}')
    resp = bridge.post("/api/activity/mcp/ask", json={"message": "add a hotel"})
    assert resp.get_json()["status"] == "no_tool_called" and "unknown tool" in resp.get_json()["error"]
    assert bridge.forwarded == []


# ---------------------------------------------------------------- /ask through the shared server

def use_model_replies(monkeypatch, *replies):
    from services import ai_client

    prompts, queue = [], list(replies)

    def fake_generate(prompt, temperature=None, model=None):
        prompts.append(prompt)
        reply = queue.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply

    monkeypatch.setattr(ai_client, "_generate", fake_generate)
    return prompts


def test_ask_selects_tool_calls_shared_server_and_answers(bridge, monkeypatch):
    prompts = use_model_replies(
        monkeypatch,
        '{"tool": "get_activity_assignments", "arguments": {"activity_id": 4}}',
        "Activity 4 is scheduled once.",
    )
    resp = bridge.post("/api/activity/mcp/ask", json={"message": "When is activity 4 scheduled?"})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["tool"] == "get_activity_assignments" and body["arguments"] == {"activity_id": 4}
    assert body["result"]["assignments"] == ASSIGNMENTS
    assert body["answer"] == "Activity 4 is scheduled once."
    assert bridge.forwarded == [("get_activity_assignments", {"activity_id": 4})]
    assert ASSIGNMENTS[0]["assignment_time"] in prompts[1]


def test_ask_answer_failure_still_returns_the_result(bridge, monkeypatch):
    from services import ai_client

    use_model_replies(monkeypatch, '{"tool": "list_activities", "arguments": {}}',
                      ai_client.AIServiceError("timed out"))
    body = bridge.post("/api/activity/mcp/ask", json={"message": "list them"}).get_json()
    assert body["answer"] is None and body["answer_error"] == "AI answer unavailable: timed out"
    assert body["result"]["count"] == 2


@pytest.mark.parametrize("raw,reason", [
    ("Sure! You should use list_activities.", "could not be read as JSON"),
    ('["list_activities"]', "not a JSON object"),
    ('{"tool": "delete_activity", "arguments": {"activity_id": 3}}', "unknown tool"),
    ('{"tool": "none", "arguments": {}}', "no registered tool matches"),
    ('{"tool": "get_activity", "arguments": {}}', "activity_id is required"),
    ('{"tool": "get_activity", "arguments": "4"}', "arguments were not a JSON object"),
])
def test_ask_bad_model_output_calls_no_tool(bridge, monkeypatch, raw, reason):
    use_model_replies(monkeypatch, raw)
    body = bridge.post("/api/activity/mcp/ask", json={"message": "anything"}).get_json()
    assert body["status"] == "no_tool_called" and reason in body["error"]
    assert bridge.forwarded == []


def test_ask_missing_message_is_400(bridge):
    assert bridge.post("/api/activity/mcp/ask", json={"message": "  "}).status_code == 400


def test_tool_selection_prompt_lists_every_activity_tool(bridge):
    prompt = bridge.mcp_mode.load_tool_selection_prompt()
    for name in TOOL_NAMES:
        assert name in prompt
