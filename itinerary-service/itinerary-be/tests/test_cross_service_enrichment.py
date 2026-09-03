import sys
from pathlib import Path
from unittest.mock import Mock

import pytest
import requests


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app import create_app
from services import activity_api, database_api, destination_api


ITEM = {
    "itinerary_item_id": 1,
    "trip_reference": "TRIP-A",
    "day": 2,
    "start_time": "09:00",
    "end_time": "10:00",
    "activity_id": 7,
    "destination_id": 3,
    "estimated_cost": 10.0,
    "notes": "Test",
}
RAW_DESTINATION = {
    "destination_id": 3,
    "city": "Tokyo",
    "country": "Japan",
    "description": "Raw dependency field",
    "average_daily_cost": 150.0,
    "recommended_trip_length": 6,
    "travel_style": "City break",
    "categories": ["culture"],
}
RAW_ACTIVITY = {
    "activity_id": 7,
    "activity_name": "Museum visit",
    "activity_type": "Sightseeing",
    "activity_cost": 25.0,
    "duration": "2 hours",
}


@pytest.fixture()
def client():
    application = create_app()
    application.config.update(TESTING=True)
    return application.test_client()


def response(status, body=None, json_error=None):
    mocked = Mock(status_code=status)
    if json_error:
        mocked.json.side_effect = json_error
    else:
        mocked.json.return_value = body
    return mocked


def test_destination_adapter_success_normalizes_response(monkeypatch):
    request_mock = Mock(return_value=response(200, RAW_DESTINATION))
    monkeypatch.setattr(destination_api.requests, "get", request_mock)
    assert destination_api.get_destination(3) == {"id": 3, "city": "Tokyo", "country": "Japan"}
    assert request_mock.call_args.args[0].endswith("/destinations/3")


def test_destination_404(monkeypatch):
    monkeypatch.setattr(destination_api.requests, "get", Mock(return_value=response(404, {"error": "destination not found"})))
    with pytest.raises(destination_api.DestinationNotFoundError):
        destination_api.get_destination(3)


def test_destination_unavailable(monkeypatch):
    monkeypatch.setattr(destination_api.requests, "get", Mock(side_effect=requests.ConnectionError("offline")))
    with pytest.raises(destination_api.DestinationUnavailableError):
        destination_api.get_destination(3)


def test_destination_timeout(monkeypatch):
    monkeypatch.setattr(destination_api.requests, "get", Mock(side_effect=requests.Timeout("slow")))
    with pytest.raises(destination_api.DestinationUnavailableError):
        destination_api.get_destination(3)


@pytest.mark.parametrize("upstream", [response(200, {"destination_id": 3}), response(200, json_error=ValueError("bad JSON")), response(500, {})])
def test_malformed_or_unexpected_destination_response(monkeypatch, upstream):
    monkeypatch.setattr(destination_api.requests, "get", Mock(return_value=upstream))
    with pytest.raises(destination_api.DestinationServiceError):
        destination_api.get_destination(3)


def test_activity_adapter_success_normalizes_response(monkeypatch):
    request_mock = Mock(return_value=response(200, RAW_ACTIVITY))
    monkeypatch.setattr(activity_api.requests, "get", request_mock)
    assert activity_api.get_activity(7) == {"id": 7, "name": "Museum visit", "type": "Sightseeing", "cost": 25.0, "duration": "2 hours"}
    assert request_mock.call_args.args[0].endswith("/activities/7")


def test_activity_404(monkeypatch):
    monkeypatch.setattr(activity_api.requests, "get", Mock(return_value=response(404, {"error": "activity not found"})))
    with pytest.raises(activity_api.ActivityNotFoundError):
        activity_api.get_activity(7)


def test_activity_unavailable(monkeypatch):
    monkeypatch.setattr(activity_api.requests, "get", Mock(side_effect=requests.ConnectionError("offline")))
    with pytest.raises(activity_api.ActivityUnavailableError):
        activity_api.get_activity(7)


def test_activity_timeout(monkeypatch):
    monkeypatch.setattr(activity_api.requests, "get", Mock(side_effect=requests.Timeout("slow")))
    with pytest.raises(activity_api.ActivityUnavailableError):
        activity_api.get_activity(7)


@pytest.mark.parametrize("upstream", [response(200, {"activity_id": 7}), response(200, json_error=ValueError("bad JSON")), response(503, {})])
def test_malformed_or_unexpected_activity_response(monkeypatch, upstream):
    monkeypatch.setattr(activity_api.requests, "get", Mock(return_value=upstream))
    with pytest.raises(activity_api.ActivityServiceError):
        activity_api.get_activity(7)


def configure_enrichment(monkeypatch, destination=RAW_DESTINATION, activity=RAW_ACTIVITY):
    monkeypatch.setattr(database_api, "get_items", Mock(return_value=[ITEM]))
    monkeypatch.setattr(database_api, "get_item", Mock(return_value=ITEM))
    monkeypatch.setattr(database_api, "get_items_by_day", Mock(return_value=[ITEM]))
    if isinstance(destination, Exception):
        monkeypatch.setattr(destination_api, "get_destination", Mock(side_effect=destination))
    else:
        monkeypatch.setattr(destination_api, "get_destination", Mock(return_value={"id": 3, "city": "Tokyo", "country": "Japan"}))
    if isinstance(activity, Exception):
        monkeypatch.setattr(activity_api, "get_activity", Mock(side_effect=activity))
    else:
        monkeypatch.setattr(activity_api, "get_activity", Mock(return_value={"id": 7, "name": "Museum visit", "type": "Sightseeing", "cost": 25.0, "duration": "2 hours"}))


@pytest.mark.parametrize("path,is_list", [("/api/itinerary", True), ("/api/itinerary/1", False), ("/api/itinerary/day/2", True)])
def test_read_endpoints_are_enriched(client, monkeypatch, path, is_list):
    configure_enrichment(monkeypatch)
    result = client.get(path)
    assert result.status_code == 200
    item = result.get_json()[0] if is_list else result.get_json()
    assert item["destination"] == {"id": 3, "city": "Tokyo", "country": "Japan"}
    assert item["activity"]["name"] == "Museum visit"
    assert item["destination_id"] == 3 and item["activity_id"] == 7


def test_destination_failure_does_not_break_response(client, monkeypatch):
    configure_enrichment(monkeypatch, destination=destination_api.DestinationNotFoundError())
    item = client.get("/api/itinerary/1").get_json()
    assert item["destination"] is None and item["activity"] is not None


def test_activity_failure_does_not_break_response(client, monkeypatch):
    configure_enrichment(monkeypatch, activity=activity_api.ActivityUnavailableError())
    item = client.get("/api/itinerary/1").get_json()
    assert item["activity"] is None and item["destination"] is not None


def test_both_services_unavailable_preserves_stored_item(client, monkeypatch):
    configure_enrichment(
        monkeypatch,
        destination=destination_api.DestinationUnavailableError(),
        activity=activity_api.ActivityUnavailableError(),
    )
    item = client.get("/api/itinerary/1").get_json()
    assert item["destination"] is None and item["activity"] is None
    for key, value in ITEM.items():
        assert item[key] == value


def test_normalized_output_excludes_raw_dependency_fields(client, monkeypatch):
    configure_enrichment(monkeypatch)
    item = client.get("/api/itinerary/1").get_json()
    assert "destination_id" not in item["destination"]
    assert "activity_name" not in item["activity"]
    assert "description" not in item["destination"]


def test_cross_service_adapters_do_not_access_sqlite_directly():
    for module in (destination_api, activity_api):
        source = Path(module.__file__).read_text(encoding="utf-8")
        assert "sqlite3" not in source
        assert ".sqlite" not in source
