"""Shared fixtures for every activity-service test (backend, database,
frontend, and the shared MCP/RAG bridges), all run in one pytest invocation
from activity-service/ — the same single tests/ layout as student-1.

backend/, database/ and frontend/ each have a top-level app.py, so none of
them is imported as plain `app` (the module cache would hand one tier's app
to another tier's tests). Each is loaded by path under its own module name
instead, like student-1/tests does. backend/ and database/ go on sys.path for
their sibling imports (backend: routes/, services/; database: init_db,
db_routes); their module names don't overlap.
"""
import importlib.util
import sqlite3
import sys
from pathlib import Path

import pytest

SERVICE_ROOT = Path(__file__).resolve().parent.parent
BACKEND_DIR = SERVICE_ROOT / "backend"
DATABASE_DIR = SERVICE_ROOT / "database"
FRONTEND_DIR = SERVICE_ROOT / "frontend"
for path in (BACKEND_DIR, DATABASE_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))


def _load_app(module_name, tier_dir):
    if module_name not in sys.modules:
        spec = importlib.util.spec_from_file_location(module_name, tier_dir / "app.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
    return sys.modules[module_name]


def load_backend_app():
    return _load_app("activity_backend_app", BACKEND_DIR)


def load_database_app():
    return _load_app("activity_database_app", DATABASE_DIR)


def load_frontend_app():
    return _load_app("activity_frontend_app", FRONTEND_DIR)


class DummyResponse:
    """Minimal stand-in for requests.Response. headers is there because the
    frontend checks Content-Type before calling .json() on error bodies."""

    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload
        self.headers = {"Content-Type": "application/json"}

    def json(self):
        return self._payload

    @property
    def text(self):
        return "" if self._payload is None else str(self._payload)


# --- backend ---

@pytest.fixture
def backend_client():
    """The backend never touches SQLite directly (it reads AND writes through
    the database tier's HTTP API), so every test that hits
    create/read/update/delete mocks db_client's requests calls."""
    with load_backend_app().create_app().test_client() as test_client:
        yield test_client


# --- database ---

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
def database_client(db_path):
    # seed_demo_data=False: the route tests assert exact rows on an empty DB;
    # startup seeding has its own tests (test_database_seed.py).
    flask_app = load_database_app().create_app(seed_demo_data=False)  # runs init_schema() against db_path
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


# --- frontend ---

SAMPLE_ACTIVITY = {
    "activity_id": 1,
    "activity_name": "Kayaking",
    "activity_type": "Adventure",
    "activity_cost": 35.0,
    "duration": "1.5 hours",
}


def make_fake_backend(activities=None, activity=None, assignments=None,
                       summary="A fun kayaking trip.", reply="Sure, here's an answer."):
    """Builds a fake for `requests.request` covering every backend endpoint
    app.py's routes call, dispatching on the URL path since app.py's
    backend_request() always calls the same generic requests.request()."""
    activities = activities if activities is not None else [SAMPLE_ACTIVITY]
    activity = activity if activity is not None else SAMPLE_ACTIVITY
    assignments = assignments if assignments is not None else []

    def fake_request(method, url, params=None, json=None, timeout=None):
        if url.endswith("/api/view_activities"):
            return DummyResponse(200, activities)
        if "/api/view_activity/" in url:
            return DummyResponse(200, activity)
        if url.endswith("/assignments"):
            return DummyResponse(200, assignments)
        if url.endswith("/api/activity/ai-summary"):
            return DummyResponse(200, {"summary": summary})
        if url.endswith("/api/activity/ai-chat"):
            return DummyResponse(200, {"reply": reply})
        if url.endswith("/api/add_activity"):
            return DummyResponse(201, dict(activity))
        if "/api/edit_activity/" in url:
            return DummyResponse(200, dict(activity))
        if "/api/delete_activity/" in url:
            return DummyResponse(200, {"message": "deleted"})
        raise AssertionError(f"unexpected backend_request call: {method} {url}")

    return fake_request


def make_recording_backend(calls, status_code, payload):
    """A fake `requests.request` for the MCP and RAG tabs: records each call's
    url, JSON body and headers in `calls`, and always returns one response."""
    def fake_request(method, url, params=None, json=None, timeout=None, headers=None):
        calls.append({"url": url, "json": json, "headers": headers})
        return DummyResponse(status_code, payload)
    return fake_request


@pytest.fixture
def frontend_app():
    return load_frontend_app()


@pytest.fixture
def frontend_client(frontend_app, monkeypatch):
    """Reachability-test client with a default happy-path backend mock
    already wired in — individual tests can re-monkeypatch
    frontend_app.requests.request for error-path cases."""
    monkeypatch.setattr(frontend_app.requests, "request", make_fake_backend())
    with frontend_app.create_app().test_client() as test_client:
        yield test_client
