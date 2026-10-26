"""Multi-Agent Mode tab: page, run card and human decision form. The backend is faked."""
import pytest

from conftest import DummyResponse, make_recording_backend


# This file's tests use `app_module` / `client` for the frontend tier.
@pytest.fixture
def app_module(frontend_app):
    return frontend_app


@pytest.fixture
def client(frontend_client):
    return frontend_client


RUN = {
    "run_id": "run-abc",
    "workflow": "activity_schedule_check",
    "state": "awaiting_human_review",  # shape of the real create response: no "input" key
    "plan": {"objective": "Check 2026-10-06 for overlaps.",
             "steps": [{"agent": "Worker", "action": "Read activities", "tool": "list_activities"},
                       {"agent": "Human", "action": "Decide"}]},
    "work": {"items": [{"kind": "overlap", "id": "1-2", "first": "#1 Kayaking 09:00-11:00",
                        "second": "#2 Museum 10:30-12:00", "overlap_minutes": 30},
                       {"kind": "free_slot", "id": "slot-1", "start": "12:00", "end": "14:00",
                        "proposal": "Proposed: free 2h slot on 2026-10-06, 12:00-14:00 (not saved)"}],
             "scheduled": [{"assignment_id": 1, "activity_id": 1, "name": "Kayaking",
                            "time": "2026-10-06T09:00", "duration": "2 hours"}],
             "summary": "3 activities scheduled on 2026-10-06; 1 overlapping pair(s); 1 proposed 2h slot(s) "
                        "that clash with nothing.",
             "totals": {"date": "2026-10-06", "slot_hours": 2.0},
             "worker_output": "Output:\nKayaking overlaps Museum.", "worker_output_source": "llm"},
    "review": {"risk": "medium", "findings": ["#1 Kayaking 09:00-11:00 overlaps #2 by 30 min."],
               "recommendations": [], "correction": "", "retest": "", "recommendation": "Accept"},
    "human_decision": {"status": "pending", "allowed_values": ["Accept", "Partially Accept", "Reject"]},
}


def make_app(app_module, monkeypatch, calls, status_code, payload):
    monkeypatch.setattr(app_module.requests, "request", make_recording_backend(calls, status_code, payload))
    return app_module.create_app().test_client()


def test_page_is_one_day_check_with_samples_and_tab(client):
    resp = client.get("/multi-agent")
    assert resp.status_code == 200
    assert b"Multi-Agent Mode" in resp.data
    assert b'name="task"' not in resp.data  # one job, no task picker
    assert b'<input type="date" name="date" required>' in resp.data
    assert b"Find room for a 2-hour walking tour" in resp.data


def test_run_posts_form_and_renders_all_agents(app_module, monkeypatch):
    calls = []
    client = make_app(app_module, monkeypatch, calls, 201, {"status": "success", "result": RUN})
    resp = client.post("/multi-agent/run", data={"date": "2026-10-06", "slot_hours": "2"})
    assert resp.status_code == 200
    assert calls[0]["url"].endswith("/api/activity/multi-agent/workflow")
    assert calls[0]["json"] == {"date": "2026-10-06", "slot_hours": 2.0}
    for text in (b"schedule check &middot; 2026-10-06", b"2026-10-06T09:00 &middot; #1 Kayaking",
                 b"Clash: #1 Kayaking 09:00-11:00", b"Proposed: free 2h slot on 2026-10-06, 12:00-14:00",
                 b"Check 2026-10-06 for overlaps.", b"MCP:list_activities", b"Kayaking overlaps Museum",
                 b"Risk: medium", b"recommends Accept", b"Record Decision", b"run-abc"):
        assert text in resp.data


def test_run_error_is_shown_on_card(app_module, monkeypatch):
    calls = []
    client = make_app(app_module, monkeypatch, calls, 403, {"status": "error", "error": "Multi-Agent mode is disabled."})
    resp = client.post("/multi-agent/run", data={"date": "2026-10-06"})
    assert resp.status_code == 200
    assert b"Multi-Agent mode is disabled." in resp.data
    assert b"Record Decision" not in resp.data


def test_decision_sends_every_field_and_shows_record(app_module, monkeypatch):
    calls = []
    record = {"workflow_id": "run-abc", "decision": "Partially Accept", "correction": "Overlap is 25 min",
              "reason": "Checked", "evidence_used": "items", "reviewer": "John", "timestamp": "2026-10-22T10:00:00+00:00"}
    client = make_app(app_module, monkeypatch, calls, 200,
                      {"status": "success", "result": {"state": "applied", "human_decision": record}})
    resp = client.post("/multi-agent/runs/run-abc/decision",
                       data={"decision": "Partially Accept", "correction": "Overlap is 25 min", "reason": "Checked",
                             "evidence_used": "items", "reviewer": "John", "approved_ids": ["1-2"]})
    assert resp.status_code == 200
    assert calls[0]["url"].endswith("/api/activity/multi-agent/runs/run-abc/decision")
    assert calls[0]["json"]["approved_ids"] == ["1-2"]
    assert calls[0]["json"]["decision"] == "Partially Accept"
    assert b"Workflow ID: run-abc" in resp.data and b"Reviewer: John" in resp.data


def test_fixable_decision_error_stays_in_form_and_is_escaped(app_module, monkeypatch):
    calls = []
    client = make_app(app_module, monkeypatch, calls, 404, {"status": "error", "error": "unknown run: <script>x</script>"})
    resp = client.post("/multi-agent/runs/run-abc/decision", data={"decision": "Accept"})
    assert resp.status_code == 200
    assert resp.headers["HX-Retarget"] == "#ma-err-run-abc"
    assert b"<script>" not in resp.data and b"&lt;script&gt;" in resp.data


def test_already_decided_replaces_form(app_module, monkeypatch):
    def fake_request(method, url, params=None, json=None, timeout=None, headers=None):
        return DummyResponse(409, {"status": "error", "error": "only one human decision is allowed per run"})

    monkeypatch.setattr(app_module.requests, "request", fake_request)
    resp = app_module.create_app().test_client().post("/multi-agent/runs/run-abc/decision", data={"decision": "Accept"})
    assert "HX-Retarget" not in resp.headers
    assert b"only one human decision" in resp.data


# ---- timeout budget -------------------------------------------------------
# A Multi-Agent run is Planner -> Worker -> Reviewer: three sequential Ollama calls. On the
# CPU-only AI host a real activity_schedule_check run measured 139s, but the frontend used
# one hardcoded 60s read timeout for every backend call, so the page showed "Read timed out"
# while the run actually completed server-side. These pin the budget so it cannot regress.

def test_multi_agent_timeout_exceeds_the_backend_ceiling(app_module):
    """The frontend must outwait the backend, or the backend's own error never surfaces."""
    from services import multi_agent_api  # backend client; conftest puts backend/ on sys.path

    _, frontend_read = app_module.MULTI_AGENT_REQUEST_TIMEOUT
    assert frontend_read > multi_agent_api.MULTI_AGENT_TIMEOUT_SECONDS, (
        f"frontend waits {frontend_read}s but the backend waits "
        f"{multi_agent_api.MULTI_AGENT_TIMEOUT_SECONDS}s for the Multi-Agent Server"
    )


def test_multi_agent_timeout_covers_a_real_run(app_module):
    # 139s measured live; keep clear headroom for a retried agent call.
    _, frontend_read = app_module.MULTI_AGENT_REQUEST_TIMEOUT
    assert frontend_read >= 240, f"{frontend_read}s is too tight for a 3-agent run"


def test_ordinary_calls_keep_the_short_timeout(app_module):
    """Only the Multi-Agent path gets the long budget; CRUD must not hang for minutes."""
    assert app_module.REQUEST_TIMEOUT == (5, 60)


def test_starting_a_run_uses_the_multi_agent_timeout(app_module, client, monkeypatch):
    seen = {}

    def fake_request(method, url, **kwargs):
        seen["timeout"] = kwargs.get("timeout")
        return DummyResponse(200, {"run": RUN})

    monkeypatch.setattr(app_module.requests, "request", fake_request)
    client.post("/multi-agent/run", data={"date": "2026-10-27", "slot_hours": "2"})

    assert seen["timeout"] == app_module.MULTI_AGENT_REQUEST_TIMEOUT
