"""Post-commit endpoint tests: two backend endpoint functions against the RUNNING stack.

Not mocked: these call the live backend on :5003, which talks to the live database
tier on :6003. They are marked `live`, so the default offline run skips them.
.github/workflows/student-3.yml starts the containers and runs them:

    docker compose -f activity-service/docker-compose.yml up -d --build
    cd activity-service && python -m pytest -m live

The two endpoint functions (backend/routes/activity_routes.py):
- add_activity     POST /api/add_activity     create, validated, persisted
- view_activities  GET  /api/view_activities  list with search / filter / sort

Every record a test creates is deleted again, so the tests can run against a stack
that already holds data.
"""
import os
import uuid

import pytest
import requests

pytestmark = pytest.mark.live

BACKEND = os.environ.get("ACTIVITY_BACKEND_URL", "http://localhost:5003").rstrip("/")
DATABASE = os.environ.get("ACTIVITY_DATABASE_URL", "http://localhost:6003").rstrip("/")
TIMEOUT = 10


@pytest.fixture
def created_ids():
    ids = []
    yield ids
    for activity_id in ids:
        requests.delete(f"{BACKEND}/api/delete_activity/{activity_id}", timeout=TIMEOUT)


def add(created_ids, **fields):
    resp = requests.post(f"{BACKEND}/api/add_activity", json=fields, timeout=TIMEOUT)
    assert resp.status_code == 201, resp.text
    created_ids.append(resp.json()["activity_id"])
    return resp.json()


# --- endpoint function 1: add_activity (POST /api/add_activity) ---

def test_add_activity_is_201_and_persists_in_the_database(created_ids):
    name = f"CI Harbour Kayak {uuid.uuid4().hex[:8]}"
    body = add(created_ids, activity_name=name, activity_type="Adventure", activity_cost=42.5,
               duration="2 hours", assignment_time="2026-11-03 09:00")
    assert body["activity_name"] == name and body["activity_cost"] == 42.5

    # Read back from the database tier directly, not through the backend, to prove persistence.
    row = requests.get(f"{DATABASE}/activities/{body['activity_id']}", timeout=TIMEOUT)
    assert row.status_code == 200
    assert row.json()["activity_name"] == name
    assignments = requests.get(f"{DATABASE}/activities/{body['activity_id']}/assignments", timeout=TIMEOUT)
    assert [a["assignment_time"] for a in assignments.json()] == ["2026-11-03 09:00"]


@pytest.mark.parametrize("payload, error", [
    ({"activity_type": "Food", "activity_cost": 10, "duration": "1 hour"}, "missing required field(s): activity_name"),
    ({"activity_name": "X", "activity_type": "Food", "activity_cost": "free", "duration": "1 hour"},
     "activity_cost must be a number"),
])
def test_add_activity_rejects_invalid_input_with_400(payload, error):
    resp = requests.post(f"{BACKEND}/api/add_activity", json=payload, timeout=TIMEOUT)
    assert resp.status_code == 400
    assert resp.json() == {"error": error}


# --- endpoint function 2: view_activities (GET /api/view_activities) ---

def test_view_activities_lists_a_created_record(created_ids):
    body = add(created_ids, activity_name=f"CI Museum {uuid.uuid4().hex[:8]}", activity_type="Culture",
               activity_cost=15, duration="1 hour")
    resp = requests.get(f"{BACKEND}/api/view_activities", timeout=TIMEOUT)
    assert resp.status_code == 200
    assert body["activity_id"] in [a["activity_id"] for a in resp.json()]


def test_view_activities_filters_and_sorts(created_ids):
    kind = f"CIType{uuid.uuid4().hex[:8]}"  # a type no other record has
    cheap = add(created_ids, activity_name="CI Cheap Walk", activity_type=kind, activity_cost=20,
                duration="1 hour", assignment_time="2026-11-04 10:00")
    dear = add(created_ids, activity_name="CI Dear Cruise", activity_type=kind, activity_cost=80,
               duration="3 hours", assignment_time="2026-11-05 18:00")

    resp = requests.get(f"{BACKEND}/api/view_activities",
                        params={"type": kind, "max_cost": 50, "include_dates": 1}, timeout=TIMEOUT)
    assert resp.status_code == 200
    assert [(a["activity_id"], a["assignment_time"]) for a in resp.json()] == [
        (cheap["activity_id"], "2026-11-04 10:00")]

    resp = requests.get(f"{BACKEND}/api/view_activities",
                        params={"type": kind, "min_duration": 2, "date_from": "2026-11-05"}, timeout=TIMEOUT)
    assert [a["activity_id"] for a in resp.json()] == [dear["activity_id"]]

    resp = requests.get(f"{BACKEND}/api/view_activities", params={"type": kind, "sort": "cost_desc"}, timeout=TIMEOUT)
    assert [a["activity_id"] for a in resp.json()] == [dear["activity_id"], cheap["activity_id"]]


def test_view_activities_rejects_a_bad_filter_with_400():
    resp = requests.get(f"{BACKEND}/api/view_activities", params={"min_cost": "cheap"}, timeout=TIMEOUT)
    assert resp.status_code == 400
    assert resp.json() == {"error": "min_cost must be a number"}
