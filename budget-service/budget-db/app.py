"""Standalone entry point for the budget database service (port 6004)."""
import os
from datetime import datetime, timezone

from flask import Flask, jsonify, request

from init_db import get_connection

REQUIRED_FIELDS = ["trip_reference", "expense", "category", "estimated_cost", "destination_id"]
TEXT_LIMITS = {"trip_reference": 120, "expense": 200, "category": 40, "destination_id": 80}
COST_FIELDS = ("estimated_cost", "actual_cost")
STATUSES = ("Planned", "Paid", "Cancelled")
UPDATABLE_FIELDS = ("trip_reference", "expense", "category", "estimated_cost", "actual_cost", "destination_id", "status")


def validate_expense(data, partial=False):
    """Return an error message, or None if `data` is a valid expense (or a valid partial update)."""
    if not isinstance(data, dict):
        return "request body must be a JSON object"
    if partial:
        if not any(field in data for field in UPDATABLE_FIELDS):
            return f"no updatable fields provided (one of: {', '.join(UPDATABLE_FIELDS)})"
    else:
        missing = [field for field in REQUIRED_FIELDS if data.get(field) in (None, "")]
        if missing:
            return f"missing required fields: {', '.join(missing)}"
    for field, limit in TEXT_LIMITS.items():
        if field in data and (not isinstance(data[field], str) or not data[field].strip() or len(data[field]) > limit):
            return f"{field} must be a non-empty string of at most {limit} characters"
    for field in COST_FIELDS:
        if field not in data or (field == "actual_cost" and data[field] is None):
            continue
        value = data[field]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value != value or value < 0:
            return f"{field} must be a number >= 0"
    if "status" in data and data["status"] not in STATUSES:
        return f"status must be one of {', '.join(STATUSES)}"
    return None


def create_app():
    app = Flask(__name__)

    @app.route("/")
    def health():
        return jsonify({"service": "budget-db", "status": "running"})

    @app.route("/health")
    def healthcheck():
        """Liveness only: does not open the database file."""
        return jsonify({"service": "budget-db", "status": "ok"})

    @app.route("/expenses")
    def list_expenses():
        filters = []
        params = []

        for field in ("category", "destination_id", "status", "trip_reference"):
            value = request.args.get(field)
            if value is not None:
                filters.append(f"{field} = ?")
                params.append(value)

        query = "SELECT * FROM expenses"
        if filters:
            query += " WHERE " + " AND ".join(filters)

        conn = get_connection()
        rows = conn.execute(query, params).fetchall()
        conn.close()
        return jsonify([dict(row) for row in rows])

    @app.route("/expenses/<int:expense_id>")
    def get_expense(expense_id):
        conn = get_connection()
        row = conn.execute("SELECT * FROM expenses WHERE id = ?", (expense_id,)).fetchone()
        conn.close()
        if row is None:
            return jsonify({"error": "expense not found"}), 404
        return jsonify(dict(row))

    @app.route("/expenses", methods=["POST"])
    def create_expense():
        data = request.get_json(silent=True)
        error = validate_expense(data)
        if error:
            return jsonify({"error": error}), 400

        now = datetime.now(timezone.utc).isoformat()
        conn = get_connection()
        cursor = conn.execute(
            """
            INSERT INTO expenses (
                trip_reference, expense, category, estimated_cost, actual_cost,
                destination_id, status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["trip_reference"],
                data["expense"],
                data["category"],
                data["estimated_cost"],
                data.get("actual_cost"),
                data["destination_id"],
                data.get("status", "Planned"),
                now,
                now,
            ),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM expenses WHERE id = ?", (cursor.lastrowid,)).fetchone()
        conn.close()
        return jsonify(dict(row)), 201

    @app.route("/expenses/<int:expense_id>", methods=["PUT"])
    def update_expense(expense_id):
        data = request.get_json(silent=True)

        conn = get_connection()
        existing = conn.execute("SELECT * FROM expenses WHERE id = ?", (expense_id,)).fetchone()
        if existing is None:
            conn.close()
            return jsonify({"error": "expense not found"}), 404
        error = validate_expense(data, partial=True)
        if error:
            conn.close()
            return jsonify({"error": error}), 400

        updates = {field: data[field] for field in UPDATABLE_FIELDS if field in data}
        updates["updated_at"] = datetime.now(timezone.utc).isoformat()

        # Column names come only from the UPDATABLE_FIELDS whitelist; every value is a bound parameter.
        set_clause = ", ".join(f"{field} = ?" for field in updates)
        params = list(updates.values()) + [expense_id]
        conn.execute(f"UPDATE expenses SET {set_clause} WHERE id = ?", params)  # noqa: S608 - whitelisted column names, bound values
        conn.commit()
        row = conn.execute("SELECT * FROM expenses WHERE id = ?", (expense_id,)).fetchone()
        conn.close()
        return jsonify(dict(row))

    @app.route("/expenses/<int:expense_id>", methods=["DELETE"])
    def delete_expense(expense_id):
        conn = get_connection()
        existing = conn.execute("SELECT * FROM expenses WHERE id = ?", (expense_id,)).fetchone()
        if existing is None:
            conn.close()
            return jsonify({"error": "expense not found"}), 404

        conn.execute("DELETE FROM expenses WHERE id = ?", (expense_id,))
        conn.commit()
        conn.close()
        return "", 204

    return app


app = create_app()


if __name__ == "__main__":
    # Local dev only (containers run gunicorn). Container networking needs all interfaces;
    # the Werkzeug debugger only with FLASK_DEBUG=1 because it allows code execution.
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "6004")),  # noqa: S104 - container bind
            debug=os.environ.get("FLASK_DEBUG") == "1")
