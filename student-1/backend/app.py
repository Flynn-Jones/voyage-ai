"""Standalone entry point for the destination backend API (port 5001).

Session 1 scope: / and /health only, proving the backend can reach the
database service over the network. Destination CRUD routes
(/api/destinations, ...) are Session 2.
"""
import os

import requests
from flask import Flask, jsonify

PORT = int(os.environ.get("PORT", "5001"))
DATABASE_SERVICE_URL = os.environ.get("DATABASE_SERVICE_URL", "http://localhost:6001")


class DatabaseServiceError(Exception):
    """Raised when the database service cannot be reached or returns an error."""


def _request(method, path, **kwargs):
    try:
        response = requests.request(method, f"{DATABASE_SERVICE_URL}{path}", timeout=5, **kwargs)
    except requests.exceptions.RequestException as exc:
        raise DatabaseServiceError(str(exc)) from exc

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

    return app


app = create_app()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT, debug=False)
