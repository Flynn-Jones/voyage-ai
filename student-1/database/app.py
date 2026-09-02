"""Standalone entry point for the destination database service (port 6001).

Session 1 scope proved the container owns SQLite via the named volume.
Session 2 adds the /destinations CRUD routes. This is the only service in
the stack that opens the SQLite file.
"""
import json
import os

from flask import Flask, jsonify, request

from init_db import get_connection

PORT = int(os.environ.get("PORT", "6001"))

REQUIRED_FIELDS = ["city", "country"]
UPDATABLE_FIELDS = [
    "city", "country", "description", "average_daily_cost",
    "recommended_trip_length", "travel_style", "categories",
]


def _row_to_dict(row):
    data = dict(row)
    try:
        data["categories"] = json.loads(data["categories"]) if data["categories"] else []
    except (TypeError, ValueError):
        data["categories"] = []
    return data


def _encode_categories(value):
    if value is None:
        return json.dumps([])
    if isinstance(value, list):
        return json.dumps(value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except ValueError:
            parsed = None
        if isinstance(parsed, list):
            return json.dumps(parsed)
        return json.dumps([value])
    return json.dumps([])


def _validate(data, partial=False):
    """Returns (cleaned, error). cleaned has raw values ready for storage
    (categories already JSON-encoded); error is a string or None."""
    if not isinstance(data, dict):
        return None, "request body must be a JSON object"

    if not partial:
        missing = [f for f in REQUIRED_FIELDS if not data.get(f)]
        if missing:
            return None, f"missing required fields: {', '.join(missing)}"
    else:
        present = [f for f in UPDATABLE_FIELDS if f in data]
        if not present:
            return None, "no updatable fields provided"
        for f in REQUIRED_FIELDS:
            if f in data and not data.get(f):
                return None, f"{f} must not be empty"

    cleaned = {}

    if "city" in data and data["city"]:
        cleaned["city"] = str(data["city"])
    if "country" in data and data["country"]:
        cleaned["country"] = str(data["country"])
    if "description" in data:
        cleaned["description"] = data["description"]
    if "travel_style" in data:
        cleaned["travel_style"] = data["travel_style"]

    if "average_daily_cost" in data and data["average_daily_cost"] is not None:
        try:
            cost = float(data["average_daily_cost"])
        except (TypeError, ValueError):
            return None, "average_daily_cost must be a number"
        if cost < 0:
            return None, "average_daily_cost must be >= 0"
        cleaned["average_daily_cost"] = cost

    if "recommended_trip_length" in data and data["recommended_trip_length"] is not None:
        try:
            length = int(data["recommended_trip_length"])
        except (TypeError, ValueError):
            return None, "recommended_trip_length must be an integer"
        if length < 1:
            return None, "recommended_trip_length must be >= 1"
        cleaned["recommended_trip_length"] = length

    if "categories" in data:
        categories = data["categories"]
        if not (categories is None or isinstance(categories, (list, str))):
            return None, "categories must be a list of strings or a string"
        if isinstance(categories, list) and not all(isinstance(c, str) for c in categories):
            return None, "categories must be a list of strings"
        cleaned["categories"] = _encode_categories(categories)

    return cleaned, None


def create_app():
    app = Flask(__name__)

    @app.route("/")
    def root():
        return jsonify({"service": "destination-database", "status": "ok"})

    @app.route("/health")
    def health():
        try:
            conn = get_connection()
            conn.execute("SELECT 1")
            conn.close()
        except Exception as exc:  # sqlite3.Error and friends
            return jsonify(
                {"service": "destination-database", "status": "unhealthy", "database": str(exc)}
            ), 503

        return jsonify({"service": "destination-database", "status": "ok", "database": "ok"})

    @app.route("/destinations")
    def list_destinations():
        filters = []
        params = []
        for field in ("city", "country", "travel_style"):
            value = request.args.get(field)
            if value is not None:
                filters.append(f"{field} = ?")
                params.append(value)

        query = "SELECT * FROM destinations"
        if filters:
            query += " WHERE " + " AND ".join(filters)
        query += " ORDER BY destination_id"

        conn = get_connection()
        rows = conn.execute(query, params).fetchall()
        conn.close()
        return jsonify([_row_to_dict(row) for row in rows])

    @app.route("/destinations/<int:destination_id>")
    def get_destination(destination_id):
        conn = get_connection()
        row = conn.execute(
            "SELECT * FROM destinations WHERE destination_id = ?", (destination_id,)
        ).fetchone()
        conn.close()
        if row is None:
            return jsonify({"error": "destination not found"}), 404
        return jsonify(_row_to_dict(row))

    @app.route("/destinations", methods=["POST"])
    def create_destination():
        data = request.get_json(silent=True) or {}
        cleaned, error = _validate(data, partial=False)
        if error:
            return jsonify({"error": error}), 400

        columns = list(cleaned.keys())
        placeholders = ", ".join("?" for _ in columns)
        conn = get_connection()
        cursor = conn.execute(
            f"INSERT INTO destinations ({', '.join(columns)}) VALUES ({placeholders})",
            [cleaned[c] for c in columns],
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM destinations WHERE destination_id = ?", (cursor.lastrowid,)
        ).fetchone()
        conn.close()
        return jsonify(_row_to_dict(row)), 201

    @app.route("/destinations/<int:destination_id>", methods=["PUT"])
    def update_destination(destination_id):
        conn = get_connection()
        existing = conn.execute(
            "SELECT * FROM destinations WHERE destination_id = ?", (destination_id,)
        ).fetchone()
        if existing is None:
            conn.close()
            return jsonify({"error": "destination not found"}), 404

        data = request.get_json(silent=True) or {}
        cleaned, error = _validate(data, partial=True)
        if error:
            conn.close()
            return jsonify({"error": error}), 400

        set_clause = ", ".join(f"{field} = ?" for field in cleaned)
        params = list(cleaned.values()) + [destination_id]
        conn.execute(f"UPDATE destinations SET {set_clause} WHERE destination_id = ?", params)
        conn.commit()
        row = conn.execute(
            "SELECT * FROM destinations WHERE destination_id = ?", (destination_id,)
        ).fetchone()
        conn.close()
        return jsonify(_row_to_dict(row))

    @app.route("/destinations/<int:destination_id>", methods=["DELETE"])
    def delete_destination(destination_id):
        conn = get_connection()
        existing = conn.execute(
            "SELECT * FROM destinations WHERE destination_id = ?", (destination_id,)
        ).fetchone()
        if existing is None:
            conn.close()
            return jsonify({"error": "destination not found"}), 404

        conn.execute("DELETE FROM destinations WHERE destination_id = ?", (destination_id,))
        conn.commit()
        conn.close()
        return "", 204

    return app


app = create_app()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT, debug=False)
