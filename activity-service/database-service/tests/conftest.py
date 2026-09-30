import os
import sqlite3
import sys

import pytest

DB_SERVICE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if DB_SERVICE_ROOT not in sys.path:
    sys.path.insert(0, DB_SERVICE_ROOT)


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    """Point init_db.DB_PATH (read fresh on every get_connection() call, not
    cached at import) at a fresh temp file per test — full isolation, no
    shared state between tests and no dependency on the real dev database."""
    path = str(tmp_path / "test-database.sqlite")
    import init_db
    monkeypatch.setattr(init_db, "DB_PATH", path)
    return path


@pytest.fixture
def client(db_path):
    import app as app_module
    flask_app = app_module.create_app()  # runs init_schema() against db_path
    with flask_app.test_client() as test_client:
        yield test_client


def seed_activity(db_path, activity_name="Kayaking", activity_type="Adventure",
                   activity_cost=35.0, duration="1.5 hours"):
    conn = sqlite3.connect(db_path)
    cursor = conn.execute(
        "INSERT INTO activities (activity_name, activity_type, activity_cost, duration) "
        "VALUES (?, ?, ?, ?)",
        (activity_name, activity_type, activity_cost, duration),
    )
    conn.commit()
    activity_id = cursor.lastrowid
    conn.close()
    return activity_id


def seed_assignment(db_path, activity_id, assignment_time="2026-09-20T10:00"):
    conn = sqlite3.connect(db_path)
    cursor = conn.execute(
        "INSERT INTO activities_assignment (activity_id, assignment_time) VALUES (?, ?)",
        (activity_id, assignment_time),
    )
    conn.commit()
    assignment_id = cursor.lastrowid
    conn.close()
    return assignment_id
