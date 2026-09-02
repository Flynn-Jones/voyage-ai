"""HTTP client for the itinerary database API."""
import os

import requests


DEFAULT_TIMEOUT = 5


class DatabaseServiceError(Exception):
    """The database service is unavailable or returned an invalid response."""


class NotFoundError(DatabaseServiceError):
    """The requested itinerary item does not exist."""


class UpstreamValidationError(DatabaseServiceError):
    """The database API rejected a representation as invalid."""


def get_database_service_url():
    return os.environ.get("DATABASE_SERVICE_URL", "http://localhost:6005").rstrip("/")


def _error_message(response, fallback):
    try:
        body = response.json()
    except ValueError:
        return fallback
    if isinstance(body, dict) and isinstance(body.get("error"), str):
        return body["error"]
    return fallback


def _request(method, path, expected_status, expected_json=None, **kwargs):
    try:
        response = requests.request(
            method,
            f"{get_database_service_url()}{path}",
            timeout=DEFAULT_TIMEOUT,
            **kwargs,
        )
    except requests.RequestException as error:
        raise DatabaseServiceError("itinerary database service is unavailable") from error

    if response.status_code == 404:
        raise NotFoundError("itinerary item not found")
    if response.status_code == 400:
        raise UpstreamValidationError(
            _error_message(response, "database service rejected the itinerary item")
        )
    if response.status_code != expected_status:
        raise DatabaseServiceError(
            f"itinerary database service returned unexpected status {response.status_code}"
        )

    if expected_json is None:
        return None
    try:
        body = response.json()
    except ValueError as error:
        raise DatabaseServiceError("itinerary database service returned invalid JSON") from error
    if not isinstance(body, expected_json):
        raise DatabaseServiceError("itinerary database service returned an unexpected JSON shape")
    return body


def get_items():
    return _request("GET", "/itinerary-items", 200, list)


def get_item(item_id):
    return _request("GET", f"/itinerary-items/{item_id}", 200, dict)


def create_item(payload):
    return _request("POST", "/itinerary-items", 201, dict, json=payload)


def update_item(item_id, payload):
    return _request("PUT", f"/itinerary-items/{item_id}", 200, dict, json=payload)


def delete_item(item_id):
    _request("DELETE", f"/itinerary-items/{item_id}", 204)


def get_items_by_day(day):
    return _request("GET", f"/itinerary-items/day/{day}", 200, list)
