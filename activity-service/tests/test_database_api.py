"""Tests for activity-service/database's read-only REST endpoints.

Run with the rest of the suite, from activity-service/: python -m pytest
"""
import pytest

from conftest import seed_activity, seed_assignment


# This file's tests use `client` for the database tier's test client.
@pytest.fixture
def client(database_client):
    return database_client


def test_health_200(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.get_json()["status"] == "ok"


def test_list_activities_empty(client):
    resp = client.get("/activities")
    assert resp.status_code == 200
    assert resp.get_json() == []


def test_list_activities_returns_seeded_rows(client, db_path):
    seed_activity(db_path, activity_name="Kayaking")
    seed_activity(db_path, activity_name="Skiing")

    resp = client.get("/activities")
    assert resp.status_code == 200
    names = [row["activity_name"] for row in resp.get_json()]
    assert names == ["Kayaking", "Skiing"]


def test_get_activity_known_id_200(client, db_path):
    activity_id = seed_activity(db_path, activity_name="Kayaking")
    resp = client.get(f"/activities/{activity_id}")
    assert resp.status_code == 200
    assert resp.get_json()["activity_name"] == "Kayaking"


def test_get_activity_unknown_id_404(client):
    resp = client.get("/activities/999")
    assert resp.status_code == 404
    assert "error" in resp.get_json()


def test_list_assignments_empty(client):
    resp = client.get("/assignments")
    assert resp.status_code == 200
    assert resp.get_json() == []


def test_list_assignments_returns_seeded_rows(client, db_path):
    activity_id = seed_activity(db_path)
    seed_assignment(db_path, activity_id, assignment_time="2026-09-20T10:00")

    resp = client.get("/assignments")
    assert resp.status_code == 200
    body = resp.get_json()
    assert len(body) == 1
    assert body[0]["assignment_time"] == "2026-09-20T10:00"


def test_get_assignment_known_id_200(client, db_path):
    activity_id = seed_activity(db_path)
    assignment_id = seed_assignment(db_path, activity_id)

    resp = client.get(f"/assignments/{assignment_id}")
    assert resp.status_code == 200
    assert resp.get_json()["activity_id"] == activity_id


def test_get_assignment_unknown_id_404(client):
    resp = client.get("/assignments/999")
    assert resp.status_code == 404


def test_activity_assignments_valid_activity_with_assignment(client, db_path):
    activity_id = seed_activity(db_path)
    seed_assignment(db_path, activity_id, assignment_time="2026-09-20T10:00")

    resp = client.get(f"/activities/{activity_id}/assignments")
    assert resp.status_code == 200
    body = resp.get_json()
    assert len(body) == 1
    assert body[0]["assignment_time"] == "2026-09-20T10:00"


def test_activity_assignments_valid_activity_no_assignment_yet(client, db_path):
    activity_id = seed_activity(db_path)
    resp = client.get(f"/activities/{activity_id}/assignments")
    assert resp.status_code == 200
    assert resp.get_json() == []


def test_activity_assignments_unknown_activity_404(client):
    resp = client.get("/activities/999/assignments")
    assert resp.status_code == 404
    assert resp.get_json()["error"] == "activity not found"


# ---------------------------------------------------------------------------
# POST /activities, PATCH /activities/<id>, DELETE /activities/<id>
#
# This is the write path moved here from activity-service/backend's own
# activity_store.py, which used to open this same SQLite file directly
# (a documented data-path violation). The database tier is now the only
# place that touches SQLite, same as every other feature.
# ---------------------------------------------------------------------------

def test_create_activity_success_201(client):
    resp = client.post("/activities", json={
        "activity_name": "Kayaking", "activity_type": "Adventure",
        "activity_cost": 35, "duration": "1.5 hours",
    })
    assert resp.status_code == 201
    body = resp.get_json()
    assert body["activity_name"] == "Kayaking"
    assert body["activity_cost"] == 35.0
    assert "activity_id" in body
    assert "assignment_id" not in body  # no assignment_time given


def test_create_activity_with_assignment_time_also_creates_assignment(client):
    resp = client.post("/activities", json={
        "activity_name": "Kayaking", "activity_type": "Adventure",
        "activity_cost": 35, "duration": "1.5 hours", "assignment_time": "2026-09-20T10:00",
    })
    assert resp.status_code == 201
    body = resp.get_json()
    assert body["assignment_time"] == "2026-09-20T10:00"

    assignments = client.get(f"/activities/{body['activity_id']}/assignments").get_json()
    assert len(assignments) == 1
    assert assignments[0]["assignment_time"] == "2026-09-20T10:00"


def test_create_activity_persists_and_is_readable(client):
    created = client.post("/activities", json={
        "activity_name": "Kayaking", "activity_type": "Adventure",
        "activity_cost": 35, "duration": "1.5 hours",
    }).get_json()

    resp = client.get(f"/activities/{created['activity_id']}")
    assert resp.status_code == 200
    assert resp.get_json()["activity_name"] == "Kayaking"


def test_create_activity_missing_required_field_400(client):
    resp = client.post("/activities", json={"activity_name": "Kayaking"})
    assert resp.status_code == 400
    assert "error" in resp.get_json()


def test_create_activity_invalid_cost_400(client):
    resp = client.post("/activities", json={
        "activity_name": "Kayaking", "activity_type": "Adventure",
        "activity_cost": "free", "duration": "1 hour",
    })
    assert resp.status_code == 400


def test_update_activity_success_200(client, db_path):
    activity_id = seed_activity(db_path, activity_cost=35.0)
    resp = client.patch(f"/activities/{activity_id}", json={"activity_cost": 40})
    assert resp.status_code == 200
    assert resp.get_json()["activity_cost"] == 40.0


def test_update_activity_not_found_404(client):
    resp = client.patch("/activities/999", json={"activity_cost": 40})
    assert resp.status_code == 404


def test_update_activity_invalid_cost_400(client, db_path):
    activity_id = seed_activity(db_path)
    resp = client.patch(f"/activities/{activity_id}", json={"activity_cost": "free"})
    assert resp.status_code == 400


def test_update_activity_sets_new_assignment_time(client, db_path):
    activity_id = seed_activity(db_path)
    resp = client.patch(f"/activities/{activity_id}", json={"assignment_time": "2026-10-01T09:00"})
    assert resp.status_code == 200
    assignments = client.get(f"/activities/{activity_id}/assignments").get_json()
    assert len(assignments) == 1
    assert assignments[0]["assignment_time"] == "2026-10-01T09:00"


def test_update_activity_replaces_existing_assignment_time(client, db_path):
    activity_id = seed_activity(db_path)
    seed_assignment(db_path, activity_id, assignment_time="2026-09-20T10:00")

    resp = client.patch(f"/activities/{activity_id}", json={"assignment_time": "2026-10-01T09:00"})
    assert resp.status_code == 200
    assignments = client.get(f"/activities/{activity_id}/assignments").get_json()
    assert len(assignments) == 1  # updated in place, not a second row
    assert assignments[0]["assignment_time"] == "2026-10-01T09:00"


def test_delete_activity_success_200(client, db_path):
    activity_id = seed_activity(db_path)
    resp = client.delete(f"/activities/{activity_id}")
    assert resp.status_code == 200
    assert resp.get_json()["message"] == "deleted"
    assert client.get(f"/activities/{activity_id}").status_code == 404


def test_delete_activity_also_deletes_its_assignments(client, db_path):
    activity_id = seed_activity(db_path)
    seed_assignment(db_path, activity_id)

    resp = client.delete(f"/activities/{activity_id}")
    assert resp.status_code == 200
    assert client.get("/assignments").get_json() == []


def test_delete_activity_not_found_404(client):
    resp = client.delete("/activities/999")
    assert resp.status_code == 404
