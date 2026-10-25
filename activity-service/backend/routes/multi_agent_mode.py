"""Multi-Agent mode: Activity's activity_schedule_check workflow on the shared Multi-Agent Server.

Planner -> Worker -> Reviewer run on the server (:7004). This blueprint starts runs, shows them,
and records the Human decision: Accept, Partially Accept or Reject, with the correction, reason,
evidence used, reviewer and timestamp written into the run's decision note. Agents only
recommend; nothing here writes to the activity database.

Gated by MULTI_AGENT_ENABLED (false in the cloud): every route answers 403 when it is off.
"""
import logging
from datetime import datetime, timezone

from flask import Blueprint, jsonify, request

from services import multi_agent_api

logger = logging.getLogger(__name__)
multi_agent_mode_bp = Blueprint("multi_agent_mode", __name__)

# Human decision (Lab 10 values) -> shared server decision. Partially Accept carries the correction.
DECISIONS = {"Accept": "approve", "Partially Accept": "partial", "Reject": "reject"}
RUN_FIELDS = ("date", "slot_hours", "request")
MAX_TEXT = 500


def _disabled():
    return jsonify({"status": "error", "error": "Multi-Agent mode is disabled."}), 403


def _error(status, message):
    return jsonify({"status": "error", "error": message}), status


def _unavailable(exc):
    return _error(502, f"Multi-Agent Server unavailable: {exc}")


def _with_human_decision(run):
    """Add the Lab 10 human_decision block: pending until a person decides."""
    pending = run.get("state") == "awaiting_human_review"
    decision = run.get("decision") or {}
    return {**run, "human_approval_required": True, "human_decision": {
        "status": "pending" if pending else run.get("state"),
        "allowed_values": list(DECISIONS),
        "record": decision.get("note"),
    }}


def _own_run(run_id):
    """(run, None) for an activity_schedule_check run, else (None, flask_response)."""
    status, body = multi_agent_api.get_run(run_id)
    if status == 404:
        return None, _error(404, f"unknown run: {run_id}")
    if status != 200:
        return None, (jsonify(body), status)
    run = body["result"]
    if run.get("workflow") != multi_agent_api.WORKFLOW:
        return None, _error(404, f"run {run_id} is not an Activity schedule check")
    return run, None


def _text(body, key):
    value = body.get(key)
    if value is None:
        return ""
    if not isinstance(value, str) or len(value) > MAX_TEXT:
        raise ValueError(f"{key} must be a string of at most {MAX_TEXT} characters")
    return value.strip()


@multi_agent_mode_bp.route("/api/activity/multi-agent/workflow", methods=["POST"])
def start_workflow():
    if not multi_agent_api.multi_agent_mode_is_enabled():
        return _disabled()
    body = request.get_json(silent=True) or {}
    run_input = {key: body[key] for key in RUN_FIELDS if key in body}
    try:
        status, envelope = multi_agent_api.start_run(run_input)
    except multi_agent_api.MultiAgentServiceError as exc:
        return _unavailable(exc)
    if status == 201:
        envelope = {**envelope, "result": _with_human_decision(envelope["result"])}
    return jsonify(envelope), status  # 201, 400 (invalid input, no run created), 500 (run failed)


@multi_agent_mode_bp.route("/api/activity/multi-agent/runs/<run_id>", methods=["GET"])
def get_run(run_id):
    if not multi_agent_api.multi_agent_mode_is_enabled():
        return _disabled()
    try:
        run, problem = _own_run(run_id)
    except multi_agent_api.MultiAgentServiceError as exc:
        return _unavailable(exc)
    return problem or (jsonify({"status": "success", "result": _with_human_decision(run)}), 200)


@multi_agent_mode_bp.route("/api/activity/multi-agent/runs/<run_id>/history", methods=["GET"])
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


@multi_agent_mode_bp.route("/api/activity/multi-agent/runs/<run_id>/decision", methods=["POST"])
def record_decision(run_id):
    if not multi_agent_api.multi_agent_mode_is_enabled():
        return _disabled()
    body = request.get_json(silent=True) or {}
    decision = body.get("decision")
    if decision not in DECISIONS:
        return _error(400, f"decision must be one of {', '.join(DECISIONS)}")
    try:
        correction, reason = _text(body, "correction"), _text(body, "reason")
        evidence_used, reviewer = _text(body, "evidence_used"), _text(body, "reviewer")
    except ValueError as exc:
        return _error(400, str(exc))
    if not reason:
        return _error(400, "reason is required")
    if not reviewer:
        return _error(400, "reviewer is required")
    if decision == "Partially Accept" and not correction:
        return _error(400, "Partially Accept needs a correction")
    approved_ids = body.get("approved_ids") or []
    if not isinstance(approved_ids, list):
        return _error(400, "approved_ids must be a list")

    timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    record = {"workflow_id": run_id, "decision": decision, "correction": correction, "reason": reason,
              "evidence_used": evidence_used, "reviewer": reviewer, "timestamp": timestamp}
    note = "\n".join([
        f"Workflow ID: {run_id}",
        f"Decision: {decision}",
        f"Correction: {correction or '-'}",
        f"Reason: {reason}",
        f"Evidence Used: {evidence_used or '-'}",
        f"Reviewer: {reviewer}",
        f"Timestamp: {timestamp}",
    ])

    try:
        run, problem = _own_run(run_id)
        if problem:
            return problem
        review = {"decision": DECISIONS[decision], "note": note, "reviewer": reviewer}
        if decision == "Partially Accept":
            # Approve the ticked items (all of them if none were ticked: the correction is to the text).
            review["approved_ids"] = approved_ids or [item["id"] for item in run["work"]["items"]]
            if not review["approved_ids"]:
                return _error(400, "Partially Accept needs at least one item; use Accept or Reject instead")
        status, envelope = multi_agent_api.submit_review(run_id, review)
    except multi_agent_api.MultiAgentServiceError as exc:
        return _unavailable(exc)
    if status != 200:
        return jsonify(envelope), status  # 400 bad ids, 409 already decided

    result = envelope["result"]
    applied_recorded = None
    if result["state"] != "rejected":
        try:
            applied_status, _ = multi_agent_api.report_applied(run_id)
            applied_recorded = applied_status == 200
        except multi_agent_api.MultiAgentServiceError:
            logger.exception("could not report applied state for run %s", run_id)
            applied_recorded = False

    return jsonify({"status": "success", "result": {
        "run_id": run_id,
        "state": "applied" if applied_recorded else result["state"],
        "human_decision": record,
        "approved_items": result["approved_items"],
        "rejected_ids": result["rejected_ids"],
        "applied_recorded": applied_recorded,
    }}), 200
