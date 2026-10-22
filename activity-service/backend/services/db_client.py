"""HTTP client wrapper around activity-service/database (port 6003), reads only."""
import os

import requests

DATABASE_SERVICE_URL = os.environ.get("DATABASE_SERVICE_URL", "http://localhost:6003")


class DatabaseServiceError(Exception):
    """Raised when the database-service is unreachable or returns an unexpected error."""


class NotFoundError(DatabaseServiceError):
    """Raised when the requested record doesn't exist."""


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


def get_activities():
    return _get("/activities")


def get_activity(activity_id):
    return _get(f"/activities/{activity_id}")


def get_activity_assignments(activity_id):
    return _get(f"/activities/{activity_id}/assignments")


def get_assignments():
    return _get("/assignments")


def _write(method, path, data, ok_status):
    try:
        response = requests.request(method, f"{DATABASE_SERVICE_URL}{path}", json=data, timeout=5)
    except requests.exceptions.RequestException as exc:
        raise DatabaseServiceError(str(exc)) from exc

    if response.status_code == 404:
        raise NotFoundError(path)
    if response.status_code != ok_status:
        raise DatabaseServiceError(f"database returned {response.status_code} for {method} {path}")

    return response.json() if response.text else None


def create_activity(data):
    return _write("POST", "/activities", data, ok_status=201)


def update_activity(activity_id, data):
    return _write("PATCH", f"/activities/{activity_id}", data, ok_status=200)


def delete_activity(activity_id):
    _write("DELETE", f"/activities/{activity_id}", None, ok_status=200)
