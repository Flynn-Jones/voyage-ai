"""Public access-service API: gateway to the internal database-service, plus shared nav/config data."""
from flask import Blueprint, jsonify

from services import db_client, nav_service
from views import nav_view, trip_view, user_view

access_api = Blueprint("access_api", __name__)


@access_api.route("/health")
def health():
    return jsonify({"status": "ok", "service": "access-service"})


@access_api.route("/nav")
def nav():
    return jsonify(nav_view.format_nav(nav_service.get_features()))


@access_api.route("/users")
def list_users():
    try:
        users = db_client.get_users()
    except db_client.DatabaseServiceError:
        return jsonify({"error": "database service unavailable"}), 502
    return jsonify(user_view.format_users(users))


@access_api.route("/users/<int:user_id>")
def get_user(user_id):
    try:
        user = db_client.get_user(user_id)
    except db_client.NotFoundError:
        return jsonify({"error": "user not found"}), 404
    except db_client.DatabaseServiceError:
        return jsonify({"error": "database service unavailable"}), 502
    return jsonify(user_view.format_user(user))


@access_api.route("/users/<int:user_id>/trips")
def get_user_trips(user_id):
    try:
        trips = db_client.get_user_trips(user_id)
    except db_client.NotFoundError:
        return jsonify({"error": "user not found"}), 404
    except db_client.DatabaseServiceError:
        return jsonify({"error": "database service unavailable"}), 502
    return jsonify(trip_view.format_trips(trips))


@access_api.route("/trips")
def list_trips():
    try:
        trips = db_client.get_trips()
    except db_client.DatabaseServiceError:
        return jsonify({"error": "database service unavailable"}), 502
    return jsonify(trip_view.format_trips(trips))


@access_api.route("/trips/<int:trip_id>")
def get_trip(trip_id):
    try:
        trip = db_client.get_trip(trip_id)
    except db_client.NotFoundError:
        return jsonify({"error": "trip not found"}), 404
    except db_client.DatabaseServiceError:
        return jsonify({"error": "database service unavailable"}), 502
    return jsonify(trip_view.format_trip(trip))
