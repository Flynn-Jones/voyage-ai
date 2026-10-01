"""Tool implementations for the shared VoyageAI MCP server.

Runs locally (not containerised) and reaches feature data over the same
microservices-net HTTP ports each backend already uses, so it stays a genuine
single shared instance rather than logic duplicated per feature.
"""
import json
import os
from pathlib import Path

import requests

BASE_DIR = Path(__file__).resolve().parent
REPO_ROOT = BASE_DIR.parent.parent  # ai-services/mcp-server -> ai-services -> repo root

BUDGET_DB_URL = os.environ.get("BUDGET_DB_URL", "http://localhost:6004")
ACCOMMODATION_DB_URL = os.environ.get("ACCOMMODATION_DB_URL", "http://localhost:6002")
DESTINATION_DB_URL = os.environ.get("DESTINATION_DB_URL", "http://localhost:6001")

DESTINATION_FILTERS = ("city", "country", "travel_style")
DESTINATION_FILTER_MAX_LENGTH = 100

IGNORED_DIRS = {".git", ".venv", "venv", "__pycache__", "node_modules", "chroma"}


def list_expenses(trip_reference: str = None):
    """Return budget expenses, optionally filtered to one trip."""
    params = {"trip_reference": trip_reference} if trip_reference else {}
    try:
        response = requests.get(f"{BUDGET_DB_URL}/expenses", params=params, timeout=5)
        response.raise_for_status()
        expenses = response.json()
    except requests.exceptions.RequestException as exc:
        return {"error": f"budget-db unavailable: {exc}"}

    return {"trip_reference": trip_reference, "count": len(expenses), "expenses": expenses}


def get_accommodation_by_destination(destination: str):
    """Return accommodation reference records matching a destination name."""
    destination = (destination or "").strip()
    if not destination:
        return {"error": "destination is required"}

    # Filter on destination_city first: a tool named "by destination" must mean
    # the city, not a free-text scan. The previous q= lookup searched
    # name/description/location, so "Tokyo" returned only the one record whose
    # description happened to mention Tokyo -- 1 of 7 real Tokyo stays, missing
    # every cheap option. Fall back to q= so a district or property name
    # ("Shinjuku", "Granbell") still resolves.
    def _query(params):
        response = requests.get(f"{ACCOMMODATION_DB_URL}/accommodations", params=params, timeout=5)
        if response.status_code == 404:
            return []
        response.raise_for_status()
        return response.json().get("data", [])

    try:
        data = _query({"destination_city": destination, "limit": 100})
        matched_by = "destination_city"
        if not data:
            data = _query({"q": destination, "limit": 100})
            matched_by = "keyword"
    except requests.exceptions.RequestException as exc:
        return {"error": f"accommodation-db unavailable: {exc}"}

    return {
        "destination": destination,
        "count": len(data),
        "matched_by": matched_by,
        "accommodations": data,
    }


def search_accommodations(
    destination: str = None,
    accommodation_type: str = None,
    min_price: float = None,
    max_price: float = None,
    min_rating: float = None,
    amenities: str = None,
    sort_by: str = None,
    limit: int = 20,
):
    """Search accommodation reference records across any combination of filters.

    Every filter is optional, so this answers both "what is there in Tokyo?" and
    "hostels in Tokyo under $100 rated 4+". `destination` matches the city first
    and falls back to a keyword search, the same as get_accommodation_by_destination.
    """
    filters = {
        "type": (accommodation_type or "").strip().lower() or None,
        "min_price": min_price,
        "max_price": max_price,
        "min_rating": min_rating,
        "amenities": (amenities or "").strip() or None,
        "sort_by": (sort_by or "").strip() or None,
        "limit": max(1, min(int(limit or 20), 100)),
    }
    params = {key: value for key, value in filters.items() if value is not None}

    destination = (destination or "").strip()

    def _query(extra):
        response = requests.get(
            f"{ACCOMMODATION_DB_URL}/accommodations", params={**params, **extra}, timeout=5
        )
        if response.status_code == 404:
            return []
        response.raise_for_status()
        return response.json().get("data", [])

    try:
        if destination:
            data = _query({"destination_city": destination})
            matched_by = "destination_city"
            if not data:
                data = _query({"q": destination})
                matched_by = "keyword"
        else:
            data = _query({})
            matched_by = "filters_only"
    except requests.exceptions.RequestException as exc:
        return {"error": f"accommodation-db unavailable: {exc}"}

    applied = {key: value for key, value in filters.items() if value is not None and key != "limit"}
    if destination:
        applied["destination"] = destination

    return {
        "destination": destination or None,
        "filters_applied": applied,
        "matched_by": matched_by,
        "count": len(data),
        "accommodations": data,
    }


ACCOMMODATION_TYPES = {"hotel", "hostel", "ryokan", "apartment", "guesthouse"}


def create_accommodation(
    name: str = None,
    destination_id: str = None,
    price_per_night: float = None,
    destination_city: str = None,
    accommodation_type: str = None,
    rating: float = None,
    location: str = None,
    description: str = None,
    amenities: list = None,
):
    """Create an accommodation record.

    The only write tool on this server: it POSTs to accommodation-db rather than
    reading. Required fields are name, destination_id and price_per_night; the
    call is rejected here before it reaches the database if any are missing.
    """
    name = (name or "").strip()
    destination_id = (destination_id or "").strip()

    missing = [
        field
        for field, value in (
            ("name", name),
            ("destination_id", destination_id),
            ("price_per_night", price_per_night),
        )
        if value in (None, "")
    ]
    if missing:
        return {"error": "missing required field(s): " + ", ".join(missing)}

    try:
        price = float(price_per_night)
    except (TypeError, ValueError):
        return {"error": f"price_per_night must be a number, got {price_per_night!r}"}
    if price < 0:
        return {"error": "price_per_night must be >= 0"}

    payload = {"name": name, "destination_id": destination_id, "price_per_night": price}

    if destination_city and str(destination_city).strip():
        payload["destination_city"] = str(destination_city).strip()

    if accommodation_type:
        candidate = str(accommodation_type).strip().lower()
        if candidate not in ACCOMMODATION_TYPES:
            return {"error": f"type must be one of {sorted(ACCOMMODATION_TYPES)}, got {candidate!r}"}
        payload["type"] = candidate

    if rating is not None:
        try:
            rating_value = float(rating)
        except (TypeError, ValueError):
            return {"error": f"rating must be a number, got {rating!r}"}
        if not 0 <= rating_value <= 5:
            return {"error": "rating must be between 0 and 5"}
        payload["rating"] = rating_value

    if location and str(location).strip():
        payload["location"] = str(location).strip()
    if description and str(description).strip():
        payload["description"] = str(description).strip()

    if amenities:
        if isinstance(amenities, str):
            amenities = [item.strip() for item in amenities.split(",")]
        cleaned = [str(item).strip() for item in amenities if str(item).strip()]
        if cleaned:
            payload["amenities"] = cleaned

    try:
        response = requests.post(f"{ACCOMMODATION_DB_URL}/accommodations", json=payload, timeout=5)
        if response.status_code >= 400:
            try:
                detail = response.json()
            except ValueError:
                detail = response.text
            return {"error": f"accommodation-db rejected the record ({response.status_code})", "detail": detail}
        created = response.json()
    except requests.exceptions.RequestException as exc:
        return {"error": f"accommodation-db unavailable: {exc}"}

    # accommodation-db silently drops amenity names it does not know about, so
    # report which ones actually landed on the record.
    requested = set(payload.get("amenities", []))
    stored = set(created.get("amenities") or [])
    result = {"created": created, "id": created.get("id")}
    if requested - stored:
        result["ignored_amenities"] = sorted(requested - stored)
    return result


class DestinationToolError(Exception):
    """Explicit list_destinations failure (bad input or destination-db problem)."""


def _clean_destination_filter(field, value):
    if value is None:
        return None
    if not isinstance(value, str):
        raise DestinationToolError(f"invalid {field}: must be a string")
    value = value.strip()
    if not value:
        return None
    if len(value) > DESTINATION_FILTER_MAX_LENGTH:
        raise DestinationToolError(
            f"invalid {field}: must be at most {DESTINATION_FILTER_MAX_LENGTH} characters"
        )
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
        raise DestinationToolError(f"invalid {field}: control characters are not allowed")
    return value


def list_destinations(city: str = None, country: str = None, travel_style: str = None):
    """Return destinations from the Destination Database API, optionally filtered.

    Filters are exact-match, as implemented by GET /destinations. Raises
    DestinationToolError on invalid input or any destination-db failure.
    """
    raw = {"city": city, "country": country, "travel_style": travel_style}
    filters = {}
    for field in DESTINATION_FILTERS:
        cleaned = _clean_destination_filter(field, raw[field])
        if cleaned is not None:
            filters[field] = cleaned

    try:
        response = requests.get(f"{DESTINATION_DB_URL}/destinations", params=filters, timeout=5)
    except requests.exceptions.RequestException as exc:
        raise DestinationToolError(f"destination-db unavailable: {exc}") from exc

    if not 200 <= response.status_code < 300:
        raise DestinationToolError(f"destination-db returned {response.status_code}")

    try:
        destinations = response.json()
    except ValueError as exc:
        raise DestinationToolError("destination-db returned unexpected payload") from exc
    if not isinstance(destinations, list):
        raise DestinationToolError("destination-db returned unexpected payload")

    return {
        "source": "destination-database",
        "filters": filters,
        "count": len(destinations),
        "destinations": destinations,
    }


def project_files(directory_path: str = "."):
    """List files/folders in a repository directory (relative to repo root)."""
    path = (REPO_ROOT / directory_path).resolve()

    try:
        path.relative_to(REPO_ROOT)
    except ValueError:
        return {"error": "directory_path must stay within the repository"}

    if not path.exists() or not path.is_dir():
        return {"error": f"Directory not found: {directory_path}"}

    items = sorted(item.name for item in path.iterdir() if item.name not in IGNORED_DIRS)
    return {"directory": directory_path, "items": items}


def ci_report(feature: str = "budget-service"):
    """Read the most recent local CI evidence report for a feature, if present."""
    report_path = REPO_ROOT / feature / "reports" / "report.json"
    if not report_path.exists():
        return {
            "error": "Report not found",
            "path": str(report_path.relative_to(REPO_ROOT)),
            "hint": f"Run the {feature} GitHub Actions workflow (workflow_dispatch) to generate report.json",
        }

    with report_path.open("r", encoding="utf-8") as file:
        return json.load(file)


if __name__ == "__main__":
    print(json.dumps(list_expenses(), indent=2))
    print(json.dumps(get_accommodation_by_destination("Tokyo"), indent=2))
    print(json.dumps(project_files("."), indent=2))
    print(json.dumps(ci_report("budget-service"), indent=2))
