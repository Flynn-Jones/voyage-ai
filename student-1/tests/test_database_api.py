"""Offline unit tests for database/app.py's /destinations CRUD routes.

No Docker required: loads database/app.py by path against a fresh tmp_path
SQLite file (seeded via init_db, the same module the container itself runs
at startup), then drives it with Flask's test client. This is the one layer
of the stack that owns SQLite directly, and until this file it had no
offline coverage at all — everything about it was proven only indirectly,
through a live backend, in test_destinations_api.py.

Kept to the highest-value branches per route rather than an exhaustive
validation matrix; test_destinations_api.py (live) already exercises the
full CRUD lifecycle end to end.
"""
import importlib.util
import os
import sys

import pytest

DATABASE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "database"
)
if DATABASE_DIR not in sys.path:
    # app.py does `from init_db import get_connection` (a sibling module,
    # not a package); loading app.py by path doesn't add its own directory
    # to sys.path the way running it as __main__ would, so this needs to be
    # explicit.
    sys.path.insert(0, DATABASE_DIR)

import init_db  # noqa: E402  (must follow the sys.path.insert above)


def _load_database_app():
    module_path = os.path.join(DATABASE_DIR, "app.py")
    spec = importlib.util.spec_from_file_location("destination_database_app", module_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["destination_database_app"] = module
    spec.loader.exec_module(module)
    return module


db_app = _load_database_app()


@pytest.fixture
def client(tmp_path, monkeypatch):
    db_file = str(tmp_path / "test.sqlite")
    conn = init_db.get_connection(db_file)
    init_db.init_schema(conn)
    init_db.seed_if_empty(conn)
    conn.close()
    # app.py calls the bare name get_connection() (imported via `from
    # init_db import get_connection`), so patch it on the app module, not on
    # init_db, and point every call at this test's own tmp_path file.
    monkeypatch.setattr(db_app, "get_connection", lambda: init_db.get_connection(db_file))
    return db_app.app.test_client()


def test_health_reports_ok(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.get_json()["status"] == "ok"


def test_list_returns_ten_seeded_rows_ordered_by_id(client):
    response = client.get("/destinations")
    assert response.status_code == 200
    rows = response.get_json()
    assert len(rows) == 10
    assert [r["destination_id"] for r in rows] == sorted(r["destination_id"] for r in rows)


def test_categories_come_back_decoded_as_a_list(client):
    rows = client.get("/destinations").get_json()
    tokyo = next(r for r in rows if r["city"] == "Tokyo")
    assert isinstance(tokyo["categories"], list)
    assert "food" in tokyo["categories"]


def test_country_filter_narrows_results(client):
    response = client.get("/destinations?country=Japan")
    cities = {r["city"] for r in response.get_json()}
    assert cities == {"Tokyo", "Kyoto", "Osaka"}


def test_get_missing_destination_returns_404(client):
    response = client.get("/destinations/999999")
    assert response.status_code == 404
    assert "error" in response.get_json()


def test_create_persists_categories_as_json_and_round_trips_as_a_list(client):
    create = client.post(
        "/destinations",
        json={
            "city": "Demo City", "country": "Australia",
            "categories": ["food", "beach"],
        },
    )
    assert create.status_code == 201
    body = create.get_json()
    assert body["categories"] == ["food", "beach"]

    fetched = client.get(f"/destinations/{body['destination_id']}").get_json()
    assert fetched["categories"] == ["food", "beach"]


def test_create_missing_city_returns_400(client):
    response = client.post("/destinations", json={"country": "Australia"})
    assert response.status_code == 400
    assert "error" in response.get_json()


def test_create_non_numeric_cost_returns_400(client):
    response = client.post(
        "/destinations",
        json={"city": "X", "country": "Y", "average_daily_cost": "not-a-number"},
    )
    assert response.status_code == 400
    assert "error" in response.get_json()


def test_update_partial_leaves_other_columns_untouched(client):
    update = client.put("/destinations/1", json={"average_daily_cost": 999.0})
    assert update.status_code == 200
    body = update.get_json()
    assert body["average_daily_cost"] == 999.0
    assert body["city"] == "Tokyo"  # untouched by the partial update


def test_update_missing_destination_returns_404(client):
    response = client.put("/destinations/999999", json={"average_daily_cost": 1})
    assert response.status_code == 404


def test_delete_then_get_returns_404_delete_missing_also_404(client):
    delete = client.delete("/destinations/1")
    assert delete.status_code == 204

    missing = client.get("/destinations/1")
    assert missing.status_code == 404

    delete_again = client.delete("/destinations/1")
    assert delete_again.status_code == 404
