"""Provisional read-only adapter for the Activity database API."""
import os

import requests


DEFAULT_TIMEOUT = 5


class ActivityServiceError(Exception):
    """Base error for a failed Activity lookup."""


class ActivityNotFoundError(ActivityServiceError):
    """The referenced activity does not exist."""


class ActivityUnavailableError(ActivityServiceError):
    """The Activity service could not be reached."""


class InvalidActivityResponseError(ActivityServiceError):
    """The Activity service returned an unusable response."""


def get_activity(activity_id):
    """Retrieve and normalize one activity using the provisional contract."""
    base_url = os.environ.get(
        "ACTIVITY_SERVICE_URL", "http://activity-service-database:6003"
    ).rstrip("/")
    try:
        response = requests.get(
            f"{base_url}/activities/{activity_id}", timeout=DEFAULT_TIMEOUT
        )
    except (requests.Timeout, requests.ConnectionError) as error:
        raise ActivityUnavailableError("activity service is unavailable") from error
    except requests.RequestException as error:
        raise ActivityServiceError("activity lookup failed") from error

    if response.status_code == 404:
        raise ActivityNotFoundError("activity not found")
    if response.status_code != 200:
        raise ActivityServiceError(
            f"activity service returned unexpected status {response.status_code}"
        )
    try:
        body = response.json()
    except ValueError as error:
        raise InvalidActivityResponseError("activity service returned invalid JSON") from error

    required = ("activity_id", "activity_name", "activity_type", "activity_cost", "duration")
    if not isinstance(body, dict) or any(field not in body for field in required):
        raise InvalidActivityResponseError("activity response is missing required fields")
    if body["activity_id"] != activity_id:
        raise InvalidActivityResponseError("activity response contains an unexpected identifier")
    if (
        not isinstance(body["activity_name"], str)
        or not isinstance(body["activity_type"], str)
        or isinstance(body["activity_cost"], bool)
        or not isinstance(body["activity_cost"], (int, float))
        or not isinstance(body["duration"], str)
    ):
        raise InvalidActivityResponseError("activity response contains invalid fields")
    return {
        "id": body["activity_id"],
        "name": body["activity_name"],
        "type": body["activity_type"],
        "cost": body["activity_cost"],
        "duration": body["duration"],
    }
