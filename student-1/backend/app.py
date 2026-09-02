"""Standalone entry point for the destination backend API (port 5001).

Session 1 scope proved the backend can reach the database service over the
network. Session 2 adds the /api/destinations CRUD routes, forwarded over
HTTP to the database service. This service must never open SQLite directly.
"""
import os

import requests
from flask import Flask, jsonify, request

PORT = int(os.environ.get("PORT", "5001"))
DATABASE_SERVICE_URL = os.environ.get("DATABASE_SERVICE_URL", "http://localhost:6001")

FORWARDED_LIST_PARAMS = ("city", "country", "travel_style")


class DatabaseServiceError(Exception):
    """Raised when the database service cannot be reached or returns an error."""


class NotFoundError(DatabaseServiceError):
    """Raised when the database service returns a 404 for the requested resource."""


class ValidationError(DatabaseServiceError):
    """Raised when the database service rejects a request as invalid (400)."""


def _read_error_message(response):
    try:
        payload = response.json()
    except ValueError:
        payload = None
    if isinstance(payload, dict) and "error" in payload:
        return payload["error"]
    return response.text or "invalid request"


def _request(method, path, **kwargs):
    try:
        response = requests.request(method, f"{DATABASE_SERVICE_URL}{path}", timeout=5, **kwargs)
    except requests.exceptions.RequestException as exc:
        raise DatabaseServiceError(str(exc)) from exc

    if response.status_code == 404:
        raise NotFoundError(_read_error_message(response))
    if response.status_code == 400:
        raise ValidationError(_read_error_message(response))
    if response.status_code >= 400:
        raise DatabaseServiceError(f"database returned {response.status_code} for {path}")

    return response


def create_app():
    app = Flask(__name__)

    @app.route("/")
    def root():
        return jsonify({"service": "destination-backend", "status": "ok"})

    @app.route("/health")
    def health():
        try:
            db_response = _request("GET", "/health")
        except DatabaseServiceError as exc:
            return jsonify(
                {
                    "service": "destination-backend",
                    "status": "unhealthy",
                    "database": {"status": "unreachable", "error": str(exc)},
                }
            ), 502

        return jsonify(
            {
                "service": "destination-backend",
                "status": "ok",
                "database": db_response.json(),
            }
        )

    @app.route("/api/destinations")
    def list_destinations():
        params = {
            key: value
            for key in FORWARDED_LIST_PARAMS
            if (value := request.args.get(key)) is not None
        }
        try:
            response = _request("GET", "/destinations", params=params)
        except DatabaseServiceError as exc:
            return jsonify({"error": "destination database unavailable"}), 503
        return jsonify(response.json())

    @app.route("/api/destinations/<int:destination_id>")
    def get_destination(destination_id):
        try:
            response = _request("GET", f"/destinations/{destination_id}")
        except NotFoundError as exc:
            return jsonify({"error": str(exc)}), 404
        except DatabaseServiceError:
            return jsonify({"error": "destination database unavailable"}), 503
        return jsonify(response.json())

    @app.route("/api/destinations", methods=["POST"])
    def create_destination():
        payload = request.get_json(silent=True) or {}
        try:
            response = _request("POST", "/destinations", json=payload)
        except ValidationError as exc:
            return jsonify({"error": str(exc)}), 400
        except DatabaseServiceError:
            return jsonify({"error": "destination database unavailable"}), 503
        return jsonify(response.json()), 201

    @app.route("/api/destinations/<int:destination_id>", methods=["PUT"])
    def update_destination(destination_id):
        payload = request.get_json(silent=True) or {}
        try:
            response = _request("PUT", f"/destinations/{destination_id}", json=payload)
        except NotFoundError as exc:
            return jsonify({"error": str(exc)}), 404
        except ValidationError as exc:
            return jsonify({"error": str(exc)}), 400
        except DatabaseServiceError:
            return jsonify({"error": "destination database unavailable"}), 503
        return jsonify(response.json())

    @app.route("/api/destinations/<int:destination_id>", methods=["DELETE"])
    def delete_destination(destination_id):
        try:
            _request("DELETE", f"/destinations/{destination_id}")
        except NotFoundError as exc:
            return jsonify({"error": str(exc)}), 404
        except DatabaseServiceError:
            return jsonify({"error": "destination database unavailable"}), 503
        return "", 204

    return app


app = create_app()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT, debug=False)
