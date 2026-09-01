"""HTTP client wrapper around the accommodation database service."""
import os

import requests

DATABASE_SERVICE_URL = os.environ.get("DATABASE_SERVICE_URL", "http://localhost:6002")


class DatabaseServiceError(Exception):
    """Raised when the database service cannot be reached or returns an unexpected error."""

    def __init__(self, message="", status_code=None, payload=None):
        super().__init__(message)
        self.status_code = status_code
        self.payload = payload or {}


class NotFoundError(DatabaseServiceError):
    """Raised when the database service returns a 404 for the requested resource."""


class ValidationError(DatabaseServiceError):
    """Raised when the database service rejects a request as invalid."""


def _read_error_payload(response):
    try:
        payload = response.json()
    except ValueError:
        payload = {"error": response.text}
    return payload if isinstance(payload, dict) else {"error": payload}


def _request(method, path, **kwargs):
    try:
        response = requests.request(method, f"{DATABASE_SERVICE_URL}{path}", timeout=5, **kwargs)
    except requests.exceptions.RequestException as exc:
        raise DatabaseServiceError(str(exc)) from exc

    if response.status_code == 404:
        raise NotFoundError(path, status_code=404, payload=_read_error_payload(response))
    if response.status_code in {400, 422}:
        payload = _read_error_payload(response)
        raise ValidationError(
            payload.get("error", payload.get("message", "invalid request")),
            status_code=response.status_code,
            payload=payload,
        )
    if response.status_code >= 400:
        raise DatabaseServiceError(
            f"database returned {response.status_code} for {path}",
            status_code=response.status_code,
            payload=_read_error_payload(response),
        )

    return response


def list_accommodations(params=None):
    response = _request("GET", "/accommodations", params=params or {})
    return response.json()


def get_accommodation(accommodation_id):
    response = _request("GET", f"/accommodations/{accommodation_id}")
    return response.json()


def create_accommodation(payload):
    response = _request("POST", "/accommodations", json=payload)
    return response.json()


def update_accommodation(accommodation_id, payload):
    response = _request("PATCH", f"/accommodations/{accommodation_id}", json=payload)
    return response.json()


def replace_accommodation(accommodation_id, payload):
    response = _request("PUT", f"/accommodations/{accommodation_id}", json=payload)
    return response.json()


def delete_accommodation(accommodation_id):
    _request("DELETE", f"/accommodations/{accommodation_id}")


def get_health():
    try:
        response = requests.get(f"{DATABASE_SERVICE_URL}/health", timeout=5)
    except requests.exceptions.RequestException as exc:
        raise DatabaseServiceError(str(exc)) from exc

    return response
