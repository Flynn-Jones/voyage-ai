"""Read-only REST endpoints over the shared users/trips database."""
from flask import Blueprint, jsonify

from db import get_connection

db_api = Blueprint("db_api", __name__)


@db_api.route("/health")
def health():
    return jsonify({"status": "ok", "service": "database"})


@db_api.route("/users")
def list_users():
    conn = get_connection()
    rows = conn.execute("SELECT * FROM users").fetchall()
    conn.close()
    return jsonify([dict(row) for row in rows])


@db_api.route("/users/<int:user_id>")
def get_user(user_id):
    conn = get_connection()
    row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    conn.close()
    if row is None:
        return jsonify({"error": "user not found"}), 404
    return jsonify(dict(row))


@db_api.route("/trips")
def list_trips():
    conn = get_connection()
    rows = conn.execute("SELECT * FROM trips").fetchall()
    conn.close()
    return jsonify([dict(row) for row in rows])


@db_api.route("/trips/<int:trip_id>")
def get_trip(trip_id):
    conn = get_connection()
    row = conn.execute("SELECT * FROM trips WHERE id = ?", (trip_id,)).fetchone()
    conn.close()
    if row is None:
        return jsonify({"error": "trip not found"}), 404
    return jsonify(dict(row))


@db_api.route("/users/<int:user_id>/trips")
def get_user_trips(user_id):
    conn = get_connection()
    user = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    if user is None:
        conn.close()
        return jsonify({"error": "user not found"}), 404
    rows = conn.execute("SELECT * FROM trips WHERE user_id = ?", (user_id,)).fetchall()
    conn.close()
    return jsonify([dict(row) for row in rows])
