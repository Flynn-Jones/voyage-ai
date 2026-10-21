"""Post-commit endpoint tests against the LIVE Accommodation containers (CI: docker compose +
docker-compose.ci.yml).

They exercise the public backend (accommodation-be, :5002), a pure JSON REST API, and read
records back from accommodation-db (:6002) directly to prove what was persisted. Every record
created here carries a unique name tag and is deleted afterwards.

    ACCOMMODATION_BE_URL (default http://localhost:5002)
    ACCOMMODATION_DB_URL (default http://localhost:6002)
    pytest -m live accommodation-service/tests/test_endpoints.py --junitxml=junit-endpoint-tests.xml
"""
import os
import uuid

import pytest
import requests

pytestmark = pytest.mark.live

BE = os.environ.get("ACCOMMODATION_BE_URL", "http://localhost:5002").rstrip("/")
DB = os.environ.get("ACCOMMODATION_DB_URL", "http://localhost:6002").rstrip("/")
TIMEOUT = 10
TAG = f"TEST-{uuid.uuid4().hex[:8].upper()}"


def valid_accommodation(**overrides):
    body = {"name": f"{TAG} Test Hotel", "destination_id": "dest-test", "destination_city": "TestCity",
            "price_per_night": 42.5, "type": "hotel"}
    body.update(overrides)
    return body


def listed(params=None):
    query = {"q": TAG, "limit": 100}
    query.update(params or {})
    return requests.get(f"{BE}/accommodations", params=query, timeout=TIMEOUT).json()


@pytest.fixture(scope="module", autouse=True)
def live_stack():
    try:
        requests.get(f"{BE}/health", timeout=TIMEOUT).raise_for_status()
        requests.get(f"{DB}/health", timeout=TIMEOUT).raise_for_status()
    except requests.RequestException as exc:
        pytest.skip(f"Accommodation containers not reachable ({exc}); start them with docker compose first")
    yield
    for row in listed()["data"]:  # clean up everything this module created
        requests.delete(f"{BE}/accommodations/{row['id']}", timeout=TIMEOUT)


@pytest.fixture
def created():
    """Create one valid accommodation through the backend; returns its id."""
    response = requests.post(f"{BE}/accommodations", json=valid_accommodation(), timeout=TIMEOUT)
    assert response.status_code == 201, response.text
    return response.json()["id"]


# ---------------------------------------------------------------- POST /accommodations (create_accommodation)

def test_post_valid_accommodation_is_201_and_reads_back(created):
    expected = valid_accommodation()
    db_record = requests.get(f"{DB}/accommodations/{created}", timeout=TIMEOUT).json()
    assert db_record["name"] == expected["name"]
    assert db_record["destination_id"] == expected["destination_id"]
    assert db_record["price_per_night"] == expected["price_per_night"]

    be_record = requests.get(f"{BE}/accommodations/{created}", timeout=TIMEOUT)
    assert be_record.status_code == 200
    assert be_record.json()["name"] == expected["name"]


@pytest.mark.parametrize("field", ["name", "destination_id", "price_per_night"])
def test_post_missing_required_field_is_422(field):
    before = listed()["total"]
    body = valid_accommodation()
    del body[field]
    response = requests.post(f"{BE}/accommodations", json=body, timeout=TIMEOUT)
    assert response.status_code == 422 and field in response.text
    assert listed()["total"] == before  # nothing persisted


# ---------------------------------------------------------------- GET /accommodations (list_accommodations)

def test_get_list_includes_created_record(created):
    result = listed()
    assert result["total"] >= 1
    assert created in [row["id"] for row in result["data"]]


def test_get_list_filters_by_max_price(created):
    too_cheap = listed({"max_price": 10})
    assert created not in [row["id"] for row in too_cheap["data"]]

    affordable = listed({"max_price": 100})
    assert created in [row["id"] for row in affordable["data"]]
