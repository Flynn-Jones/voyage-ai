import os
import sqlite3
import sys

import pytest

BACKEND_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_ROOT not in sys.path:
    sys.path.insert(0, BACKEND_ROOT)

SCHEMA = """
CREATE TABLE IF NOT EXISTS activities (
    activity_id INTEGER PRIMARY KEY AUTOINCREMENT,
    activity_name TEXT NOT NULL,
    activity_type TEXT,
    activity_cost REAL,
    duration TEXT
);

CREATE TABLE IF NOT EXISTS activities_assignment (
    assignment_id INTEGER PRIMARY KEY AUTOINCREMENT,
    activity_id INTEGER NOT NULL,
    assignment_time TEXT,
    FOREIGN KEY (activity_id) REFERENCES activities (activity_id)
);
"""


class DummyResponse:
    """Minimal stand-in for requests.Response, matching the pattern already
    used in accommodation-service's own tests (test_forwarding.py)."""

    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


@pytest.fixture
def client(tmp_path, monkeypatch):
    """A Flask test client backed by a fresh temp SQLite file per test (for
    activity_store's direct-SQLite writes) — fully isolated, no shared state
    between tests and no dependency on a running database-service for the
    write path."""
    db_path = str(tmp_path / "test-activity.sqlite")
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA)
    conn.commit()
    conn.close()

    from services import activity_store
    monkeypatch.setattr(activity_store, "DB_PATH", db_path)

    import app as app_module
    flask_app = app_module.create_app()
    with flask_app.test_client() as test_client:
        yield test_client
