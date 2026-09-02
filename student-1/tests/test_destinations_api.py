"""Live-stack CRUD tests for the destination backend + database APIs.

Requires the stack to be running:
    docker compose -f student-1/docker-compose.yml up -d --build
    pytest student-1/tests/test_destinations_api.py -v
"""
import requests

BACKEND_URL = "http://localhost:5001"
DATABASE_URL = "http://localhost:6001"


def test_seed_count():
    response = requests.get(f"{BACKEND_URL}/api/destinations", timeout=5)
    assert response.status_code == 200
    assert len(response.json()) >= 10


def test_tokyo_and_kyoto_seeded():
    response = requests.get(f"{BACKEND_URL}/api/destinations", timeout=5)
    cities = {d["city"] for d in response.json()}
    assert "Tokyo" in cities
    assert "Kyoto" in cities


def test_categories_is_a_list():
    response = requests.get(f"{BACKEND_URL}/api/destinations", timeout=5)
    tokyo = next(d for d in response.json() if d["city"] == "Tokyo")
    assert isinstance(tokyo["categories"], list)
    assert len(tokyo["categories"]) > 0


def test_crud_lifecycle():
    created_id = None
    try:
        create_response = requests.post(
            f"{BACKEND_URL}/api/destinations",
            json={
                "city": "Demo City",
                "country": "Australia",
                "description": "Temporary test record",
                "average_daily_cost": 150,
                "recommended_trip_length": 3,
                "travel_style": "City break",
                "categories": ["food"],
            },
            timeout=5,
        )
        assert create_response.status_code == 201
        created = create_response.json()
        created_id = created["destination_id"]
        assert created["city"] == "Demo City"

        get_response = requests.get(f"{BACKEND_URL}/api/destinations/{created_id}", timeout=5)
        assert get_response.status_code == 200
        assert get_response.json()["city"] == "Demo City"

        update_response = requests.put(
            f"{BACKEND_URL}/api/destinations/{created_id}",
            json={"average_daily_cost": 175, "categories": ["food", "beach"]},
            timeout=5,
        )
        assert update_response.status_code == 200
        updated = update_response.json()
        assert updated["average_daily_cost"] == 175
        assert updated["categories"] == ["food", "beach"]

        confirm_response = requests.get(f"{BACKEND_URL}/api/destinations/{created_id}", timeout=5)
        assert confirm_response.json()["average_daily_cost"] == 175

        delete_response = requests.delete(
            f"{BACKEND_URL}/api/destinations/{created_id}", timeout=5
        )
        assert delete_response.status_code == 204

        missing_response = requests.get(
            f"{BACKEND_URL}/api/destinations/{created_id}", timeout=5
        )
        assert missing_response.status_code == 404
        created_id = None
    finally:
        if created_id is not None:
            requests.delete(f"{BACKEND_URL}/api/destinations/{created_id}", timeout=5)


def test_get_missing_returns_404():
    response = requests.get(f"{BACKEND_URL}/api/destinations/999999", timeout=5)
    assert response.status_code == 404
    assert "error" in response.json()


def test_post_missing_city_returns_400():
    response = requests.post(
        f"{BACKEND_URL}/api/destinations", json={"country": "Japan"}, timeout=5
    )
    assert response.status_code == 400
    assert "error" in response.json()


def test_post_bad_cost_returns_400():
    response = requests.post(
        f"{BACKEND_URL}/api/destinations",
        json={"city": "X", "country": "Y", "average_daily_cost": "not-a-number"},
        timeout=5,
    )
    assert response.status_code == 400
    assert "error" in response.json()


def test_database_api_direct_read():
    response = requests.get(f"{DATABASE_URL}/destinations", timeout=5)
    assert response.status_code == 200
    assert len(response.json()) >= 10
