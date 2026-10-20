"""Post-commit endpoint tests against the LIVE Budget containers (CI: docker compose + docker-compose.ci.yml).

They exercise the public backend (budget-be, :5004), which renders HTML for the HTMX-style UI, and
read records back as JSON from budget-db (:6004) to prove what was persisted. Every record carries
a unique trip_reference and is deleted afterwards.

    BUDGET_BE_URL (default http://localhost:5004)   BUDGET_DB_URL (default http://localhost:6004)
    pytest -m live budget-service/tests/test_endpoints.py --junitxml=junit-endpoint-tests.xml
"""
import os
import re
import uuid

import pytest
import requests

pytestmark = pytest.mark.live

BE = os.environ.get("BUDGET_BE_URL", "http://localhost:5004").rstrip("/")
DB = os.environ.get("BUDGET_DB_URL", "http://localhost:6004").rstrip("/")
TIMEOUT = 10
TAG = f"TEST-{uuid.uuid4().hex[:8].upper()}"
ID_IN_HTML = re.compile(r'data-expense-id="(\d+)"')


def valid_expense(**overrides):
    body = {"trip_reference": TAG, "expense": "Endpoint test taxi", "category": "Transport",
            "estimated_cost": 42.5, "destination_id": "dest-test", "status": "Planned"}
    body.update(overrides)
    return body


def db_rows():
    return requests.get(f"{DB}/expenses", params={"trip_reference": TAG}, timeout=TIMEOUT).json()


@pytest.fixture(scope="module", autouse=True)
def live_stack():
    try:
        requests.get(f"{BE}/", timeout=TIMEOUT).raise_for_status()
        requests.get(f"{DB}/expenses", timeout=TIMEOUT).raise_for_status()
    except requests.RequestException as exc:
        pytest.skip(f"Budget containers not reachable ({exc}); start them with docker compose first")
    yield
    for row in db_rows():  # clean up everything this module created
        requests.delete(f"{DB}/expenses/{row['id']}", timeout=TIMEOUT)


@pytest.fixture
def created():
    """Create one valid expense through the backend; returns its id."""
    response = requests.post(f"{BE}/expenses", json=valid_expense(), timeout=TIMEOUT)
    assert response.status_code == 201, response.text
    match = ID_IN_HTML.search(response.text)
    assert match, f"no expense id in the backend's HTML: {response.text[:200]}"
    return int(match.group(1))


# ---------------------------------------------------------------- POST /expenses

def test_post_valid_expense_is_201_and_reads_back(created):
    record = requests.get(f"{DB}/expenses/{created}", timeout=TIMEOUT).json()
    expected = valid_expense()
    assert {k: record[k] for k in expected} == expected
    page = requests.get(f"{BE}/expenses/{created}", timeout=TIMEOUT)
    assert page.status_code == 200 and "Endpoint test taxi" in page.text


@pytest.mark.parametrize("field", ["trip_reference", "expense", "category", "estimated_cost", "destination_id"])
def test_post_missing_required_field_is_400(field):
    before = len(db_rows())
    body = valid_expense()
    del body[field]
    response = requests.post(f"{BE}/expenses", json=body, timeout=TIMEOUT)
    assert response.status_code == 400 and field in response.text
    assert len(db_rows()) == before  # nothing persisted


@pytest.mark.parametrize("overrides, message", [
    ({"estimated_cost": "abc"}, "estimated_cost must be a number"),
    ({"estimated_cost": -5}, "estimated_cost must be a number"),
    ({"estimated_cost": True}, "estimated_cost must be a number"),
    ({"actual_cost": "lots"}, "actual_cost must be a number"),
    ({"status": "Maybe"}, "status must be one of"),
    ({"category": "   "}, "category must be a non-empty string"),
])
def test_post_invalid_field_is_400(overrides, message):
    before = len(db_rows())
    response = requests.post(f"{BE}/expenses", json=valid_expense(**overrides), timeout=TIMEOUT)
    assert response.status_code == 400 and message in response.text
    assert len(db_rows()) == before


# ---------------------------------------------------------------- PUT /expenses/<id>

def test_put_valid_update_persists(created):
    response = requests.put(f"{BE}/expenses/{created}", json={"actual_cost": 47.0, "status": "Paid"}, timeout=TIMEOUT)
    assert response.status_code == 200, response.text
    record = requests.get(f"{DB}/expenses/{created}", timeout=TIMEOUT).json()
    assert record["actual_cost"] == 47.0 and record["status"] == "Paid"
    assert record["estimated_cost"] == 42.5  # untouched fields stay


def test_put_unknown_id_is_404():
    response = requests.put(f"{BE}/expenses/987654321", json={"status": "Paid"}, timeout=TIMEOUT)
    assert response.status_code == 404


@pytest.mark.parametrize("body, message", [
    ({"estimated_cost": "abc"}, "estimated_cost must be a number"),
    ({"status": "Refunded"}, "status must be one of"),
    ({}, "no updatable fields"),
])
def test_put_invalid_data_is_400_and_record_unchanged(created, body, message):
    before = requests.get(f"{DB}/expenses/{created}", timeout=TIMEOUT).json()
    response = requests.put(f"{BE}/expenses/{created}", json=body, timeout=TIMEOUT)
    assert response.status_code == 400 and message in response.text
    assert requests.get(f"{DB}/expenses/{created}", timeout=TIMEOUT).json() == before
