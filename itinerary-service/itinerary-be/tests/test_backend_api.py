import copy
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest
import requests


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app import create_app
from services import database_api, enrichment


VALID_ITEM = {
    "trip_reference": "TRIP-TEST",
    "day": 2,
    "start_time": "09:00",
    "end_time": "10:30",
    "activity_id": 101,
    "destination_id": 11,
    "estimated_cost": 25.0,
    "notes": "Backend test item",
}
ITEM_WITH_ID = {"itinerary_item_id": 50, **VALID_ITEM}


@pytest.fixture()
def client():
    application = create_app()
    application.config.update(TESTING=True)
    return application.test_client()


@pytest.fixture(autouse=True)
def isolate_existing_route_tests_from_enrichment(monkeypatch):
    """These CRUD adapter tests predate and are independent of enrichment."""
    monkeypatch.setattr(enrichment, "enrich_items", lambda items: items)
    monkeypatch.setattr(enrichment, "enrich_item", lambda item: item)


def response(status, body=None, json_error=None):
    mocked = Mock(status_code=status)
    if json_error:
        mocked.json.side_effect = json_error
    else:
        mocked.json.return_value = body
    return mocked


def test_get_all_success(client, monkeypatch):
    records = [ITEM_WITH_ID]
    monkeypatch.setattr(database_api.requests, "request", Mock(return_value=response(200, records)))
    result = client.get("/api/itinerary")
    assert result.status_code == 200
    assert result.get_json() == records


def test_get_single_success(client, monkeypatch):
    monkeypatch.setattr(database_api.requests, "request", Mock(return_value=response(200, ITEM_WITH_ID)))
    result = client.get("/api/itinerary/50")
    assert result.status_code == 200
    assert result.get_json() == ITEM_WITH_ID


def test_get_missing_maps_to_404(client, monkeypatch):
    monkeypatch.setattr(database_api.requests, "request", Mock(return_value=response(404, {"error": "missing"})))
    assert client.get("/api/itinerary/999").status_code == 404


def test_post_valid_returns_201(client, monkeypatch):
    request_mock = Mock(return_value=response(201, ITEM_WITH_ID))
    monkeypatch.setattr(database_api.requests, "request", request_mock)
    result = client.post("/api/itinerary", json=VALID_ITEM)
    assert result.status_code == 201
    assert result.get_json() == ITEM_WITH_ID
    assert request_mock.call_args.kwargs["json"] == VALID_ITEM


def test_post_invalid_does_not_call_database(client, monkeypatch):
    request_mock = Mock()
    monkeypatch.setattr(database_api.requests, "request", request_mock)
    invalid = copy.deepcopy(VALID_ITEM)
    invalid["end_time"] = "08:00"
    assert client.post("/api/itinerary", json=invalid).status_code == 400
    request_mock.assert_not_called()


def test_put_valid(client, monkeypatch):
    updated = {**ITEM_WITH_ID, "notes": "Updated"}
    monkeypatch.setattr(database_api.requests, "request", Mock(return_value=response(200, updated)))
    payload = {**VALID_ITEM, "notes": "Updated"}
    result = client.put("/api/itinerary/50", json=payload)
    assert result.status_code == 200
    assert result.get_json()["notes"] == "Updated"


def test_put_invalid_returns_400(client, monkeypatch):
    request_mock = Mock()
    monkeypatch.setattr(database_api.requests, "request", request_mock)
    invalid = copy.deepcopy(VALID_ITEM)
    del invalid["day"]
    assert client.put("/api/itinerary/50", json=invalid).status_code == 400
    request_mock.assert_not_called()


def test_delete_success(client, monkeypatch):
    monkeypatch.setattr(database_api.requests, "request", Mock(return_value=response(204)))
    assert client.delete("/api/itinerary/50").status_code == 204


def test_delete_missing_returns_404(client, monkeypatch):
    monkeypatch.setattr(database_api.requests, "request", Mock(return_value=response(404, {})))
    assert client.delete("/api/itinerary/999").status_code == 404


def test_get_by_day_success(client, monkeypatch):
    records = [ITEM_WITH_ID]
    monkeypatch.setattr(database_api.requests, "request", Mock(return_value=response(200, records)))
    result = client.get("/api/itinerary/day/2")
    assert result.status_code == 200
    assert result.get_json() == records


def test_invalid_day_returns_400_without_database_call(client, monkeypatch):
    request_mock = Mock()
    monkeypatch.setattr(database_api.requests, "request", request_mock)
    assert client.get("/api/itinerary/day/0").status_code == 400
    request_mock.assert_not_called()


def test_database_unavailable_maps_to_502(client, monkeypatch):
    request_mock = Mock(side_effect=requests.ConnectionError("offline"))
    monkeypatch.setattr(database_api.requests, "request", request_mock)
    result = client.get("/api/itinerary")
    assert result.status_code == 502
    assert result.get_json() == {"error": "itinerary database service is unavailable"}


@pytest.mark.parametrize(
    "upstream_response",
    [response(200, {"not": "a list"}), response(200, json_error=ValueError("bad JSON"))],
)
def test_malformed_database_response_is_safe(client, monkeypatch, upstream_response):
    monkeypatch.setattr(database_api.requests, "request", Mock(return_value=upstream_response))
    result = client.get("/api/itinerary")
    assert result.status_code == 502
    assert result.get_json() == {"error": "itinerary database service is unavailable"}
