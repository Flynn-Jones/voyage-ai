"""AI-mode routes: spending analysis and a Plan -> Act -> Observe -> Adapt expense creation loop."""
import json
import logging

from flask import Blueprint, jsonify, request

from services import database_api, llm_client, reference_api

ai_mode_bp = Blueprint("ai_mode", __name__)
logger = logging.getLogger(__name__)


@ai_mode_bp.route("/budget/trips")
def list_trips():
    try:
        expenses = database_api.get_expenses()
    except database_api.DatabaseServiceError:
        return jsonify({"error": "Budget database is unavailable."}), 502

    trips = sorted({expense["trip_reference"] for expense in expenses})
    return jsonify({"trips": trips})


@ai_mode_bp.route("/budget/ai-analyse", methods=["POST"])
def ai_analyse():
    body = request.get_json(silent=True) or {}
    trip_reference = (body.get("trip_reference") or "").strip()
    filters = {"trip_reference": trip_reference} if trip_reference else None

    try:
        expenses = database_api.get_expenses(filters)
    except database_api.DatabaseServiceError:
        return jsonify({"error": "Budget database is unavailable."}), 502

    if not expenses:
        message = (
            f"No expenses found for trip '{trip_reference}'." if trip_reference
            else "No expenses found."
        )
        return jsonify({"analysis": message})

    evidence = json.dumps(expenses, indent=2)

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

    trip_reference = plan.get("trip_reference")
    if not trip_reference:
        logger.info("[PLAN] No trip reference found in prompt; asking for clarification.")
        return jsonify({
            "clarification": "I couldn't tell which trip this expense is for. "
                              "Could you name the trip reference (e.g. 'TRIP-1001')?"
        })

    destination = plan.get("destination")
    match = None
    note = None

    if destination:
        logger.info("[ACT] Querying accommodation reference data for destination=%s", destination)
        try:
            accommodations = reference_api.get_accommodation(destination)
        except reference_api.NotFoundError:
            accommodations = []
        except reference_api.ReferenceServiceError:
            logger.info("[ACT] Accommodation reference service unavailable.")
            return jsonify({"error": "Accommodation reference service is unavailable."}), 502
        logger.info("[ACT] Found %d accommodation record(s).", len(accommodations))

        if accommodations:
            match = accommodations[0]
            if len(accommodations) > 1:
                note = (
                    f"Multiple accommodation matches found for '{destination}'; "
                    f"used the first ({match.get('name')})."
                )
        else:
            logger.info("[OBSERVE] No accommodation match found for '%s'.", destination)

    # Only let the accommodation match override what the traveller already told
    # us: use it as the expense itself when they're clearly booking a stay, or
    # to fill in whatever they left out (name and/or cost).
    category = (plan.get("category") or "").strip()
    is_lodging = category.lower() in {"accommodation", "hotel", "lodging"}
    missing_details = not plan.get("expense") or plan.get("estimated_cost") in (None, "")

    if match and (is_lodging or missing_details):
        logger.info("[ADAPT] Using accommodation match to fill in expense details: %s", match.get("name"))
        expense_name = match.get("name") or plan.get("expense") or f"Accommodation in {destination}"
        category = "Accommodation"
        estimated_cost = match.get("price_per_night")
        if estimated_cost is None:
            estimated_cost = plan.get("estimated_cost")
        destination_id = match.get("destination_id")
    else:
        expense_name = plan.get("expense")
        estimated_cost = plan.get("estimated_cost")
        destination_id = match.get("destination_id") if match else None
        note = None

    if not destination_id:
        logger.info("[OBSERVE] No destination_id yet; checking trip '%s' for prior expenses.", trip_reference)
        try:
            trip_expenses = database_api.get_expenses({"trip_reference": trip_reference})
        except database_api.DatabaseServiceError:
            trip_expenses = []
        if trip_expenses:
            destination_id = trip_expenses[0].get("destination_id")

    if not expense_name:
        return jsonify({
            "clarification": "I couldn't tell what this expense was for. "
                              "Could you describe it (e.g. 'dinner', 'museum tickets')?"
        })
    if estimated_cost in (None, ""):
        return jsonify({
            "clarification": f"How much did '{expense_name}' cost (or is expected to cost)?"
        })
    try:
        estimated_cost = float(estimated_cost)
    except (TypeError, ValueError):
        return jsonify({
            "clarification": f"I couldn't understand the cost for '{expense_name}'. Could you give me a number?"
        })
    if not destination_id:
        return jsonify({
            "clarification": f"I don't have a destination on file for {trip_reference} yet. "
                              "Which destination is this for (e.g. 'Tokyo')?"
        })

    payload = {
        "trip_reference": trip_reference,
        "expense": expense_name,
        "category": category or "Other",
        "estimated_cost": estimated_cost,
        "destination_id": destination_id,
        "status": "Planned",
    }
    logger.info("[ADAPT] Creating expense: %s", payload)
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
    if note:
        response["note"] = note
    return jsonify(response), 201
