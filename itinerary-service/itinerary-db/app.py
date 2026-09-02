import os

from flask import Flask, jsonify, request

from database import DEFAULT_DB_FILE, get_connection
from init_db import initialize_database
from validation import ValidationError, validate_itinerary_item


def create_app(db_file=None):
    database_path = db_file or DEFAULT_DB_FILE
    initialize_database(database_path)
    app = Flask(__name__)
    app.config["DB_FILE"] = database_path

    def connection():
        return get_connection(app.config["DB_FILE"])

    def fetch_item(item_id):
        conn = connection()
        try:
            return conn.execute(
                "SELECT * FROM itinerary_items WHERE itinerary_item_id = ?",
                (item_id,),
            ).fetchone()
        finally:
            conn.close()

    def invalid_request(error):
        return jsonify({"error": str(error)}), 400

    @app.get("/health")
    def health():
        return jsonify({"service": "itinerary-db", "status": "running"})

    @app.get("/itinerary-items")
    def list_items():
        conn = connection()
        try:
            rows = conn.execute(
                "SELECT * FROM itinerary_items ORDER BY day ASC, start_time ASC"
            ).fetchall()
            return jsonify([dict(row) for row in rows])
        finally:
            conn.close()

    @app.get("/itinerary-items/<int:item_id>")
    def get_item(item_id):
        row = fetch_item(item_id)
        if row is None:
            return jsonify({"error": "itinerary item not found"}), 404
        return jsonify(dict(row))

    @app.post("/itinerary-items")
    def create_item():
        try:
            item = validate_itinerary_item(request.get_json(silent=True))
        except ValidationError as error:
            return invalid_request(error)

        conn = connection()
        try:
            cursor = conn.execute(
                """
                INSERT INTO itinerary_items (
                    trip_reference, day, start_time, end_time, activity_id,
                    destination_id, estimated_cost, notes
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                tuple(item.values()),
            )
            conn.commit()
            row = conn.execute(
                "SELECT * FROM itinerary_items WHERE itinerary_item_id = ?",
                (cursor.lastrowid,),
            ).fetchone()
            return jsonify(dict(row)), 201
        finally:
            conn.close()

    @app.put("/itinerary-items/<int:item_id>")
    def update_item(item_id):
        if fetch_item(item_id) is None:
            return jsonify({"error": "itinerary item not found"}), 404

        try:
            item = validate_itinerary_item(request.get_json(silent=True))
        except ValidationError as error:
            return invalid_request(error)

        conn = connection()
        try:
            conn.execute(
                """
                UPDATE itinerary_items
                SET trip_reference = ?, day = ?, start_time = ?, end_time = ?,
                    activity_id = ?, destination_id = ?, estimated_cost = ?, notes = ?
                WHERE itinerary_item_id = ?
                """,
                (*item.values(), item_id),
            )
            conn.commit()
            row = conn.execute(
                "SELECT * FROM itinerary_items WHERE itinerary_item_id = ?",
                (item_id,),
            ).fetchone()
            return jsonify(dict(row))
        finally:
            conn.close()

    @app.delete("/itinerary-items/<int:item_id>")
    def delete_item(item_id):
        conn = connection()
        try:
            cursor = conn.execute(
                "DELETE FROM itinerary_items WHERE itinerary_item_id = ?",
                (item_id,),
            )
            if cursor.rowcount == 0:
                return jsonify({"error": "itinerary item not found"}), 404
            conn.commit()
            return "", 204
        finally:
            conn.close()

    @app.get("/itinerary-items/day/<int:day>")
    def list_items_by_day(day):
        if day < 1:
            return jsonify({"error": "day must be a positive integer"}), 400
        conn = connection()
        try:
            rows = conn.execute(
                """
                SELECT * FROM itinerary_items
                WHERE day = ?
                ORDER BY start_time ASC
                """,
                (day,),
            ).fetchall()
            return jsonify([dict(row) for row in rows])
        finally:
            conn.close()

    return app


app = create_app()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "6005"))
    app.run(host="0.0.0.0", port=port, debug=False)
