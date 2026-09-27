"""Frontend tests for the MCP Mode tab (/mcp-mode) against a mocked backend.

Run with the rest of the frontend suite:
    python -m pytest activity-service/frontend/tests
"""
from conftest import make_recording_backend


def post_mcp_call(app_module, monkeypatch, form, status_code, payload):
    calls = []
    monkeypatch.setattr(app_module.requests, "request", make_recording_backend(calls, status_code, payload))
    with app_module.create_app().test_client() as test_client:
        resp = test_client.post("/mcp-mode/call", data=form)
    return resp, calls


def test_mcp_mode_200_lists_every_tool(client, app_module):
    resp = client.get("/mcp-mode")
    assert resp.status_code == 200
    for tool in app_module.MCP_TOOLS:
        assert tool["name"].encode() in resp.data


def test_home_page_links_to_mcp_mode(client):
    resp = client.get("/")
    assert b'href="/mcp-mode">MCP Mode</a>' in resp.data


def test_mcp_tool_call_forwards_argument_and_renders_result(app_module, monkeypatch):
    resp, calls = post_mcp_call(app_module, monkeypatch, {"kind": "tool", "tool": "get_activity", "record_id": "4"},
                                200, {"status": "success", "tool": "get_activity", "arguments": {"activity_id": 4},
                                      "result": {"activity": {"activity_name": "Harbour Kayaking"}}})
    assert resp.status_code == 200
    assert b"get_activity" in resp.data and b"HTTP 200" in resp.data and b"Harbour Kayaking" in resp.data
    assert calls[0]["url"].endswith("/api/activity/mcp/activity")
    assert calls[0]["json"] == {"activity_id": "4"}
    assert calls[0]["headers"] == {"X-MCP-Mode": "on"}


def test_mcp_tool_without_argument_sends_empty_body(app_module, monkeypatch):
    _, calls = post_mcp_call(app_module, monkeypatch, {"kind": "tool", "tool": "list_activities", "record_id": "9"},
                             200, {"status": "success", "result": {"count": 0, "activities": []}})
    assert calls[0]["url"].endswith("/api/activity/mcp/list-activities")
    assert calls[0]["json"] == {}


def test_mcp_ask_card_shows_selected_tool_and_model_output(app_module, monkeypatch):
    resp, calls = post_mcp_call(app_module, monkeypatch, {"kind": "ask", "message": "When is activity 4?"}, 200, {
        "status": "success", "tool": "get_activity_assignments", "arguments": {"activity_id": 4},
        "result": {"activity_id": 4, "count": 0, "assignments": []},
        "model_output": '{"tool": "get_activity_assignments", "arguments": {"activity_id": 4}}',
    })
    assert b"get_activity_assignments" in resp.data and b"When is activity 4?" in resp.data
    assert b"Raw model tool selection" in resp.data
    assert calls[0]["url"].endswith("/api/activity/mcp/ask")
    assert calls[0]["json"] == {"message": "When is activity 4?"}


def test_mcp_ask_no_tool_called_shows_reason(app_module, monkeypatch):
    resp, _ = post_mcp_call(app_module, monkeypatch, {"kind": "ask", "message": "delete everything"}, 200, {
        "status": "no_tool_called", "error": "no registered tool matches this request", "tool": None,
        "arguments": {}, "model_output": '{"tool": "none"}',
    })
    assert b"no tool called" in resp.data
    assert b"no registered tool matches this request" in resp.data


def test_mcp_backend_error_is_shown_on_card(app_module, monkeypatch):
    resp, _ = post_mcp_call(app_module, monkeypatch, {"kind": "tool", "tool": "list_assignments"}, 403,
                            {"status": "error", "error": "MCP mode is disabled."})
    assert resp.status_code == 200  # card swapped in; the 403 is shown on it
    assert b"MCP mode is disabled." in resp.data and b"HTTP 403" in resp.data


def test_mcp_empty_ask_is_not_forwarded(app_module, monkeypatch):
    resp, calls = post_mcp_call(app_module, monkeypatch, {"kind": "ask", "message": "  "}, 200, {})
    assert resp.status_code == 204
    assert calls == []


def test_mcp_unknown_tool_is_not_forwarded(app_module, monkeypatch):
    resp, calls = post_mcp_call(app_module, monkeypatch, {"kind": "tool", "tool": "delete_activity"}, 200, {})
    assert b"Unknown tool" in resp.data
    assert calls == []


def test_mcp_backend_unreachable_shows_error_card(app_module, monkeypatch):
    def boom(method, url, params=None, json=None, timeout=None, headers=None):
        raise app_module.requests.exceptions.RequestException("connection refused")

    monkeypatch.setattr(app_module.requests, "request", boom)
    with app_module.create_app().test_client() as test_client:
        resp = test_client.post("/mcp-mode/call", data={"kind": "tool", "tool": "list_activities"})
    assert resp.status_code == 200
    assert b"no response" in resp.data and b"connection refused" in resp.data
