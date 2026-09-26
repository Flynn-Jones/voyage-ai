"""HTTP client wrapper for communicating with the Destination database service."""
import os
import requests

DESTINATION_SERVICE_URL = os.environ.get("DESTINATION_SERVICE_URL", "http://destination-db:6001")

class DestinationServiceError(Exception):
    """Raised when the destination service cannot be reached or returns an unexpected error."""
    def __init__(self, message="", status_code=None, payload=None):
        super().__init__(message)
        self.status_code = status_code
        self.payload = payload or {}

class DestinationNotFoundError(DestinationServiceError):
    """Raised when the destination service returns a 404."""

class DestinationUnavailableError(DestinationServiceError):
    """Raised when the destination service is unreachable."""

def list_destinations():
    """Retrieve list of destinations from the destination service."""
    try:
        response = requests.get(f"{DESTINATION_SERVICE_URL.rstrip('/')}/destinations", timeout=5)
    except (requests.Timeout, requests.ConnectionError) as exc:
        raise DestinationUnavailableError("destination service is unavailable") from exc
    except requests.RequestException as exc:
        raise DestinationServiceError(str(exc)) from exc

    if response.status_code == 404:
        raise DestinationNotFoundError("destinations not found")
    if response.status_code >= 400:
        raise DestinationServiceError(f"destination service returned {response.status_code}", status_code=response.status_code)

    try:
        data = response.json()
    except ValueError as exc:
        raise DestinationServiceError("destination service returned invalid JSON") from exc

    # Return list of destinations whether paginated dict or direct list
    if isinstance(data, dict) and "data" in data:
        return data["data"]
    if isinstance(data, list):
        return data
    return []
