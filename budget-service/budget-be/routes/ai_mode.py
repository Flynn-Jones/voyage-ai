"""AI-mode routes: spending analysis and a Plan -> Act -> Observe -> Adapt expense creation loop."""
import json
import logging

from flask import Blueprint, jsonify, request

from services import database_api, llm_client, reference_api

ai_mode_bp = Blueprint("ai_mode", __name__)
logger = logging.getLogger(__name__)


@ai_mode_bp.route("/budget/ai-analyse", methods=["POST"])
def ai_analyse():
    try:
        expenses = database_api.get_expenses()
    except database_api.DatabaseServiceError:
        return jsonify({"error": "Budget database is unavailable."}), 502

    evidence = json.dumps(expenses, indent=2) if expenses else "No expenses found."

    # evidence = json.dumps(expenses, indent=2) if expenses else "No expenses found."
    # logger.info("[DEBUG] Evidence sent to model:\n%s", evidence)

    try:
        analysis = llm_client.call_budget_agent("system_prompt.txt", "task_prompt.txt", evidence)
        summary = llm_client.summarise_analysis(analysis)
    except llm_client.LLMServiceError:
        return jsonify({"error": "AI service is unavailable."}), 502

    return jsonify({"analysis": summary})


@ai_mode_bp.route("/budget/ai-add-expense", methods=["POST"])
def ai_add_expense():
    body = request.get_json(silent=True) or {}
    user_prompt = (body.get("prompt") or "").strip()
    if not user_prompt:
        return jsonify({"error": "Missing 'prompt' in request body."}), 400

    logger.info("[PLAN] Parsing user prompt: %r", user_prompt)
    try:
        plan = llm_client.extract_expense_intent(user_prompt)
    except llm_client.LLMServiceError:
        logger.info("[PLAN] AI service unavailable, cannot parse prompt.")
        return jsonify({"error": "AI service is unavailable."}), 502
    logger.info("[PLAN] Extracted intent: %s", plan)

    destination = plan.get("destination")
    if not destination:
        logger.info("[PLAN] No destination found in prompt; asking for clarification.")
        return jsonify({
            "clarification": "I couldn't tell which destination this expense is for. "
                              "Could you name the destination (e.g. 'Rome', 'Bali')?"
        })

    logger.info("[ACT] Querying accommodation reference data for destination=%s", destination)
    try:
        accommodations = reference_api.get_accommodation(destination)
    except reference_api.NotFoundError:
        accommodations = []
    except reference_api.ReferenceServiceError:
        logger.info("[ACT] Accommodation reference service unavailable.")
        return jsonify({"error": "Accommodation reference service is unavailable."}), 502
    logger.info("[ACT] Found %d accommodation record(s).", len(accommodations))

    logger.info("[OBSERVE] Checking for a usable match.")
    if not accommodations:
        logger.info("[OBSERVE] No accommodation match found for '%s'.", destination)
        return jsonify({
            "clarification": f"I couldn't find accommodation data for '{destination}'. "
                              "Could you give me the expense name and cost directly?"
        })

    if len(accommodations) > 1:
        logger.info(
            "[OBSERVE] Multiple matches found (%d) for '%s'; using the first and noting ambiguity.",
            len(accommodations), destination,
        )
    else:
        logger.info("[OBSERVE] Single match found for '%s'.", destination)

    match = accommodations[0]

    logger.info("[ADAPT] Creating expense from matched accommodation: %s", match.get("name"))
    payload = {
        "trip_reference": plan.get("trip_reference") or "UNSPECIFIED",
        "expense": match.get("name", f"Accommodation in {destination}"),
        "category": "Accommodation",
        "estimated_cost": match.get("price_per_night", 0),
        "destination_id": match.get("destination_id"),
        "status": "Planned",
    }
    try:
        expense = database_api.create_expense(payload)
    except database_api.ValidationError as exc:
        logger.info("[ADAPT] Validation failed: %s", exc)
        return jsonify({"error": str(exc)}), 400
    except database_api.DatabaseServiceError:
        logger.info("[ADAPT] Budget database unavailable.")
        return jsonify({"error": "Budget database is unavailable."}), 502

    logger.info("[ADAPT] Created expense id=%s", expense.get("id"))
    response = {"created": expense}
    if len(accommodations) > 1:
        response["note"] = (
            f"Multiple accommodation matches found for '{destination}'; "
            f"used the first ({match.get('name')})."
        )
    return jsonify(response), 201
