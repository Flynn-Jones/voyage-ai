"""Itinerary-owned human review and retry-safe application over HTTP boundaries."""
from flask import Blueprint, current_app, jsonify, request

from services import database_api, multi_agent_api
from services import multi_agent_validation as validate

multi_agent_bp = Blueprint("multi_agent", __name__, url_prefix="/api/itinerary/multi-agent")


@multi_agent_bp.before_request
def enabled():
    if not current_app.config["MULTI_AGENT_ENABLED"]:
        return jsonify(status="error", error="Multi-Agent mode is disabled"), 403


@multi_agent_bp.errorhandler(multi_agent_api.UpstreamError)
def upstream_error(exc):
    return jsonify(status="error", error=str(exc)), exc.status


@multi_agent_bp.errorhandler(ValueError)
def invalid_input(exc):
    return jsonify(status="error", error=str(exc)), 400


def _success(result, status=200):
    return jsonify(status="success", result=result), status


def _shape(run, requested, run_id=None):
    try:
        if (not isinstance(run, dict) or run.get("workflow") != validate.WORKFLOW
                or not isinstance(run.get("run_id"), str) or not validate.RUN_ID.fullmatch(run["run_id"])
                or (run_id is not None and run["run_id"] != run_id)):
            raise ValueError("invalid workflow/run identity")
        plan, review = run.get("plan"), run.get("review")
        if (not isinstance(plan, dict) or plan.get("scope") != requested or not isinstance(plan.get("steps"), list)
                or not plan["steps"] or any(not isinstance(step, dict) for step in plan["steps"])
                or not any(step.get("tool") == "get_itinerary" for step in plan["steps"])
                or any(step.get("tool") not in (None, "get_itinerary") for step in plan["steps"])
                or not isinstance(plan.get("objective"), str) or not plan["objective"].strip() or not isinstance(review, dict)
                or review.get("risk") not in ("low", "medium", "high")
                or review.get("status") not in ("ready_for_human_review", "needs_manual_review")
                or any(not isinstance(review.get(key), list) or any(not isinstance(text, str) for text in review[key])
                       for key in ("findings", "recommendations"))):
            raise ValueError("malformed Planner/Reviewer output")
        validate.work_items(run.get("work"), requested)
    except ValueError as exc:
        raise multi_agent_api.UpstreamError(f"Invalid Multi-Agent run: {exc}") from exc
    return run


def _own_run(run_id):
    if not validate.RUN_ID.fullmatch(run_id):
        raise multi_agent_api.UpstreamError("unknown run", 404)
    run = multi_agent_api.call("GET", f"/runs/{run_id}")
    if not isinstance(run, dict):
        raise multi_agent_api.UpstreamError("Malformed Multi-Agent run")
    if run.get("workflow") != validate.WORKFLOW or run.get("run_id") != run_id:
        raise multi_agent_api.UpstreamError("run does not belong to the Itinerary workflow", 404)
    try:
        requested = validate.scope(run.get("input"))
    except ValueError as exc:
        raise multi_agent_api.UpstreamError("Malformed stored run scope") from exc
    if run.get("state") not in ("running", "awaiting_human_review", "approved", "partially_accepted", "corrected", "rejected", "applied", "failed"):
        raise multi_agent_api.UpstreamError("Malformed stored run state")
    return run if run["state"] in ("running", "failed") else _shape(run, requested, run_id)


@multi_agent_bp.post("/run")
def start():
    requested = validate.scope(request.get_json(silent=True))
    result = multi_agent_api.call("POST", f"/workflows/{validate.WORKFLOW}/runs",
                                  {"input": requested, "requested_by": "itinerary-be"})
    _shape(result, requested)
    if result.get("state") != "awaiting_human_review":
        raise multi_agent_api.UpstreamError("New run is not awaiting human review")
    return _success(result, 201)


@multi_agent_bp.get("/runs/<run_id>")
def get_run(run_id):
    return _success(_own_run(run_id))


@multi_agent_bp.get("/runs/<run_id>/history")
def history(run_id):
    _own_run(run_id)
    events = multi_agent_api.call("GET", f"/runs/{run_id}/history")
    if not isinstance(events, list) or any(
        not isinstance(e, dict) or not isinstance(e.get("step"), str) or not isinstance(e.get("state"), str) for e in events
    ):
        raise multi_agent_api.UpstreamError("Malformed run history")
    return _success(events)


@multi_agent_bp.post("/runs/<run_id>/review")
def review_run(run_id):
    body = request.get_json(silent=True)
    run = _own_run(run_id)
    if run["state"] != "awaiting_human_review":
        raise multi_agent_api.UpstreamError("run already decided or not reviewable", 409)
    requested = validate.scope(run["input"])
    expected = validate.decision_items(validate.work_items(run["work"], requested), body, requested)
    payload = {key: body[key] for key in ("decision", "approved_ids", "rejected_ids", "edits", "note") if key in body}
    payload["reviewer"] = "itinerary-ui"
    result = multi_agent_api.call("POST", f"/runs/{run_id}/review", payload)
    try:
        if (not isinstance(result, dict) or result.get("run_id") != run_id or result.get("decision") != body["decision"]
                or result.get("state") != validate.DECISIONS[body["decision"]]
                or not isinstance(result.get("approved_items"), list)
                or [validate.proposal(i, requested) for i in result["approved_items"]] != expected):
            raise ValueError("decision output does not match the human selection")
    except ValueError as exc:
        raise multi_agent_api.UpstreamError(str(exc)) from exc
    # Review records the decision only. Applying is an explicit, separate action.
    return _success(result)


@multi_agent_bp.post("/runs/<run_id>/apply")
def apply_run(run_id):
    run = _own_run(run_id)
    if run["state"] not in ("approved", "partially_accepted", "corrected"):
        raise multi_agent_api.UpstreamError("run must have an unapplied approving human decision", 409)
    requested = validate.scope(run["input"])
    try:
        decision = run.get("decision")
        if (not isinstance(decision, dict) or not isinstance(decision.get("decision"), str)
                or validate.DECISIONS.get(decision["decision"]) != run["state"]):
            raise ValueError("missing or inconsistent human decision")
        expected = validate.decision_items(validate.work_items(run["work"], requested), decision, requested)
        approved = run.get("approved_items")
        if not isinstance(approved, list) or [validate.proposal(i, requested) for i in approved] != expected:
            raise ValueError("approved items do not match the recorded human decision")
    except ValueError as exc:
        raise multi_agent_api.UpstreamError(str(exc)) from exc
    payload = {"workflow": validate.WORKFLOW, **requested, "decision": decision["decision"], "changes": expected}
    try:
        applied = database_api.apply_multi_agent(run_id, payload)
    except database_api.ApplicationConflict as exc:
        raise multi_agent_api.UpstreamError(str(exc), 409) from exc
    except database_api.DatabaseServiceError as exc:
        raise multi_agent_api.UpstreamError("Database application failed; retry this same run safely") from exc
    ids = [i["itinerary_item_id"] for i in expected]
    if (not isinstance(applied, dict) or applied.get("run_id") != run_id or applied.get("updated_item_ids") != ids
            or type(applied.get("replayed")) is not bool):
        raise multi_agent_api.UpstreamError("Database returned a malformed application receipt; retry the same run")
    recorded = False
    try:
        result = multi_agent_api.call("POST", f"/runs/{run_id}/applied", {"created_ids": ids, "target": "itinerary-db"})
        recorded = (isinstance(result, dict) and result.get("run_id") == run_id and result.get("state") == "applied"
                    and result.get("created_ids") == ids and result.get("target") == "itinerary-db")
    except multi_agent_api.UpstreamError:
        # The database receipt makes retry safe, even across restarts/concurrent requests.
        recorded = False
    return _success({**applied, "state": "applied" if recorded else run["state"], "applied_recorded": recorded,
                     "message": "Saved approved changes." if recorded else
                     "Changes saved; acknowledgement pending. Refresh the run, then retry Apply if needed."})
