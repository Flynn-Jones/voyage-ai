"""HTTP client wrapper around the internal database (port 6000)."""
import os

import requests

DATABASE_SERVICE_URL = os.environ.get("DATABASE_SERVICE_URL", "http://localhost:6000")


class DatabaseServiceError(Exception):
    """Raised when database cannot be reached or returns an unexpected error."""


class NotFoundError(Exception):
    """Raised when database returns a 404 for the requested resource."""


def _get(path):
    try:
        response = requests.get(f"{DATABASE_SERVICE_URL}{path}", timeout=5)
    except requests.exceptions.RequestException as exc:
        raise DatabaseServiceError(str(exc)) from exc

    if response.status_code == 404:
        raise NotFoundError(path)
    if response.status_code != 200:
        raise DatabaseServiceError(f"database returned {response.status_code} for {path}")

    return response.json()


def get_users():
    return _get("/users")


def get_user(user_id):
    return _get(f"/users/{user_id}")


def get_user_trips(user_id):
    return _get(f"/users/{user_id}/trips")


def get_trips():
    return _get("/trips")


def get_trip(trip_id):
    return _get(f"/trips/{trip_id}")
