"""AI-facing routes for the Activity Manager conversational chat mode."""
from flask import Blueprint, jsonify, request

from services import ai_client

ai_api = Blueprint("ai_api", __name__)


@ai_api.route("/api/activity/ai-chat", methods=["POST"])
def ai_chat():
    data = request.get_json(silent=True)
    message = (data or {}).get("message", "")
    if not isinstance(message, str) or not message.strip():
        return jsonify({"error": "message is required"}), 400

    context = (data or {}).get("context")

    try:
        reply = ai_client.ask(message.strip(), context)
    except ai_client.AIServiceError as exc:
        return jsonify({"error": f"AI chat unavailable: {exc}"}), 502

    return jsonify({"reply": reply})
