"""Multi-Agent mode: Accommodation's accommodation_shortlist workflow on the shared Multi-Agent
Server.

Plan / Act / Observe happen on the server (Planner -> Worker -> Reviewer). This blueprint is the
Human Review -> Adapt half: it forwards the person's decision and reports the run closed.
accommodation_shortlist is read-only (no write tool) and never touches accommodation-db, so there
is nothing for this backend to apply beyond closing the run's lifecycle.
"""
import logging

from flask import Blueprint, jsonify, request

from services import multi_agent_api

logger = logging.getLogger(__name__)
multi_agent_mode_bp = Blueprint("multi_agent_mode", __name__)

DECISIONS = ("approve", "partial", "correct", "reject")


def _disabled():
    return jsonify({"status": "error", "error": "Multi-Agent mode is disabled (cloud default)."}), 403


def _error(status, message):
    return jsonify({"status": "error", "error": message}), status


def _unavailable(exc):
    return _error(502, f"Multi-Agent Server unavailable: {exc}")


def _own_run(run_id):
    """(run, None) for an accommodation_shortlist run, else (None, flask_response)."""
    status, body = multi_agent_api.get_run(run_id)
    if status == 404:
        return None, _error(404, f"unknown run: {run_id}")
    if status != 200:
        return None, (jsonify(body), status)
    run = body["result"]
    if run.get("workflow") != multi_agent_api.WORKFLOW:
        return None, _error(404, f"run {run_id} is not an Accommodation shortlist")
    return run, None


@multi_agent_mode_bp.route("/accommodation/multi-agent/run", methods=["POST"])
def run_shortlist():
    if not multi_agent_api.multi_agent_mode_is_enabled():
        return _disabled()
    body = request.get_json(silent=True) or {}
    run_input = {key: body[key] for key in ("destination", "budget_cap", "interests") if key in body}
    try:
        status, envelope = multi_agent_api.start_run(run_input)
    except multi_agent_api.MultiAgentServiceError as exc:
        return _unavailable(exc)
    return jsonify(envelope), status  # 201, 400 (invalid input, no run created), 500 (run failed)


@multi_agent_mode_bp.route("/accommodation/multi-agent/runs/<run_id>", methods=["GET"])
def get_run(run_id):
    if not multi_agent_api.multi_agent_mode_is_enabled():
        return _disabled()
    try:
        run, problem = _own_run(run_id)
    except multi_agent_api.MultiAgentServiceError as exc:
        return _unavailable(exc)
    return problem or (jsonify({"status": "success", "result": run}), 200)


@multi_agent_mode_bp.route("/accommodation/multi-agent/runs/<run_id>/history", methods=["GET"])
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


@multi_agent_mode_bp.route("/accommodation/multi-agent/runs/<run_id>/review", methods=["POST"])
def review_run(run_id):
    if not multi_agent_api.multi_agent_mode_is_enabled():
        return _disabled()
    body = request.get_json(silent=True) or {}
    decision = body.get("decision")
    if decision not in DECISIONS:
        return _error(400, f"decision must be one of {', '.join(DECISIONS)}")

    try:
        run, problem = _own_run(run_id)
        if problem:
            return problem
        review = {"decision": decision, "approved_ids": body.get("approved_ids") or [],
                  "rejected_ids": body.get("rejected_ids") or [],
                  "note": body.get("note"), "reviewer": body.get("reviewer") or "accommodation-ui"}
        status, envelope = multi_agent_api.submit_review(run_id, review)
    except multi_agent_api.MultiAgentServiceError as exc:
        return _unavailable(exc)
    if status != 200:
        return jsonify(envelope), status  # 400 bad ids, 409 already decided: nothing written

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
        "decision": decision,
        "state": "applied" if applied_recorded else result["state"],
        "approved_items": result["approved_items"],
        "rejected_ids": result["rejected_ids"],
        "applied_recorded": applied_recorded,
    }}), 200
