"""Release 2 post-commit checks: real HTTP only, no Flask test client or mocked services.

Run explicitly after starting Compose with docker-compose.ci.yml. Unreachable services fail;
this suite is outside the existing offline test directories. Each case owns a unique trip tag.
"""
import json
import os
import uuid
from pathlib import Path

import pytest
import requests

pytestmark = pytest.mark.live
TIMEOUT = 15
FIELDS = ("trip_reference", "day", "start_time", "end_time", "activity_id", "destination_id", "estimated_cost", "notes")


class LiveAPI:
    def __init__(self):
        self.backend = os.environ.get("ITINERARY_BE_URL", "http://localhost:5005").rstrip("/")
        self.database = os.environ.get("ITINERARY_DB_URL", "http://localhost:6005").rstrip("/")
        self.events = []

    def call(self, method, path, expected, body=None, *, database=False):
        url = (self.database if database else self.backend) + path
        event = {"method": method, "url": url, "expected_status": expected}
        if body is not None:
            event["request"] = body
        self.events.append(event)
        try:
            response = requests.request(method, url, json=body, timeout=TIMEOUT)
        except requests.RequestException as exc:
            event["transport_error"] = type(exc).__name__
            raise
        event["actual_status"] = response.status_code
        if response.content:
            event["response"] = response.json()
        assert response.status_code == expected, event
        return event.get("response")

    def rows(self, trip):
        rows = self.call("GET", "/itinerary-items", 200, database=True)
        assert isinstance(rows, list)
        return [row for row in rows if row["trip_reference"] == trip]


@pytest.fixture(scope="module")
def live_api():
    api = LiveAPI()
    try:
        assert api.call("GET", "/health", 200)["service"] == "itinerary-be"
        assert api.call("GET", "/health", 200, database=True)["service"] == "itinerary-db"
        yield api
    finally:
        directory = Path(os.environ.get("ITINERARY_CI_REPORT_DIR", "itinerary-service/reports/post-commit"))
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "http-results.json").write_text(
            json.dumps({"backend": api.backend, "database": api.database, "http_results": api.events}, indent=2) + "\n",
            encoding="utf-8",
        )


@pytest.fixture
def case(live_api):
    trip = "CI-ENDPOINT-" + uuid.uuid4().hex
    body = {"trip_reference": trip, "day": 9, "start_time": "09:00", "end_time": "10:00",
            "activity_id": 1, "destination_id": 1, "estimated_cost": 12.5, "notes": "Temporary CI endpoint item"}
    try:
        yield live_api, body
    finally:
        # Discover by exact tag even if creation succeeded but its response/ID assertion failed.
        # Try every deletion before failing teardown; never delete seeded or other tests' records.
        errors = []
        for row in live_api.rows(trip):
            item_id = row["itinerary_item_id"]
            try:
                live_api.call("DELETE", f"/api/itinerary/{item_id}", 204)
                live_api.call("GET", f"/api/itinerary/{item_id}", 404)
                live_api.call("GET", f"/itinerary-items/{item_id}", 404, database=True)
            except (requests.RequestException, AssertionError, ValueError) as exc:
                errors.append(str(exc))
        assert not live_api.rows(trip), "CI-created itinerary records remain after cleanup"
        assert not errors, errors


def create(api, body):
    row = api.call("POST", "/api/itinerary", 201, body)
    assert type(row.get("itinerary_item_id")) is int and row["itinerary_item_id"] > 0
    assert {field: row[field] for field in FIELDS} == body
    return row


def persisted(api, item_id, expected):
    # The public read includes best-effort enrichment; the DB API proves actual persistence.
    public = api.call("GET", f"/api/itinerary/{item_id}", 200)
    stored = api.call("GET", f"/itinerary-items/{item_id}", 200, database=True)
    assert public["itinerary_item_id"] == stored["itinerary_item_id"] == item_id
    assert {field: public[field] for field in FIELDS} == expected
    assert stored == {"itinerary_item_id": item_id, **expected}


def test_post_create_returns_201_and_persists(case):
    api, body = case
    row = create(api, body)
    persisted(api, row["itinerary_item_id"], body)


@pytest.mark.parametrize("field", ["trip_reference", "day", "start_time", "end_time", "activity_id", "destination_id", "estimated_cost"])
def test_post_missing_required_field_is_400_without_persistence(case, field):
    api, body = case
    before = api.rows(body["trip_reference"])
    invalid = {key: value for key, value in body.items() if key != field}
    result = api.call("POST", "/api/itinerary", 400, invalid)
    assert field in result["error"]
    assert api.rows(body["trip_reference"]) == before


@pytest.mark.parametrize("overrides,field", [
    ({"day": 0}, "day"), ({"end_time": "08:00"}, "end_time"), ({"estimated_cost": -1}, "estimated_cost"),
])
def test_post_invalid_values_are_400_without_persistence(case, overrides, field):
    api, body = case
    before = api.rows(body["trip_reference"])
    result = api.call("POST", "/api/itinerary", 400, {**body, **overrides})
    assert field in result["error"]
    assert api.rows(body["trip_reference"]) == before


def test_put_update_returns_200_and_persists(case):
    api, body = case
    row = create(api, body)
    updated = {**body, "day": 10, "start_time": "10:30", "end_time": "12:00", "estimated_cost": 27.75,
               "notes": "Updated CI endpoint item"}
    result = api.call("PUT", f"/api/itinerary/{row['itinerary_item_id']}", 200, updated)
    assert result == {"itinerary_item_id": row["itinerary_item_id"], **updated}
    persisted(api, row["itinerary_item_id"], updated)


def test_put_deleted_id_is_404_without_recreation(case):
    api, body = case
    item_id = create(api, body)["itinerary_item_id"]
    api.call("DELETE", f"/api/itinerary/{item_id}", 204)
    result = api.call("PUT", f"/api/itinerary/{item_id}", 404, body)
    assert "not found" in result["error"].lower()
    api.call("GET", f"/api/itinerary/{item_id}", 404)
    api.call("GET", f"/itinerary-items/{item_id}", 404, database=True)
    assert not api.rows(body["trip_reference"])


@pytest.mark.parametrize("overrides,field", [
    ({"start_time": "bad"}, "start_time"), ({"end_time": "08:00"}, "end_time"),
    ({"estimated_cost": -1}, "estimated_cost"), ({"day": False}, "day"),
])
def test_put_invalid_values_are_400_and_record_unchanged(case, overrides, field):
    api, body = case
    item_id = create(api, body)["itinerary_item_id"]
    result = api.call("PUT", f"/api/itinerary/{item_id}", 400, {**body, **overrides})
    assert field in result["error"]
    persisted(api, item_id, body)


@pytest.mark.parametrize("method,path", [
    ("POST", "/ai-review"), ("POST", "/mcp/itinerary"), ("POST", "/rag/answer"),
    ("POST", "/rag/retrieve"), ("POST", "/rag/refresh"), ("POST", "/multi-agent/run"),
    ("GET", "/multi-agent/runs/run-ci-disabled"), ("GET", "/multi-agent/runs/run-ci-disabled/history"),
    ("POST", "/multi-agent/runs/run-ci-disabled/review"), ("POST", "/multi-agent/runs/run-ci-disabled/apply"),
])
def test_all_ai_routes_are_disabled(live_api, method, path):
    result = live_api.call(method, "/api/itinerary" + path, 403, {} if method == "POST" else None)
    assert isinstance(result.get("error"), str) and "disabled" in result["error"].lower()
