"""REST endpoints over the activity-service database."""
from flask import Blueprint, jsonify, request

from init_db import get_connection

db_api = Blueprint("db_api", __name__)

ACTIVITY_FIELDS = ("activity_name", "activity_type", "activity_cost", "duration")


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


@db_api.route("/activities", methods=["POST"])
def create_activity():
    data = request.get_json(silent=True) or {}
    missing = [field for field in ACTIVITY_FIELDS if field not in data]
    if missing:
        return jsonify({"error": f"missing required field(s): {', '.join(missing)}"}), 400
    try:
        cost = float(data["activity_cost"])
    except (TypeError, ValueError):
        return jsonify({"error": "activity_cost must be a number"}), 400

    conn = get_connection()
    cursor = conn.execute(
        "INSERT INTO activities (activity_name, activity_type, activity_cost, duration) "
        "VALUES (?, ?, ?, ?)",
        (data["activity_name"], data["activity_type"], cost, data["duration"]),
    )
    activity_id = cursor.lastrowid

    assignment_time = data.get("assignment_time")
    assignment_id = None
    if assignment_time:
        assignment_cursor = conn.execute(
            "INSERT INTO activities_assignment (activity_id, assignment_time) VALUES (?, ?)",
            (activity_id, assignment_time),
        )
        assignment_id = assignment_cursor.lastrowid

    conn.commit()
    conn.close()

    result = {
        "activity_id": activity_id,
        "activity_name": data["activity_name"],
        "activity_type": data["activity_type"],
        "activity_cost": cost,
        "duration": data["duration"],
    }
    if assignment_id is not None:
        result["assignment_id"] = assignment_id
        result["assignment_time"] = assignment_time
    return jsonify(result), 201


@db_api.route("/activities/<int:activity_id>", methods=["PATCH"])
def update_activity(activity_id):
    conn = get_connection()
    existing = conn.execute(
        "SELECT * FROM activities WHERE activity_id = ?", (activity_id,)
    ).fetchone()
    if existing is None:
        conn.close()
        return jsonify({"error": "activity not found"}), 404

    data = request.get_json(silent=True) or {}
    fields = {key: data[key] for key in ACTIVITY_FIELDS if key in data}
    if "activity_cost" in fields:
        try:
            fields["activity_cost"] = float(fields["activity_cost"])
        except (TypeError, ValueError):
            conn.close()
            return jsonify({"error": "activity_cost must be a number"}), 400

    if fields:
        set_clause = ", ".join(f"{key} = ?" for key in fields)
        conn.execute(
            f"UPDATE activities SET {set_clause} WHERE activity_id = ?",  # noqa: S608 - column names come only from ACTIVITY_FIELDS; values are bound
            (*fields.values(), activity_id),
        )

    if "assignment_time" in data:
        existing_assignment = conn.execute(
            "SELECT * FROM activities_assignment WHERE activity_id = ?", (activity_id,)
        ).fetchone()
        if existing_assignment:
            conn.execute(
                "UPDATE activities_assignment SET assignment_time = ? WHERE activity_id = ?",
                (data["assignment_time"], activity_id),
            )
        else:
            conn.execute(
                "INSERT INTO activities_assignment (activity_id, assignment_time) VALUES (?, ?)",
                (activity_id, data["assignment_time"]),
            )

    conn.commit()
    updated = conn.execute(
        "SELECT * FROM activities WHERE activity_id = ?", (activity_id,)
    ).fetchone()
    conn.close()
    return jsonify(dict(updated))


@db_api.route("/activities/<int:activity_id>", methods=["DELETE"])
def delete_activity(activity_id):
    conn = get_connection()
    existing = conn.execute(
        "SELECT * FROM activities WHERE activity_id = ?", (activity_id,)
    ).fetchone()
    if existing is None:
        conn.close()
        return jsonify({"error": "activity not found"}), 404

    conn.execute("DELETE FROM activities_assignment WHERE activity_id = ?", (activity_id,))
    conn.execute("DELETE FROM activities WHERE activity_id = ?", (activity_id,))
    conn.commit()
    conn.close()
    return jsonify({"message": "deleted"})


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
