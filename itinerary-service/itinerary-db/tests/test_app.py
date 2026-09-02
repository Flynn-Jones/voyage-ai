import copy
import sys
from pathlib import Path

import pytest


DB_ROOT = Path(__file__).resolve().parents[1]
if str(DB_ROOT) not in sys.path:
    sys.path.insert(0, str(DB_ROOT))


VALID_ITEM = {
    "trip_reference": "TRIP-TEST",
    "day": 3,
    "start_time": "09:15",
    "end_time": "10:45",
    "activity_id": 901,
    "destination_id": 91,
    "estimated_cost": 25.5,
    "notes": "Test itinerary item",
}


@pytest.fixture()
def client(tmp_path):
    from app import create_app

    application = create_app(str(tmp_path / "test-itinerary.sqlite"))
    application.config.update(TESTING=True)
    return application.test_client()


def create_item(client, **changes):
    payload = copy.deepcopy(VALID_ITEM)
    payload.update(changes)
    return client.post("/itinerary-items", json=payload)


def test_get_all_records(client):
    response = client.get("/itinerary-items")
    assert response.status_code == 200
    assert isinstance(response.get_json(), list)


def test_seed_count_is_at_least_ten(client):
    assert len(client.get("/itinerary-items").get_json()) >= 10


def test_get_one_valid_item(client):
    seeded = client.get("/itinerary-items").get_json()[0]
    response = client.get(f"/itinerary-items/{seeded['itinerary_item_id']}")
    assert response.status_code == 200
    assert response.get_json()["itinerary_item_id"] == seeded["itinerary_item_id"]


def test_get_nonexistent_item_returns_404(client):
    assert client.get("/itinerary-items/999999").status_code == 404


def test_post_valid_item_returns_201(client):
    response = create_item(client)
    assert response.status_code == 201
    assert response.get_json()["trip_reference"] == "TRIP-TEST"


def test_post_missing_required_field_returns_400(client):
    payload = copy.deepcopy(VALID_ITEM)
    del payload["activity_id"]
    assert client.post("/itinerary-items", json=payload).status_code == 400


def test_post_invalid_day_returns_400(client):
    assert create_item(client, day=0).status_code == 400


def test_post_invalid_time_format_returns_400(client):
    assert create_item(client, start_time="9:15").status_code == 400


def test_post_end_not_after_start_returns_400(client):
    assert create_item(client, start_time="10:00", end_time="10:00").status_code == 400


def test_post_negative_estimated_cost_returns_400(client):
    assert create_item(client, estimated_cost=-0.01).status_code == 400


def test_put_valid_update(client):
    item_id = create_item(client).get_json()["itinerary_item_id"]
    payload = copy.deepcopy(VALID_ITEM)
    payload.update({"day": 4, "notes": "Updated"})
    response = client.put(f"/itinerary-items/{item_id}", json=payload)
    assert response.status_code == 200
    assert response.get_json()["day"] == 4
    assert response.get_json()["notes"] == "Updated"


def test_put_nonexistent_item_returns_404(client):
    assert client.put("/itinerary-items/999999", json=VALID_ITEM).status_code == 404


def test_delete_valid_item_returns_204(client):
    item_id = create_item(client).get_json()["itinerary_item_id"]
    assert client.delete(f"/itinerary-items/{item_id}").status_code == 204
    assert client.get(f"/itinerary-items/{item_id}").status_code == 404


def test_delete_nonexistent_item_returns_404(client):
    assert client.delete("/itinerary-items/999999").status_code == 404


def test_get_by_day_returns_only_selected_day(client):
    records = client.get("/itinerary-items/day/2").get_json()
    assert records
    assert all(item["day"] == 2 for item in records)


def test_get_by_day_orders_items_by_start_time(client):
    create_item(client, day=7, start_time="15:00", end_time="16:00")
    create_item(client, day=7, start_time="08:00", end_time="09:00")
    records = client.get("/itinerary-items/day/7").get_json()
    times = [item["start_time"] for item in records]
    assert times == sorted(times)
