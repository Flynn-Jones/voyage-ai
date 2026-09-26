"""AI-facing routes for the Activity Manager conversational chat mode.

ai_chat() is the Plan -> Act -> Observe -> Adapt loop's orchestrator,
mirroring the same pattern accommodation-service's ai_recommend() and
budget-service's ai_add_expense() already use: this route owns the stage
sequencing and data-fetching, while services/ai_client.py provides the
LLM-calling and pure-filtering primitives for each stage.
"""
import logging

from flask import Blueprint, jsonify, request

from services import ai_client, db_client

ai_api = Blueprint("ai_api", __name__)
logger = logging.getLogger(__name__)


@ai_api.route("/api/activity/ai-chat", methods=["POST"])
def ai_chat():
    data = request.get_json(silent=True)
    message = (data or {}).get("message", "")
    if not isinstance(message, str) or not message.strip():
        return jsonify({"error": "message is required"}), 400
    message = message.strip()

    logger.info("[PLAN] Extracting intent from message: %r", message)
    print(f"[PLAN] Extracting intent from message: {message!r}", flush=True)
    try:
        intent, used_fallback = ai_client.extract_intent(message)
    except ai_client.AIServiceError as exc:
        return jsonify({"error": f"AI chat unavailable: {exc}"}), 502
    logger.info("[PLAN] Intent: %s (fallback=%s)", intent, used_fallback)
    print(f"[PLAN] Intent: {intent} (fallback={used_fallback})", flush=True)

    logger.info("[ACT] Fetching activities and applying filters")
    print("[ACT] Fetching activities and applying filters", flush=True)
    try:
        activities = db_client.get_activities()
    except db_client.DatabaseServiceError as exc:
        return jsonify({"error": f"Activity database unavailable: {exc}"}), 502
    candidates = ai_client.filter_candidates(activities, intent)
    logger.info("[ACT] %d candidate(s) out of %d total", len(candidates), len(activities))
    print(f"[ACT] {len(candidates)} candidate(s) out of {len(activities)} total", flush=True)

    observation = ai_client.classify_candidates(candidates)
    logger.info("[OBSERVE] status=%s count=%d", observation["status"], observation["count"])
    print(f"[OBSERVE] status={observation['status']} count={observation['count']}", flush=True)

    logger.info("[ADAPT] status=%s", observation["status"])
    print(f"[ADAPT] status={observation['status']}", flush=True)
    if observation["status"] == "none":
        reply = ai_client.format_no_match_reply(intent, used_fallback)
    elif observation["status"] == "many":
        reply = ai_client.format_ambiguous_reply(observation["candidates"])
    else:
        try:
            reply = ai_client.generate_grounded_reply(message, observation["candidates"])
        except ai_client.AIServiceError as exc:
            return jsonify({"error": f"AI chat unavailable: {exc}"}), 502

    return jsonify({"reply": reply})
