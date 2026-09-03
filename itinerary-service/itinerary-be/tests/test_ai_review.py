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
from services import database_api, llm_client
from services.schedule_analyzer import analyze_schedule

ITEMS = [
    {"itinerary_item_id": 1, "trip_reference": "TRIP-A", "day": 4, "start_time": "09:00", "end_time": "11:00", "activity_id": 1, "destination_id": 1, "estimated_cost": 20.5, "notes": "Museum"},
    {"itinerary_item_id": 2, "trip_reference": "TRIP-A", "day": 4, "start_time": "10:30", "end_time": "12:00", "activity_id": 2, "destination_id": 1, "estimated_cost": 30, "notes": "Lunch"},
]

@pytest.fixture()
def client():
    application = create_app()
    application.config.update(TESTING=True)
    return application.test_client()

def mock_success(monkeypatch, items=None, recommendation="Allow more time between activities."):
    database_mock = Mock(return_value=ITEMS if items is None else items)
    llm_mock = Mock(return_value={"model": "test-model", "recommendation": recommendation})
    monkeypatch.setattr(database_api, "get_items_by_day", database_mock)
    monkeypatch.setattr(llm_client, "review_itinerary", llm_mock)
    return database_mock, llm_mock

def test_valid_review_returns_all_workflow_stages(client, monkeypatch):
    mock_success(monkeypatch)
    result = client.post("/api/itinerary/ai-review", json={"prompt": "Review Day 4"})
    assert result.status_code == 200
    assert set(result.get_json()) == {"plan", "act", "observe", "adapt"}

def test_review_fetches_only_requested_day(client, monkeypatch):
    database_mock, _ = mock_success(monkeypatch)
    result = client.post("/api/itinerary/ai-review", json={"prompt": "Is this too busy?", "day": 4})
    assert result.get_json()["act"]["records_retrieved"] == 2
    database_mock.assert_called_once_with(4)

def test_review_handles_day_with_no_records(client, monkeypatch):
    mock_success(monkeypatch, items=[], recommendation="There are no records to review.")
    body = client.post("/api/itinerary/ai-review", json={"prompt": "Review Day 8"}).get_json()
    assert body["observe"]["item_count"] == 0
    assert body["observe"]["earliest_start"] is None

@pytest.mark.parametrize("body", [{"prompt": "Review my trip"}, {"prompt": "Review", "day": 0}, {"prompt": "Review", "day": True}, {"day": 4}, {"prompt": "  ", "day": 4}])
def test_missing_or_invalid_input_returns_400(client, monkeypatch, body):
    database_mock = Mock()
    monkeypatch.setattr(database_api, "get_items_by_day", database_mock)
    assert client.post("/api/itinerary/ai-review", json=body).status_code == 400
    database_mock.assert_not_called()

def test_overlap_detection():
    assert analyze_schedule(ITEMS)["overlaps"] == [{"first_item_id": 1, "second_item_id": 2, "overlap_minutes": 30}]

def test_duration_calculation():
    observations = analyze_schedule(ITEMS)
    assert observations["total_scheduled_minutes"] == 210
    assert observations["day_span_minutes"] == 180

def test_total_cost_calculation():
    assert analyze_schedule(ITEMS)["total_estimated_cost"] == 50.5

def test_invalid_schedule_and_cost_are_reported_safely():
    invalid = copy.deepcopy(ITEMS[0])
    invalid.update(start_time="bad", estimated_cost="unknown")
    observations = analyze_schedule([invalid])
    assert observations["invalid_schedule_items"][0]["itinerary_item_id"] == 1
    assert observations["invalid_cost_items"][0]["itinerary_item_id"] == 1

def test_database_unavailable_maps_to_502(client, monkeypatch):
    monkeypatch.setattr(database_api, "get_items_by_day", Mock(side_effect=database_api.DatabaseServiceError()))
    result = client.post("/api/itinerary/ai-review", json={"prompt": "Review", "day": 4})
    assert result.status_code == 502
    assert result.get_json() == {"error": "itinerary database service is unavailable"}

@pytest.mark.parametrize("error", [llm_client.LLMServiceError("offline"), llm_client.LLMServiceError("timeout")])
def test_ollama_failure_maps_to_502(client, monkeypatch, error):
    monkeypatch.setattr(database_api, "get_items_by_day", Mock(return_value=ITEMS))
    monkeypatch.setattr(llm_client, "review_itinerary", Mock(side_effect=error))
    result = client.post("/api/itinerary/ai-review", json={"prompt": "Review", "day": 4})
    assert result.status_code == 502
    assert result.get_json() == {"error": "AI review service is unavailable"}

def test_llm_timeout_is_wrapped(monkeypatch):
    monkeypatch.setattr(llm_client.requests, "post", Mock(side_effect=requests.Timeout("slow")))
    with pytest.raises(llm_client.LLMServiceError):
        llm_client.review_itinerary("Review", 4, ITEMS, analyze_schedule(ITEMS))

@pytest.mark.parametrize("body", [{}, {"message": {}}, {"message": {"content": "  "}}])
def test_malformed_or_empty_ollama_response_is_wrapped(monkeypatch, body):
    response = Mock()
    response.raise_for_status.return_value = None
    response.json.return_value = body
    monkeypatch.setattr(llm_client.requests, "post", Mock(return_value=response))
    with pytest.raises(llm_client.LLMServiceError):
        llm_client.review_itinerary("Review", 4, ITEMS, analyze_schedule(ITEMS))

def test_review_does_not_mutate_itinerary(client, monkeypatch):
    database_mock, llm_mock = mock_success(monkeypatch)
    before = copy.deepcopy(ITEMS)
    assert client.post("/api/itinerary/ai-review", json={"prompt": "Review", "day": 4}).status_code == 200
    database_mock.assert_called_once()
    llm_mock.assert_called_once()
    assert ITEMS == before
