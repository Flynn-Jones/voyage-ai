"""Provisional read-only adapter for the Destination database API."""
import os

import requests


DEFAULT_TIMEOUT = 5


class DestinationServiceError(Exception):
    """Base error for a failed Destination lookup."""


class DestinationNotFoundError(DestinationServiceError):
    """The referenced destination does not exist."""


class DestinationUnavailableError(DestinationServiceError):
    """The Destination service could not be reached."""


class InvalidDestinationResponseError(DestinationServiceError):
    """The Destination service returned an unusable response."""


def get_destination(destination_id):
    """Retrieve and normalize one destination using the provisional contract."""
    base_url = os.environ.get(
        "DESTINATION_SERVICE_URL", "http://destination-db:6001"
    ).rstrip("/")
    try:
        response = requests.get(
            f"{base_url}/destinations/{destination_id}", timeout=DEFAULT_TIMEOUT
        )
    except (requests.Timeout, requests.ConnectionError) as error:
        raise DestinationUnavailableError("destination service is unavailable") from error
    except requests.RequestException as error:
        raise DestinationServiceError("destination lookup failed") from error

    if response.status_code == 404:
        raise DestinationNotFoundError("destination not found")
    if response.status_code != 200:
        raise DestinationServiceError(
            f"destination service returned unexpected status {response.status_code}"
        )
    try:
        body = response.json()
    except ValueError as error:
        raise InvalidDestinationResponseError("destination service returned invalid JSON") from error

    required = ("destination_id", "city", "country")
    if not isinstance(body, dict) or any(field not in body for field in required):
        raise InvalidDestinationResponseError("destination response is missing required fields")
    if body["destination_id"] != destination_id:
        raise InvalidDestinationResponseError("destination response contains an unexpected identifier")
    if not isinstance(body["city"], str) or not isinstance(body["country"], str):
        raise InvalidDestinationResponseError("destination response contains invalid fields")
    return {"id": body["destination_id"], "city": body["city"], "country": body["country"]}
