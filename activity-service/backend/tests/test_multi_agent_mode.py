"""Multi-Agent mode routes: /api/activity/multi-agent/*. The shared Multi-Agent Server is mocked."""
import pytest

from services import multi_agent_api

RUN = {
    "run_id": "run-1",
    "workflow": "activity_schedule_check",
    "state": "awaiting_human_review",
    "plan": {"objective": "Check 2026-10-06 for overlaps.", "steps": []},
    "work": {"items": [{"id": "1-2", "overlap_minutes": 30}], "summary": "1 overlapping pair",
             "evidence": [], "worker_output": "Output:\n...", "checks": []},
    "review": {"risk": "medium", "findings": [], "recommendations": [], "recommendation": "Accept"},
    "decision": None,
}


@pytest.fixture
def server(monkeypatch):
    """Fake Multi-Agent Server: records calls and answers from canned envelopes."""
    calls = []

    def ok(result, status=200):
        return status, {"status": "success", "result": result}

    def get_run(run_id):
        calls.append(("get_run", run_id))
        if run_id == "missing":
            return 404, {"status": "error", "error": "unknown run"}
        if run_id == "other":
            return ok({**RUN, "run_id": "other", "workflow": "budget_reconcile"})
        return ok(RUN)

    def submit_review(run_id, review):
        calls.append(("submit_review", run_id, review))
        state = {"approve": "approved", "partial": "partially_accepted", "reject": "rejected"}[review["decision"]]
        approved = [] if state == "rejected" else RUN["work"]["items"]
        return ok({"run_id": run_id, "state": state, "approved_items": approved,
                   "rejected_ids": ["1-2"] if state == "rejected" else []})

    def report_applied(run_id):
        calls.append(("report_applied", run_id))
        return ok({"state": "applied"})

    def start_run(run_input):
        calls.append(("start_run", run_input))
        if run_input.get("task") not in ("summary", "overlaps", "free_slots"):
            return 400, {"status": "error", "error": "invalid input: task must be one of ..."}
        return ok(RUN, 201)

    monkeypatch.setattr(multi_agent_api, "MULTI_AGENT_ENABLED", True)
    monkeypatch.setattr(multi_agent_api, "get_run", get_run)
    monkeypatch.setattr(multi_agent_api, "submit_review", submit_review)
    monkeypatch.setattr(multi_agent_api, "report_applied", report_applied)
    monkeypatch.setattr(multi_agent_api, "start_run", start_run)
    return calls


DECISION = {"decision": "Accept", "reason": "Clash matches the schedule", "reviewer": "John",
            "evidence_used": "worker items, reviewer risk"}


def test_start_workflow_marks_human_decision_pending(client, server):
    resp = client.post("/api/activity/multi-agent/workflow",
                       json={"task": "overlaps", "date": "2026-10-06", "ignored": "x"})
    assert resp.status_code == 201
    result = resp.get_json()["result"]
    assert result["human_approval_required"] is True
    assert result["human_decision"]["status"] == "pending"
    assert result["human_decision"]["allowed_values"] == ["Accept", "Partially Accept", "Reject"]
    assert server[0] == ("start_run", {"task": "overlaps", "date": "2026-10-06"})


def test_start_workflow_passes_through_invalid_input(client, server):
    resp = client.post("/api/activity/multi-agent/workflow", json={"task": "nope"})
    assert resp.status_code == 400


def test_disabled_mode_is_403_everywhere(client, monkeypatch):
    monkeypatch.setattr(multi_agent_api, "MULTI_AGENT_ENABLED", False)
    assert client.post("/api/activity/multi-agent/workflow", json={"task": "summary"}).status_code == 403
    assert client.get("/api/activity/multi-agent/runs/run-1").status_code == 403
    assert client.get("/api/activity/multi-agent/runs/run-1/history").status_code == 403
    assert client.post("/api/activity/multi-agent/runs/run-1/decision", json=DECISION).status_code == 403


def test_server_down_is_502(client, monkeypatch):
    monkeypatch.setattr(multi_agent_api, "MULTI_AGENT_ENABLED", True)

    def boom(*_args, **_kwargs):
        raise multi_agent_api.MultiAgentServiceError("unreachable")

    monkeypatch.setattr(multi_agent_api, "start_run", boom)
    resp = client.post("/api/activity/multi-agent/workflow", json={"task": "summary"})
    assert resp.status_code == 502
    assert "unreachable" in resp.get_json()["error"]


def test_other_features_runs_are_hidden(client, server):
    assert client.get("/api/activity/multi-agent/runs/other").status_code == 404
    assert client.get("/api/activity/multi-agent/runs/missing").status_code == 404


def test_accept_records_full_decision_and_closes_run(client, server):
    resp = client.post("/api/activity/multi-agent/runs/run-1/decision", json=DECISION)
    assert resp.status_code == 200
    result = resp.get_json()["result"]
    assert result["state"] == "applied"
    record = result["human_decision"]
    assert record["workflow_id"] == "run-1" and record["decision"] == "Accept"
    assert record["reviewer"] == "John" and record["timestamp"]

    _, _, review = next(c for c in server if c[0] == "submit_review")
    assert review["decision"] == "approve" and review["reviewer"] == "John"
    for line in ("Workflow ID: run-1", "Decision: Accept", "Reason: Clash matches the schedule",
                 "Evidence Used: worker items, reviewer risk", "Reviewer: John", "Timestamp: "):
        assert line in review["note"]
    assert ("report_applied", "run-1") in server


def test_partially_accept_needs_correction_and_sends_items(client, server):
    resp = client.post("/api/activity/multi-agent/runs/run-1/decision",
                       json={**DECISION, "decision": "Partially Accept"})
    assert resp.status_code == 400 and "correction" in resp.get_json()["error"]

    resp = client.post("/api/activity/multi-agent/runs/run-1/decision",
                       json={**DECISION, "decision": "Partially Accept", "correction": "Overlap is 25 min"})
    assert resp.status_code == 200
    _, _, review = next(c for c in server if c[0] == "submit_review")
    assert review["decision"] == "partial" and review["approved_ids"] == ["1-2"]
    assert "Correction: Overlap is 25 min" in review["note"]


def test_reject_does_not_report_applied(client, server):
    resp = client.post("/api/activity/multi-agent/runs/run-1/decision", json={**DECISION, "decision": "Reject"})
    assert resp.status_code == 200
    assert resp.get_json()["result"]["state"] == "rejected"
    assert not any(c[0] == "report_applied" for c in server)


@pytest.mark.parametrize("body,message", [
    ({**DECISION, "decision": "approve"}, "decision must be one of"),
    ({**DECISION, "reason": ""}, "reason is required"),
    ({**DECISION, "reviewer": " "}, "reviewer is required"),
    ({**DECISION, "reason": "x" * 501}, "at most 500"),
])
def test_invalid_decision_is_400_and_not_sent(client, server, body, message):
    resp = client.post("/api/activity/multi-agent/runs/run-1/decision", json=body)
    assert resp.status_code == 400 and message in resp.get_json()["error"]
    assert not any(c[0] == "submit_review" for c in server)
