"""Lightweight reachability tests for activity-service/frontend, per the
agreed minimal scope: each page/route returns 200 against a mocked backend,
plus a couple of "doesn't crash, degrades gracefully" checks for the error
path — full interaction/HTMX-swap testing is explicitly out of scope here.

Run with the rest of the suite, from activity-service/: python -m pytest
"""
import pytest

from conftest import SAMPLE_ACTIVITY, DummyResponse, make_fake_backend


# This file's tests use `app_module` / `client` for the frontend tier.
@pytest.fixture
def app_module(frontend_app):
    return frontend_app


@pytest.fixture
def client(frontend_client):
    return frontend_client


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


# --- Search / filter on the home and list pages ---

def _filter_client(app_module, monkeypatch, calls, filtered_status=200):
    """Backend fake for /api/view_activities that records each call's params:
    the unfiltered call (Type dropdown) returns two types, a filtered call
    returns one dated activity, or an error when filtered_status says so."""
    all_activities = [SAMPLE_ACTIVITY, {**SAMPLE_ACTIVITY, "activity_id": 2,
                                        "activity_name": "Food Walk", "activity_type": "Food"}]

    def fake_request(method, url, params=None, json=None, timeout=None):
        assert url.endswith("/api/view_activities"), url
        calls.append(params or {})
        if not params:
            return DummyResponse(200, all_activities)
        if filtered_status != 200:
            return DummyResponse(filtered_status, {"error": "min_cost must be a number"})
        return DummyResponse(200, [{**SAMPLE_ACTIVITY, "assignment_time": "2026-10-02 09:00"}])

    monkeypatch.setattr(app_module.requests, "request", fake_request)
    return app_module.create_app().test_client()


def test_list_forwards_filters_and_lists_all_types(app_module, monkeypatch):
    calls = []
    client = _filter_client(app_module, monkeypatch, calls)
    resp = client.get("/activities?type=Adventure&max_cost=50&date_from=2026-10-01&q=")
    assert resp.status_code == 200
    assert calls[0] == {"type": "Adventure", "max_cost": "50", "date_from": "2026-10-01", "include_dates": "1"}
    assert calls[1] == {}  # unfiltered call for the Type dropdown
    assert b'<option value="Food"' in resp.data
    assert b"2026-10-02 09:00" in resp.data
    assert b'class="filter-form"' in resp.data


def test_filter_htmx_request_returns_results_fragment_only(app_module, monkeypatch):
    calls = []
    client = _filter_client(app_module, monkeypatch, calls)
    resp = client.get("/?type=Adventure", headers={"HX-Request": "true"})
    assert resp.status_code == 200
    assert b"<html" not in resp.data and b"filter-form" not in resp.data
    assert b"Kayaking" in resp.data
    assert len(calls) == 1


def test_home_has_no_edit_or_delete_buttons(client):
    resp = client.get("/")
    assert b"hx-delete" not in resp.data
    assert b"Kayaking" in resp.data


def test_filter_validation_error_is_shown(app_module, monkeypatch):
    client = _filter_client(app_module, monkeypatch, [], filtered_status=400)
    resp = client.get("/activities?min_cost=cheap", headers={"HX-Request": "true"})
    assert resp.status_code == 200
    assert b"Could not load activities: min_cost must be a number" in resp.data
