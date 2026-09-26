"""Tool implementations for the activity-service MCP server.

Mirrors ai-services/mcp-server/tools.py: runs locally (not containerised) and
reaches activity data over the same database-service HTTP endpoints the
backend's services/db_client.py already uses, rather than a parallel data
path. Every tool is read-only and never raises — failures come back as
{"error": ...} so MCP clients always get a structured result.
"""
import json
import os

import requests

ACTIVITY_DB_URL = os.environ.get("ACTIVITY_DB_URL", "http://localhost:6003")

try:
    ACTIVITY_DB_TIMEOUT_SECONDS = int(os.environ.get("ACTIVITY_DB_TIMEOUT_SECONDS", "5"))
except ValueError:
    ACTIVITY_DB_TIMEOUT_SECONDS = 5


class _NotFound(Exception):
    pass


def _get(path):
    """GET a database-service path. Raises _NotFound on 404 and
    requests.exceptions.RequestException on any other failure."""
    response = requests.get(f"{ACTIVITY_DB_URL}{path}", timeout=ACTIVITY_DB_TIMEOUT_SECONDS)
    if response.status_code == 404:
        raise _NotFound(path)
    response.raise_for_status()
    return response.json()


def _positive_int(value, name):
    """Returns (int_value, error) — error is a message, or None."""
    if isinstance(value, bool):
        return None, f"{name} must be a positive integer"
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None, f"{name} must be a positive integer"
    if number < 1 or str(number) != str(value).strip():
        return None, f"{name} must be a positive integer"
    return number, None


def _unavailable(exc):
    return {"error": f"activity database unavailable: {exc}"}


def list_activities():
    """Return every activity (id, name, type, cost, duration)."""
    try:
        activities = _get("/activities")
    except (_NotFound, requests.exceptions.RequestException) as exc:
        return _unavailable(exc)

    return {"count": len(activities), "activities": activities}


def get_activity(activity_id):
    """Return one activity by its numeric activity_id."""
    activity_id, error = _positive_int(activity_id, "activity_id")
    if error:
        return {"error": error}

    try:
        activity = _get(f"/activities/{activity_id}")
    except _NotFound:
        return {"error": "activity not found", "activity_id": activity_id}
    except requests.exceptions.RequestException as exc:
        return _unavailable(exc)

    return {"activity": activity}


def get_activity_assignments(activity_id):
    """Return the scheduled time assignments for one activity."""
    activity_id, error = _positive_int(activity_id, "activity_id")
    if error:
        return {"error": error}

    try:
        assignments = _get(f"/activities/{activity_id}/assignments")
    except _NotFound:
        return {"error": "activity not found", "activity_id": activity_id}
    except requests.exceptions.RequestException as exc:
        return _unavailable(exc)

    return {"activity_id": activity_id, "count": len(assignments), "assignments": assignments}


def list_assignments():
    """Return every activity time assignment across all activities."""
    try:
        assignments = _get("/assignments")
    except (_NotFound, requests.exceptions.RequestException) as exc:
        return _unavailable(exc)

    return {"count": len(assignments), "assignments": assignments}


def get_assignment(assignment_id):
    """Return one time assignment by its numeric assignment_id."""
    assignment_id, error = _positive_int(assignment_id, "assignment_id")
    if error:
        return {"error": error}

    try:
        assignment = _get(f"/assignments/{assignment_id}")
    except _NotFound:
        return {"error": "assignment not found", "assignment_id": assignment_id}
    except requests.exceptions.RequestException as exc:
        return _unavailable(exc)

    return {"assignment": assignment}


if __name__ == "__main__":
    print(json.dumps(list_activities(), indent=2))
    print(json.dumps(get_activity(1), indent=2))
    print(json.dumps(get_activity_assignments(1), indent=2))
    print(json.dumps(list_assignments(), indent=2))
    print(json.dumps(get_assignment(1), indent=2))
