import os
import sys

import pytest

FRONTEND_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if FRONTEND_ROOT not in sys.path:
    sys.path.insert(0, FRONTEND_ROOT)


class DummyResponse:
    """Minimal stand-in for requests.Response — headers included since
    app.py checks Content-Type before calling .json() on error bodies."""

    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload
        self.headers = {"Content-Type": "application/json"}

    def json(self):
        return self._payload


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


@pytest.fixture
def app_module():
    import app as flask_app_module
    return flask_app_module


@pytest.fixture
def client(app_module, monkeypatch):
    """Reachability-test client with a default happy-path backend mock
    already wired in — individual tests can re-monkeypatch
    app_module.requests.request for error-path cases."""
    monkeypatch.setattr(app_module.requests, "request", make_fake_backend())
    flask_app = app_module.create_app()
    with flask_app.test_client() as test_client:
        yield test_client
