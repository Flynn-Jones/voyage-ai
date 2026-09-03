"""Plan -> Act -> Observe -> Adapt itinerary review endpoint."""
import logging
import re

from flask import Blueprint, jsonify, request

from services import database_api, llm_client
from services.schedule_analyzer import analyze_schedule


ai_review_bp = Blueprint("ai_review", __name__, url_prefix="/api/itinerary")
logger = logging.getLogger(__name__)


def _requested_day(body):
    day = body.get("day")
    if day is None:
        match = re.search(r"\bday\s*(\d+)\b", body["prompt"], re.IGNORECASE)
        day = int(match.group(1)) if match else None
    if isinstance(day, bool) or not isinstance(day, int) or day < 1:
        raise ValueError("day must be a positive integer or be stated in the prompt (for example, Day 4)")
    return day


@ai_review_bp.post("/ai-review")
def ai_review():
    body = request.get_json(silent=True)
    if not isinstance(body, dict) or not isinstance(body.get("prompt"), str) or not body["prompt"].strip():
        return jsonify({"error": "prompt is required and must be a non-empty string"}), 400
    try:
        day = _requested_day(body)
    except ValueError as error:
        return jsonify({"error": str(error)}), 400

    plan = {
        "requested_day": day,
        "data_required": "all itinerary records for the requested day",
        "checks": [
            "schedule coverage and duration",
            "overlapping itinerary items",
            "short gaps and continuous-day intensity",
            "estimated cost",
            "missing or invalid schedule values",
        ],
    }
    logger.info("[PLAN] Review Day %s using deterministic schedule checks", day)

    try:
        items = database_api.get_items_by_day(day)
    except database_api.DatabaseServiceError:
        logger.warning("[ACT] Database API unavailable while retrieving Day %s", day)
        return jsonify({"error": "itinerary database service is unavailable"}), 502
    act = {"source": "itinerary database API over HTTP", "requested_day": day, "records_retrieved": len(items)}
    logger.info("[ACT] Retrieved %s Day %s records", len(items), day)

    observations = analyze_schedule(items)
    logger.info("[OBSERVE] Day %s: %s", day, observations)

    try:
        adaptation = llm_client.review_itinerary(body["prompt"].strip(), day, items, observations)
    except llm_client.LLMServiceError:
        logger.warning("[ADAPT] Ollama unavailable or returned an invalid response")
        return jsonify({"error": "AI review service is unavailable"}), 502
    adapt = {**adaptation, "advisory_only": True}
    logger.info("[ADAPT] Generated advisory recommendation with %s", adapt["model"])

    return jsonify({"plan": plan, "act": act, "observe": observations, "adapt": adapt})
