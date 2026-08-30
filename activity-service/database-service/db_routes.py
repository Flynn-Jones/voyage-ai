"""Read-only REST endpoints over the activity-service database."""
from flask import Blueprint, jsonify

from init_db import get_connection

db_api = Blueprint("db_api", __name__)


@db_api.route("/health")
def health():
    return jsonify({"status": "ok", "service": "database"})


@db_api.route("/activities")
def list_activities():
    conn = get_connection()
    rows = conn.execute("SELECT * FROM activities").fetchall()
    conn.close()
    return jsonify([dict(row) for row in rows])


@db_api.route("/activities/<int:activity_id>")
def get_activity(activity_id):
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM activities WHERE activity_id = ?", (activity_id,)
    ).fetchone()
    conn.close()
    if row is None:
        return jsonify({"error": "activity not found"}), 404
    return jsonify(dict(row))


@db_api.route("/assignments")
def list_assignments():
    conn = get_connection()
    rows = conn.execute("SELECT * FROM activities_assignment").fetchall()
    conn.close()
    return jsonify([dict(row) for row in rows])


@db_api.route("/assignments/<int:assignment_id>")
def get_assignment(assignment_id):
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM activities_assignment WHERE assignment_id = ?", (assignment_id,)
    ).fetchone()
    conn.close()
    if row is None:
        return jsonify({"error": "assignment not found"}), 404
    return jsonify(dict(row))


@db_api.route("/activities/<int:activity_id>/assignments")
def get_activity_assignments(activity_id):
    conn = get_connection()
    activity = conn.execute(
        "SELECT * FROM activities WHERE activity_id = ?", (activity_id,)
    ).fetchone()
    if activity is None:
        conn.close()
        return jsonify({"error": "activity not found"}), 404
    rows = conn.execute(
        "SELECT * FROM activities_assignment WHERE activity_id = ?", (activity_id,)
    ).fetchall()
    conn.close()
    return jsonify([dict(row) for row in rows])
