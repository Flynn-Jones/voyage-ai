"""Multi-Agent mode: Budget's budget_reconcile workflow on the shared Multi-Agent Server.

Plan / Act / Observe happen on the server (Planner -> Worker -> Reviewer). This blueprint is
the Human Review -> Adapt half: it forwards the person's decision, writes ONLY the approved
(and edited) lines to budget-db as Planned expenses, and reports the created ids back.
A rejected run writes nothing; a second decision gets the server's 409 before any write.
"""
import logging

from flask import Blueprint, jsonify, request

from services import database_api, multi_agent_api

logger = logging.getLogger(__name__)
multi_agent_mode_bp = Blueprint("multi_agent_mode", __name__)

DECISIONS = ("approve", "partial", "correct", "reject")
EDITABLE_FIELDS = ("estimated_cost", "category")
MAX_CATEGORY = 40


def _disabled():
    return jsonify({"status": "error", "error": "Multi-Agent mode is disabled (cloud default)."}), 403


def _error(status, message):
    return jsonify({"status": "error", "error": message}), status


def _unavailable(exc):
    return _error(502, f"Multi-Agent Server unavailable: {exc}")


def _own_run(run_id):
    """(run, None) for a budget_reconcile run, else (None, flask_response)."""
    status, body = multi_agent_api.get_run(run_id)
    if status == 404:
        return None, _error(404, f"unknown run: {run_id}")
    if status != 200:
        return None, (jsonify(body), status)
    run = body["result"]
    if run.get("workflow") != multi_agent_api.WORKFLOW:
        return None, _error(404, f"run {run_id} is not a Budget reconciliation")
    return run, None


def _validate_edits(edits):
    if not isinstance(edits, dict):
        return "edits must be an object of {line id: {field: value}}"
    for line_id, changes in edits.items():
        if not isinstance(changes, dict) or not changes:
            return f"edits for line {line_id} must be a non-empty object"
        unknown = set(changes) - set(EDITABLE_FIELDS)
        if unknown:
            return f"line {line_id}: only {', '.join(EDITABLE_FIELDS)} can be edited (got {', '.join(sorted(unknown))})"
        if "estimated_cost" in changes:
            cost = changes["estimated_cost"]
            if isinstance(cost, bool) or not isinstance(cost, (int, float)) or cost < 0:
                return f"line {line_id}: estimated_cost must be a number >= 0"
        if "category" in changes:
            category = changes["category"]
            if not isinstance(category, str) or not category.strip() or len(category) > MAX_CATEGORY:
                return f"line {line_id}: category must be a non-empty string (max {MAX_CATEGORY} chars)"
            changes["category"] = category.strip()
    return None


@multi_agent_mode_bp.route("/budget/multi-agent/run", methods=["POST"])
def run_reconcile():
    if not multi_agent_api.multi_agent_mode_is_enabled():
        return _disabled()
    body = request.get_json(silent=True) or {}
    run_input = {key: body[key] for key in ("trip_reference", "budget_cap", "note") if key in body}
    try:
        status, envelope = multi_agent_api.start_run(run_input)
    except multi_agent_api.MultiAgentServiceError as exc:
        return _unavailable(exc)
    return jsonify(envelope), status  # 201, 400 (invalid input, no run created), 500 (run failed)


@multi_agent_mode_bp.route("/budget/multi-agent/runs/<run_id>", methods=["GET"])
def get_run(run_id):
    if not multi_agent_api.multi_agent_mode_is_enabled():
        return _disabled()
    try:
        run, problem = _own_run(run_id)
    except multi_agent_api.MultiAgentServiceError as exc:
        return _unavailable(exc)
    return problem or (jsonify({"status": "success", "result": run}), 200)


@multi_agent_mode_bp.route("/budget/multi-agent/runs/<run_id>/history", methods=["GET"])
def get_history(run_id):
    if not multi_agent_api.multi_agent_mode_is_enabled():
        return _disabled()
    try:
        _, problem = _own_run(run_id)
        if problem:
            return problem
        status, envelope = multi_agent_api.get_history(run_id)
    except multi_agent_api.MultiAgentServiceError as exc:
        return _unavailable(exc)
    return jsonify(envelope), status


@multi_agent_mode_bp.route("/budget/multi-agent/runs/<run_id>/review", methods=["POST"])
def review_run(run_id):
    if not multi_agent_api.multi_agent_mode_is_enabled():
        return _disabled()
    body = request.get_json(silent=True) or {}
    decision = body.get("decision")
    if decision not in DECISIONS:
        return _error(400, f"decision must be one of {', '.join(DECISIONS)}")
    edits = body.get("edits") or {}
    problem = _validate_edits(edits)
    if problem:  # checked before the decision is sent: a decision can only be made once
        return _error(400, problem)

    try:
        run, problem = _own_run(run_id)
        if problem:
            return problem
        review = {"decision": decision, "approved_ids": body.get("approved_ids") or [],
                  "rejected_ids": body.get("rejected_ids") or [], "edits": edits,
                  "note": body.get("note"), "reviewer": body.get("reviewer") or "budget-ui"}
        status, envelope = multi_agent_api.submit_review(run_id, review)
    except multi_agent_api.MultiAgentServiceError as exc:
        return _unavailable(exc)
    if status != 200:
        return jsonify(envelope), status  # 400 bad ids, 409 already decided: nothing written

    result = envelope["result"]
    trip_reference = run["input"]["trip_reference"]
    created_ids = []
    try:
        for item in result["approved_items"]:
            created = database_api.create_expense({
                "trip_reference": trip_reference,
                "expense": item["expense"],
                "category": item["category"],
                "estimated_cost": item["estimated_cost"],
                "destination_id": item["destination_id"],
                "status": "Planned",
            })
            created_ids.append(created.get("id"))
    except (database_api.DatabaseServiceError, database_api.ValidationError) as exc:
        for expense_id in created_ids:  # no half-applied decisions
            try:
                database_api.delete_expense(expense_id)
            except (database_api.DatabaseServiceError, database_api.NotFoundError):
                logger.exception("rollback failed for expense %s (run %s)", expense_id, run_id)
        return _error(502, f"budget-db write failed, nothing was saved (decision '{decision}' is recorded on "
                           f"run {run_id}): {exc}")

    applied_recorded = None
    if result["state"] != "rejected":
        try:
            applied_status, _ = multi_agent_api.report_applied(run_id, created_ids)
            applied_recorded = applied_status == 200
        except multi_agent_api.MultiAgentServiceError:
            logger.exception("could not report applied ids for run %s", run_id)
            applied_recorded = False

    return jsonify({"status": "success", "result": {
        "run_id": run_id,
        "decision": decision,
        "state": "applied" if applied_recorded else result["state"],
        "created_expense_ids": created_ids,
        "approved_items": result["approved_items"],
        "rejected_ids": result["rejected_ids"],
        "applied_recorded": applied_recorded,
    }}), 200
