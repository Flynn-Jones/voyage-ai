"""Activity Manager business logic: validates input, delegates to services, returns JSON."""
from flask import Blueprint, jsonify, request

from services import activity_store, ai_client, db_client

activity_api = Blueprint("activity_api", __name__)

REQUIRED_ACTIVITY_FIELDS = ("activity_name", "activity_type", "activity_cost", "duration")


@activity_api.route("/health")
def health():
    return jsonify({"status": "ok", "service": "activity-backend"})


@activity_api.route("/api/view_activities")
def view_activities():
    try:
        activities = db_client.get_activities()
    except db_client.DatabaseServiceError:
        return jsonify({"error": "database service unavailable"}), 502
    return jsonify(activities)


@activity_api.route("/api/view_activity/<int:activity_id>")
def view_activity(activity_id):
    try:
        activity = db_client.get_activity(activity_id)
    except db_client.NotFoundError:
        return jsonify({"error": "activity not found"}), 404
    except db_client.DatabaseServiceError:
        return jsonify({"error": "database service unavailable"}), 502
    return jsonify(activity)


@activity_api.route("/api/activities/<int:activity_id>/assignments")
def activity_assignments(activity_id):
    try:
        assignments = db_client.get_activity_assignments(activity_id)
    except db_client.NotFoundError:
        return jsonify({"error": "activity not found"}), 404
    except db_client.DatabaseServiceError:
        return jsonify({"error": "database service unavailable"}), 502
    return jsonify(assignments)


def _validate_activity_payload(data):
    if not data:
        return "request body must be JSON"
    missing = [field for field in REQUIRED_ACTIVITY_FIELDS if not data.get(field)]
    if missing:
        return f"missing required field(s): {', '.join(missing)}"
    try:
        float(data["activity_cost"])
    except (TypeError, ValueError):
        return "activity_cost must be a number"
    return None


@activity_api.route("/api/add_activity", methods=["POST"])
def add_activity():
    data = request.get_json(silent=True)
    error = _validate_activity_payload(data)
    if error:
        return jsonify({"error": error}), 400

    created = activity_store.create_activity(data)
    return jsonify(created), 201


@activity_api.route("/api/edit_activity/<int:activity_id>", methods=["PATCH"])
def edit_activity(activity_id):
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "request body must be JSON"}), 400
    if "activity_cost" in data:
        try:
            float(data["activity_cost"])
        except (TypeError, ValueError):
            return jsonify({"error": "activity_cost must be a number"}), 400

    try:
        updated = activity_store.update_activity(activity_id, data)
    except activity_store.ActivityNotFoundError:
        return jsonify({"error": "activity not found"}), 404
    return jsonify(updated)


@activity_api.route("/api/delete_activity/<int:activity_id>", methods=["DELETE"])
def delete_activity(activity_id):
    try:
        activity_store.delete_activity(activity_id)
    except activity_store.ActivityNotFoundError:
        return jsonify({"error": "activity not found"}), 404
    return jsonify({"message": "deleted"})


@activity_api.route("/api/activity/ai-summary", methods=["POST"])
def activity_ai_summary():
    data = request.get_json(silent=True)
    if not data or not data.get("activity_id"):
        return jsonify({"error": "activity_id is required"}), 400

    activity_id = data["activity_id"]
    try:
        activity = db_client.get_activity(activity_id)
    except db_client.NotFoundError:
        return jsonify({"error": "activity not found"}), 404
    except db_client.DatabaseServiceError:
        return jsonify({"error": "database service unavailable"}), 502

    try:
        summary = ai_client.generate_summary(activity)
    except ai_client.AIServiceError as exc:
        return jsonify({"error": f"AI summary unavailable: {exc}"}), 502

    return jsonify({"summary": summary})
