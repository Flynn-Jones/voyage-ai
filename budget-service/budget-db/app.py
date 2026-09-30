"""Standalone entry point for the budget database service (port 6004)."""
from datetime import datetime, timezone

from flask import Flask, jsonify, request

from init_db import get_connection

REQUIRED_FIELDS = ["trip_reference", "expense", "category", "estimated_cost", "destination_id"]


def create_app():
    app = Flask(__name__)

    @app.route("/")
    def health():
        return jsonify({"service": "budget-db", "status": "running"})

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
        data = request.get_json(silent=True) or {}
        missing = [field for field in REQUIRED_FIELDS if data.get(field) in (None, "")]
        if missing:
            return jsonify({"error": f"missing required fields: {', '.join(missing)}"}), 400

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
        data = request.get_json(silent=True) or {}

        conn = get_connection()
        existing = conn.execute("SELECT * FROM expenses WHERE id = ?", (expense_id,)).fetchone()
        if existing is None:
            conn.close()
            return jsonify({"error": "expense not found"}), 404

        updatable_fields = [
            "trip_reference", "expense", "category", "estimated_cost",
            "actual_cost", "destination_id", "status",
        ]
        updates = {field: data[field] for field in updatable_fields if field in data}
        updates["updated_at"] = datetime.now(timezone.utc).isoformat()

        set_clause = ", ".join(f"{field} = ?" for field in updates)
        params = list(updates.values()) + [expense_id]
        conn.execute(f"UPDATE expenses SET {set_clause} WHERE id = ?", params)
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
    app.run(host="0.0.0.0", port=6004, debug=True)
