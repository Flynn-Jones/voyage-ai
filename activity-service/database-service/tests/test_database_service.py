"""Tests for activity-service/database-service's read-only REST endpoints.

Run in its own pytest invocation, separate from backend/frontend's tests:
all three layers have a top-level `app` module, and combining them in one
pytest command lets Python's module cache serve the wrong one. Run as:
    python -m pytest activity-service/database-service/tests
"""
from conftest import seed_activity, seed_assignment


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
