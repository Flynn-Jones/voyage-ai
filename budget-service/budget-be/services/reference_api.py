"""HTTP client wrapper around Student 2's accommodation database-service (port 6002)."""
import os

import requests

ACCOMMODATION_SERVICE_URL = os.environ.get("ACCOMMODATION_SERVICE_URL", "http://localhost:6002")


class ReferenceServiceError(Exception):
    """Raised when the accommodation reference service cannot be reached or returns an unexpected error."""


class NotFoundError(Exception):
    """Raised when the accommodation reference service returns a 404."""


def get_accommodation(destination):
    try:
        response = requests.get(
            f"{ACCOMMODATION_SERVICE_URL}/accommodation",
            params={"destination": destination},
            timeout=5,
        )
    except requests.exceptions.RequestException as exc:
        raise ReferenceServiceError(str(exc)) from exc

    if response.status_code == 404:
        raise NotFoundError(destination)
    if response.status_code != 200:
        raise ReferenceServiceError(f"accommodation service returned {response.status_code}")

    return response.json()
