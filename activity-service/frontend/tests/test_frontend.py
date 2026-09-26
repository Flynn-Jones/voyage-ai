"""Lightweight reachability tests for activity-service/frontend, per the
agreed minimal scope: each page/route returns 200 against a mocked backend,
plus a couple of "doesn't crash, degrades gracefully" checks for the error
path — full interaction/HTMX-swap testing is explicitly out of scope here.

Run in its own pytest invocation, separately from activity-service/backend's
tests: both services have a top-level `app` module, and running them in one
combined `pytest` command would let Python's module cache serve the first
one's `app` to the second, silently testing the wrong app. Run as:
    python -m pytest activity-service/frontend/tests
"""
from conftest import DummyResponse, make_fake_backend


def test_health_200(client):
    resp = client.get("/health")
    assert resp.status_code == 200


def test_hub_200(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert b"Kayaking" in resp.data


def test_activities_list_200(client):
    resp = client.get("/activities")
    assert resp.status_code == 200
    assert b"Kayaking" in resp.data


def test_activity_detail_200(client):
    resp = client.get("/activities/1")
    assert resp.status_code == 200
    assert b"Kayaking" in resp.data


def test_activity_summary_fragment_200(client):
    resp = client.get("/activities/1/summary")
    assert resp.status_code == 200
    assert b"fun kayaking trip" in resp.data


def test_register_form_200(client):
    resp = client.get("/activities/register")
    assert resp.status_code == 200
    assert b"Register Activity" in resp.data or b"Register a New Activity" in resp.data


def test_edit_form_200(client):
    resp = client.get("/activities/1/edit")
    assert resp.status_code == 200
    assert b"Kayaking" in resp.data


def test_ai_mode_200(client):
    resp = client.get("/ai-mode")
    assert resp.status_code == 200


# --- Reachability under backend failure: should degrade, not crash ---

def test_hub_degrades_gracefully_when_backend_unreachable(app_module, monkeypatch):
    def boom(method, url, params=None, json=None, timeout=None):
        raise app_module.requests.exceptions.RequestException("connection refused")

    monkeypatch.setattr(app_module.requests, "request", boom)
    flask_app = app_module.create_app()
    with flask_app.test_client() as test_client:
        resp = test_client.get("/")
        assert resp.status_code == 200  # renders the error-state UI, not a 500
        assert b"Could not load activities" in resp.data


def test_activity_detail_404_when_activity_missing(app_module, monkeypatch):
    def fake_request(method, url, params=None, json=None, timeout=None):
        if "/api/view_activity/" in url:
            return DummyResponse(404, {"error": "activity not found"})
        raise AssertionError(f"unexpected call: {method} {url}")

    monkeypatch.setattr(app_module.requests, "request", fake_request)
    flask_app = app_module.create_app()
    with flask_app.test_client() as test_client:
        resp = test_client.get("/activities/999")
        assert resp.status_code == 404


def test_activity_summary_fragment_shows_error_not_crash(app_module, monkeypatch):
    def fake_request(method, url, params=None, json=None, timeout=None):
        if url.endswith("/api/activity/ai-summary"):
            return DummyResponse(502, {"error": "AI summary unavailable: ollama down"})
        raise AssertionError(f"unexpected call: {method} {url}")

    monkeypatch.setattr(app_module.requests, "request", fake_request)
    flask_app = app_module.create_app()
    with flask_app.test_client() as test_client:
        resp = test_client.get("/activities/1/summary")
        assert resp.status_code == 200  # fragment still renders — with an error message
        assert b"Could not generate a summary" in resp.data
