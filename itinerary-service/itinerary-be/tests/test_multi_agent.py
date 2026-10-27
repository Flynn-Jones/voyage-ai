"""Owning backend boundary: mocked shared HTTP service and database API."""
import copy
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import create_app
from services import database_api, multi_agent_api

BASE = "/api/itinerary/multi-agent"
RUN_ID = "run-itinerary-test"


@pytest.fixture
def run():
    scope = {"trip_reference": "TRIP-A", "day": 1}
    base = {"trip_reference": "TRIP-A", "day": 1, "activity_id": 1, "destination_id": 1, "estimated_cost": 10.0, "notes": None}
    rows = [{**base, "itinerary_item_id": i, "start_time": "10:00", "end_time": "11:00"} for i in (2, 3)]
    items = [{**row, "id": str(row["itinerary_item_id"]), "original": dict(row), "reason": "Remove overlap",
              "start_time": f"{11 + n}:00", "end_time": f"{12 + n}:00"} for n, row in enumerate(rows)]
    return {"run_id": RUN_ID, "workflow": "itinerary_schedule_review", "state": "awaiting_human_review", "input": scope,
            "plan": {"scope": scope, "objective": "Review overlaps", "steps": [{"tool": "get_itinerary"}]},
            "work": {"scope": scope, "summary": "Two shifts", "items": items,
                     "evidence": [{"source": "MCP:get_itinerary", "scope": scope, "records": rows}]},
            "review": {"risk": "medium", "status": "ready_for_human_review", "findings": [], "recommendations": []},
            "decision": None, "approved_items": None, "applied": None}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("MULTI_AGENT_ENABLED", "true")
    return create_app().test_client()


def respond(monkeypatch, *responses):
    mocked = Mock(side_effect=[Mock(status_code=code, json=Mock(return_value=body)) for code, body in responses])
    monkeypatch.setattr(multi_agent_api.requests, "request", mocked)
    return mocked


def success(value, code=200):
    return code, {"status": "success", "result": value}


def approve(run):
    run["state"] = "approved"
    run["decision"] = {"decision": "approve", "approved_ids": ["2", "3"], "rejected_ids": [], "edits": {}}
    run["approved_items"] = copy.deepcopy(run["work"]["items"])
    return run


@pytest.mark.parametrize("method,path", [("post", "/run"), ("get", f"/runs/{RUN_ID}"),
    ("get", f"/runs/{RUN_ID}/history"), ("post", f"/runs/{RUN_ID}/review"), ("post", f"/runs/{RUN_ID}/apply")])
def test_disabled_before_body_or_network(monkeypatch, method, path):
    monkeypatch.setenv("MULTI_AGENT_ENABLED", "false")
    network = respond(monkeypatch)
    result = getattr(create_app().test_client(), method)(BASE + path)
    assert result.status_code == 403 and "disabled" in result.json["error"]
    network.assert_not_called()


@pytest.mark.parametrize("body", [{}, [], {"trip_reference": ""}, {"trip_reference": "A", "day": True},
                                 {"trip_reference": "A", "day": -1}, {"trip_reference": "A", "day": "1"}])
def test_start_bad_scope_no_network(client, monkeypatch, body):
    network = respond(monkeypatch)
    assert client.post(BASE + "/run", json=body).status_code == 400
    network.assert_not_called()


def test_start_uses_only_scoped_fixed_workflow(client, monkeypatch, run):
    network = respond(monkeypatch, success(run, 201))
    result = client.post(BASE + "/run", json={"trip_reference": " TRIP-A ", "day": 1, "workflow": "budget_reconcile"})
    assert result.status_code == 201
    call = network.call_args
    assert call.args == ("POST", "http://localhost:7004/workflows/itinerary_schedule_review/runs")
    assert call.kwargs["json"] == {"input": {"trip_reference": "TRIP-A", "day": 1}, "requested_by": "itinerary-be"}
    assert call.kwargs["timeout"] == 420
    assert set(result.json["result"]) >= {"plan", "work", "review", "state"}


@pytest.mark.parametrize("failure,status", [(requests.Timeout(), 504), (requests.ConnectionError(), 502), (ValueError(), 502)])
def test_network_failures(client, monkeypatch, failure, status):
    monkeypatch.setattr(multi_agent_api.requests, "request", Mock(side_effect=failure))
    assert client.post(BASE + "/run", json={"trip_reference": "TRIP-A"}).status_code == status


@pytest.mark.parametrize("body", [[], {}, {"status": "success"}, {"status": "success", "result": []}])
def test_malformed_start_response(client, monkeypatch, body):
    respond(monkeypatch, (201, body))
    assert client.post(BASE + "/run", json={"trip_reference": "TRIP-A"}).status_code == 502


@pytest.mark.parametrize("status", [400, 404, 409])
def test_shared_errors_preserved(client, monkeypatch, status):
    respond(monkeypatch, (status, {"status": "error", "error": "unknown workflow or invalid input"}))
    assert client.post(BASE + "/run", json={"trip_reference": "TRIP-A"}).status_code == status


@pytest.mark.parametrize("mutation", ["plan", "work", "review", "scope", "evidence"])
def test_malformed_agent_output(client, monkeypatch, run, mutation):
    if mutation in ("plan", "work", "review"):
        run[mutation] = None
    elif mutation == "scope":
        run["work"]["items"][0]["trip_reference"] = "TRIP-B"
    else:
        run["work"]["evidence"][0]["records"] = []
    respond(monkeypatch, success(run))
    assert client.get(BASE + f"/runs/{RUN_ID}").status_code == 502


@pytest.mark.parametrize("mutation", ["workflow", "run_id"])
def test_foreign_run_not_exposed_or_applied(client, monkeypatch, run, mutation):
    run[mutation] = "budget_reconcile" if mutation == "workflow" else "run-other"
    network = respond(monkeypatch, success(run))
    write = Mock()
    monkeypatch.setattr(database_api, "apply_multi_agent", write)
    assert client.post(BASE + f"/runs/{RUN_ID}/apply").status_code == 404
    assert network.call_count == 1
    write.assert_not_called()


@pytest.mark.parametrize("decision,state,ids,edits", [
    ("approve", "approved", ["2", "3"], {}), ("partial", "partially_accepted", ["2"], {}),
    ("correct", "corrected", ["2", "3"], {"2": {"notes": "Human correction"}}), ("reject", "rejected", [], {}),
])
def test_human_decisions_do_not_write_until_apply(client, monkeypatch, run, decision, state, ids, edits):
    body = {"decision": decision, "approved_ids": ids, "edits": edits}
    approved = [{**i, **edits.get(i["id"], {})} for i in run["work"]["items"] if i["id"] in ids]
    decision_result = {"run_id": RUN_ID, "decision": decision, "state": state, "approved_items": approved, "rejected_ids": []}
    respond(monkeypatch, success(run), success(decision_result))
    write = Mock()
    monkeypatch.setattr(database_api, "apply_multi_agent", write)
    response = client.post(BASE + f"/runs/{RUN_ID}/review", json=body)
    assert response.status_code == 200 and response.json["result"]["state"] == state
    write.assert_not_called()


@pytest.mark.parametrize("body", [[], {}, {"decision": []}, {"decision": {}}, {"decision": "partial"}, {"decision": "correct"},
    {"decision": "approve", "rejected_ids": ["2"]}, {"decision": "partial", "approved_ids": ["99"]},
    {"decision": "correct", "edits": {"2": {"trip_reference": "TRIP-B"}}},
    {"decision": "correct", "edits": {"2": {"start_time": "bad"}}},
    {"decision": "correct", "edits": {"2": {"end_time": "15:00"}}}])
def test_invalid_human_decision_stays_reviewable(client, monkeypatch, run, body):
    network = respond(monkeypatch, success(run))
    assert client.post(BASE + f"/runs/{RUN_ID}/review", json=body).status_code == 400
    assert network.call_count == 1


@pytest.mark.parametrize("state", ["rejected", "applied", "awaiting_human_review", "running", "failed"])
def test_non_applicable_run_never_writes(client, monkeypatch, run, state):
    run["state"] = state
    respond(monkeypatch, success(run))
    write = Mock()
    monkeypatch.setattr(database_api, "apply_multi_agent", write)
    assert client.post(BASE + f"/runs/{RUN_ID}/apply").status_code == 409
    write.assert_not_called()


def test_apply_uses_database_api_then_shared_applied_mechanism(client, monkeypatch, run):
    approve(run)
    network = respond(monkeypatch, success(run), success({"run_id": RUN_ID, "state": "applied", "created_ids": [2, 3], "target": "itinerary-db"}))
    write = Mock(return_value={"run_id": RUN_ID, "updated_item_ids": [2, 3], "replayed": False})
    monkeypatch.setattr(database_api, "apply_multi_agent", write)
    result = client.post(BASE + f"/runs/{RUN_ID}/apply")
    assert result.status_code == 200 and result.json["result"]["applied_recorded"] is True
    assert write.call_args.args[0] == RUN_ID
    assert write.call_args.args[1]["trip_reference"] == "TRIP-A"
    assert write.call_args.args[1]["changes"] == run["approved_items"]
    assert network.call_args.args[1].endswith("/applied")


def test_lost_ack_can_retry_without_rewriting(client, monkeypatch, run):
    approve(run)
    write = Mock(return_value={"run_id": RUN_ID, "updated_item_ids": [2, 3], "replayed": True})
    monkeypatch.setattr(database_api, "apply_multi_agent", write)
    respond(monkeypatch, success(run), (503, {}))
    response = client.post(BASE + f"/runs/{RUN_ID}/apply")
    assert response.status_code == 200 and response.json["result"]["applied_recorded"] is False
    assert response.json["result"]["replayed"] is True


@pytest.mark.parametrize("mutation", ["missing_decision", "malformed_decision", "altered_approval", "scope_change"])
def test_untrusted_approved_output_cannot_write(client, monkeypatch, run, mutation):
    approve(run)
    if mutation == "missing_decision":
        run["decision"] = None
    elif mutation == "malformed_decision":
        run["decision"] = {"decision": []}
    elif mutation == "altered_approval":
        run["approved_items"][0]["notes"] = "Not what the human approved"
    else:
        run["approved_items"][0]["day"] = 2
    respond(monkeypatch, success(run))
    write = Mock()
    monkeypatch.setattr(database_api, "apply_multi_agent", write)
    assert client.post(BASE + f"/runs/{RUN_ID}/apply").status_code == 502
    write.assert_not_called()


@pytest.mark.parametrize("failure,status", [(database_api.ApplicationConflict("stale"), 409),
                                         (database_api.DatabaseServiceError(), 502)])
def test_database_failure_does_not_acknowledge(client, monkeypatch, run, failure, status):
    approve(run)
    network = respond(monkeypatch, success(run))
    monkeypatch.setattr(database_api, "apply_multi_agent", Mock(side_effect=failure))
    assert client.post(BASE + f"/runs/{RUN_ID}/apply").status_code == status
    assert network.call_count == 1


def test_history_and_unknown_run(client, monkeypatch, run):
    respond(monkeypatch, success(run), success([{"step": "plan", "state": "running"}]))
    assert client.get(BASE + f"/runs/{RUN_ID}/history").json["result"][0]["step"] == "plan"
    respond(monkeypatch, (404, {"status": "error", "error": "unknown run"}))
    assert client.get(BASE + "/runs/run-missing").status_code == 404
    assert client.get(BASE + "/runs/invalid").status_code == 404


def test_malformed_history_is_error(client, monkeypatch, run):
    respond(monkeypatch, success(run), success({"not": "events"}))
    assert client.get(BASE + f"/runs/{RUN_ID}/history").status_code == 502


def test_review_response_cannot_substitute_approved_records(client, monkeypatch, run):
    result = {"run_id": RUN_ID, "decision": "approve", "state": "approved", "approved_items": []}
    respond(monkeypatch, success(run), success(result))
    assert client.post(BASE + f"/runs/{RUN_ID}/review", json={"decision": "approve"}).status_code == 502


@pytest.mark.parametrize("key", ["steps", "objective"])
def test_empty_planner_output_rejected(client, monkeypatch, run, key):
    run["plan"][key] = [] if key == "steps" else ""
    respond(monkeypatch, success(run))
    assert client.get(BASE + f"/runs/{RUN_ID}").status_code == 502


def test_applied_ack_malformed_is_reported_as_pending(client, monkeypatch, run):
    approve(run)
    respond(monkeypatch, success(run), success({"state": "applied"}))
    monkeypatch.setattr(database_api, "apply_multi_agent", Mock(return_value={"run_id": RUN_ID, "updated_item_ids": [2, 3], "replayed": False}))
    result = client.post(BASE + f"/runs/{RUN_ID}/apply")
    assert result.status_code == 200 and not result.json["result"]["applied_recorded"]


def test_database_receipt_malformed_not_acknowledged(client, monkeypatch, run):
    approve(run)
    network = respond(monkeypatch, success(run))
    monkeypatch.setattr(database_api, "apply_multi_agent", Mock(return_value={"run_id": RUN_ID, "updated_item_ids": [999], "replayed": False}))
    assert client.post(BASE + f"/runs/{RUN_ID}/apply").status_code == 502
    assert network.call_count == 1
