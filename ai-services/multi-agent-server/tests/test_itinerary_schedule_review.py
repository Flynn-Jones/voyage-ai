"""Itinerary plugin through the existing shared lifecycle, store and audit contracts."""
import copy
import json
from unittest.mock import Mock

import pytest

from workflows.itinerary_schedule_review import ItineraryScheduleReview, proposals


def records():
    base = {"trip_reference": "TRIP-A", "day": 1, "activity_id": 1, "destination_id": 1, "estimated_cost": 10, "notes": "Stored note"}
    return [{**base, "itinerary_item_id": 1, "start_time": "09:00", "end_time": "11:00"},
            {**base, "itinerary_item_id": 2, "start_time": "10:00", "end_time": "11:00"},
            {**base, "itinerary_item_id": 3, "start_time": "10:30", "end_time": "11:30"}]


def prepare(monkeypatch, fake_mcp, rows=None):
    rows = records() if rows is None else rows

    def sdk(tool, arguments):
        fake_mcp.calls.append((tool, arguments))
        return {"trip_reference": "TRIP-A", "count": len(rows), "items": rows}
    monkeypatch.setattr(fake_mcp, "sdk", sdk)


def test_registry_lifecycle_history_and_audit(client, fake_mcp, monkeypatch):
    prepare(monkeypatch, fake_mcp)
    run = client.start("itinerary_schedule_review", {"trip_reference": "TRIP-A", "day": 1})
    assert run["plan"]["scope"] == {"trip_reference": "TRIP-A", "day": 1}
    assert [(i["id"], i["start_time"], i["end_time"]) for i in run["work"]["items"]] == [
        ("2", "11:00", "12:00"), ("3", "12:00", "13:00")]
    assert fake_mcp.calls == [("get_itinerary", {"trip_reference": "TRIP-A", "day": 1})]
    assert run["review"]["status"] == "ready_for_human_review"
    assert client.post(f"/runs/{run['run_id']}/review", {"decision": "approve"})[0] == 200
    assert client.post(f"/runs/{run['run_id']}/applied", {"created_ids": [2, 3], "target": "itinerary-db"})[0] == 200
    history = client.get(f"/runs/{run['run_id']}/history")[1]["result"]
    assert [e["step"] for e in history] == ["created", "plan", "work", "review", "human_review", "human_review", "applied"]
    audit = client.app.store.read_audit(run["run_id"])
    assert [e["agent"] for e in audit] == ["planner", "worker", "reviewer", "human", "backend"]
    assert audit[1]["tools_called"][0]["tool"] == "get_itinerary"


@pytest.mark.parametrize("body,state,count", [
    ({"decision": "approve"}, "approved", 2),
    ({"decision": "partial", "approved_ids": ["2"]}, "partially_accepted", 1),
    ({"decision": "correct", "edits": {"2": {"notes": "Human correction"}}}, "corrected", 2),
    ({"decision": "reject"}, "rejected", 0),
])
def test_each_human_decision(client, fake_mcp, monkeypatch, body, state, count):
    prepare(monkeypatch, fake_mcp)
    run = client.start("itinerary_schedule_review", {"trip_reference": "TRIP-A"})
    status, result = client.post(f"/runs/{run['run_id']}/review", body)
    assert status == 200 and result["result"]["state"] == state
    assert len(result["result"]["approved_items"]) == count
    assert client.post(f"/runs/{run['run_id']}/review", body)[0] == 409
    if state == "rejected":
        assert client.post(f"/runs/{run['run_id']}/applied", {"created_ids": [], "target": "itinerary-db"})[0] == 409


@pytest.mark.parametrize("payload", [{}, {"trip_reference": " "}, {"trip_reference": 1},
                                       {"trip_reference": "x" * 121}, {"trip_reference": "A", "day": True},
                                       {"trip_reference": "A", "day": 0}, {"trip_reference": "A", "day": "1"}])
def test_bad_scope_creates_no_run(client, payload):
    assert client.post("/workflows/itinerary_schedule_review/runs", {"input": payload})[0] == 400
    assert client.get("/runs")[1]["result"] == []


@pytest.mark.parametrize("mutation", ["trip", "day", "duplicate", "time", "cost", "missing"])
def test_invalid_evidence_fails_closed(client, fake_mcp, monkeypatch, mutation):
    rows = records()
    if mutation == "trip":
        rows[1]["trip_reference"] = "TRIP-B"
    elif mutation == "day":
        rows[1]["day"] = 2
    elif mutation == "duplicate":
        rows[1]["itinerary_item_id"] = 1
    elif mutation == "time":
        rows[1]["start_time"] = "bad"
    elif mutation == "cost":
        rows[1]["estimated_cost"] = float("nan")
    else:
        del rows[1]["activity_id"]
    prepare(monkeypatch, fake_mcp, rows)
    status, body = client.post("/workflows/itinerary_schedule_review/runs", {"input": {"trip_reference": "TRIP-A", "day": 1}})
    assert status == 500 and body["error"]["state"] == "failed"


def test_no_data_and_no_space_are_manual_review():
    workflow = ItineraryScheduleReview()
    payload = workflow.validate_input({"trip_reference": "TRIP-A"})
    plan = workflow.fallback_plan(payload)
    for rows in [[], [{**r, "start_time": "23:00", "end_time": "23:59"} for r in records()]]:
        ctx = Mock()
        ctx.mcp.return_value = {"trip_reference": "TRIP-A", "count": len(rows), "items": rows}
        work = workflow.work(payload, plan, ctx)
        assert workflow.fallback_review(payload, plan, work)["status"] == "needs_manual_review"
        assert not work["items"]


def test_valid_model_outputs_and_unsafe_review_cannot_override_rules(client, chat, fake_mcp, monkeypatch):
    prepare(monkeypatch, fake_mcp)
    workflow = ItineraryScheduleReview()
    payload = workflow.validate_input({"trip_reference": "TRIP-A"})
    plan = workflow.fallback_plan(payload)
    ctx = Mock()
    ctx.mcp.return_value = {"trip_reference": "TRIP-A", "count": 3, "items": records()}
    work = workflow.work(payload, plan, ctx)
    review = workflow.fallback_review(payload, plan, work)
    chat.queue("Planner", json.dumps(plan))
    chat.queue("Reviewer", json.dumps(review))
    run = client.start(workflow.name, payload)
    assert run["review"] == review
    audit = client.app.store.read_audit(run["run_id"])
    assert audit[0]["source"] == audit[2]["source"] == "llm"
    unsafe = copy.deepcopy(work)
    unsafe["items"][0]["trip_reference"] = "TRIP-B"
    workflow.reviewer_prompt(payload, plan, unsafe)
    with pytest.raises(ValueError):
        workflow.validate_review(review)
    assert workflow.fallback_review(payload, plan, unsafe)["risk"] == "high"


def test_planner_cannot_change_scope_or_tool():
    workflow = ItineraryScheduleReview()
    payload = workflow.validate_input({"trip_reference": "TRIP-A"})
    plan = workflow.fallback_plan(payload)
    plan["scope"] = {"trip_reference": "TRIP-B", "day": None}
    with pytest.raises(ValueError):
        workflow.validate_plan(plan, payload)
    plan = workflow.fallback_plan(payload)
    plan["steps"][0]["tool"] = "create_accommodation"
    with pytest.raises(ValueError):
        workflow.validate_plan(plan, payload)


def test_days_are_independent_and_duration_preserved():
    rows = records()
    rows[2]["day"] = 2
    items, issues = proposals(rows)
    assert len(items) == 1 and items[0]["day"] == 1 and not issues
