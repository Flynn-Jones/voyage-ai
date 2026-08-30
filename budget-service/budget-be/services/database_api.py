"""HTTP client wrapper around the budget-db service (port 6004)."""
import os

import requests

DATABASE_SERVICE_URL = os.environ.get("DATABASE_SERVICE_URL", "http://localhost:6004")


class DatabaseServiceError(Exception):
    """Raised when the database service cannot be reached or returns an unexpected error."""


class NotFoundError(Exception):
    """Raised when the database service returns a 404 for the requested resource."""


class ValidationError(Exception):
    """Raised when the database service rejects a request as invalid (400)."""


def _request(method, path, **kwargs):
    try:
        response = requests.request(method, f"{DATABASE_SERVICE_URL}{path}", timeout=5, **kwargs)
    except requests.exceptions.RequestException as exc:
        raise DatabaseServiceError(str(exc)) from exc

    if response.status_code == 404:
        raise NotFoundError(path)
    if response.status_code == 400:
        raise ValidationError(response.json().get("error", "invalid request"))
    if response.status_code >= 400:
        raise DatabaseServiceError(f"database returned {response.status_code} for {path}")

    return response


def get_expenses(filters=None):
    response = _request("GET", "/expenses", params=filters or {})
    return response.json()


def get_expense(expense_id):
    response = _request("GET", f"/expenses/{expense_id}")
    return response.json()


def create_expense(payload):
    response = _request("POST", "/expenses", json=payload)
    return response.json()


def update_expense(expense_id, payload):
    response = _request("PUT", f"/expenses/{expense_id}", json=payload)
    return response.json()


def delete_expense(expense_id):
    _request("DELETE", f"/expenses/{expense_id}")
