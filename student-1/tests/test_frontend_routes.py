"""Offline unit tests for frontend/app.py.

No Docker required: loads frontend/app.py by path (there is no package),
monkeypatches its `requests` module with a fake that records every outbound
call and returns canned responses, then drives it with Flask's test client.

Covers: rendering the seeded list, frontend-side search/filter (the backend
only supports exact-match city/country/travel_style filters), the HTMX
partial-vs-full-page branch, the create/edit/delete flows including the
delete confirmation step, and that invalid numeric input never raises inside
the frontend (it is forwarded to the backend as a raw string so the
backend's own validation message is what the user sees).
"""
import importlib.util
import os
import sys

import requests

MODULE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "frontend", "app.py"
)


def _load_frontend_app():
    spec = importlib.util.spec_from_file_location("destination_frontend_app", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules["destination_frontend_app"] = module
    spec.loader.exec_module(module)
    return module


frontend_app = _load_frontend_app()
frontend_app.app.testing = True  # unhandled exceptions propagate instead of becoming a 500

SEED_DESTINATIONS = [
    {"destination_id": 1, "city": "Tokyo", "country": "Japan",
     "description": "Neon-lit metropolis mixing ultramodern tech with historic temples.",
     "average_daily_cost": 150.0, "recommended_trip_length": 6, "travel_style": "City break",
     "categories": ["food", "culture", "nightlife", "shopping"]},
    {"destination_id": 2, "city": "Kyoto", "country": "Japan",
     "description": "Former imperial capital known for temples, shrines and gardens.",
     "average_daily_cost": 130.0, "recommended_trip_length": 4, "travel_style": "Cultural",
     "categories": ["culture", "history", "nature"]},
    {"destination_id": 3, "city": "Osaka", "country": "Japan",
     "description": "Japan's kitchen, famous for street food and lively nightlife.",
     "average_daily_cost": 125.0, "recommended_trip_length": 3, "travel_style": "Food and nightlife",
     "categories": ["food", "nightlife"]},
    {"destination_id": 4, "city": "Seoul", "country": "South Korea",
     "description": "Fast-paced capital blending K-pop culture with ancient palaces.",
     "average_daily_cost": 120.0, "recommended_trip_length": 5, "travel_style": "City break",
     "categories": ["culture", "shopping", "nightlife"]},
    {"destination_id": 5, "city": "Bangkok", "country": "Thailand",
     "description": "Bustling capital known for street food, temples and river life.",
     "average_daily_cost": 70.0, "recommended_trip_length": 5, "travel_style": "Budget",
     "categories": ["food", "culture", "budget"]},
    {"destination_id": 6, "city": "Singapore", "country": "Singapore",
     "description": "Ultra-clean city-state famed for its skyline and hawker food.",
     "average_daily_cost": 180.0, "recommended_trip_length": 3, "travel_style": "City break",
     "categories": ["food", "shopping", "family"]},
    {"destination_id": 7, "city": "Sydney", "country": "Australia",
     "description": "Harbour city with iconic beaches and the famous Opera House.",
     "average_daily_cost": 190.0, "recommended_trip_length": 5, "travel_style": "Coastal",
     "categories": ["beach", "nature", "family"]},
    {"destination_id": 8, "city": "Melbourne", "country": "Australia",
     "description": "Laneways, coffee culture and a thriving arts scene.",
     "average_daily_cost": 165.0, "recommended_trip_length": 4, "travel_style": "Arts and coffee",
     "categories": ["food", "culture", "arts"]},
    {"destination_id": 9, "city": "Paris", "country": "France",
     "description": "The City of Light, home to world-class art, food and romance.",
     "average_daily_cost": 200.0, "recommended_trip_length": 5, "travel_style": "Romantic",
     "categories": ["culture", "food", "romance"]},
    {"destination_id": 10, "city": "Rome", "country": "Italy",
     "description": "Ancient ruins and Renaissance art layered through a living city.",
     "average_daily_cost": 155.0, "recommended_trip_length": 4, "travel_style": "Historical",
     "categories": ["history", "culture", "food"]},
]


class DummyResponse:
    def __init__(self, status_code, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload
        self.text = text
        self.headers = {"Content-Type": "application/json"} if payload is not None else {}

    def json(self):
        if self._payload is None:
            raise ValueError("response has no JSON body")
        return self._payload


def install_fake_backend(monkeypatch, router):
    """router(method, url, json_body, params) -> DummyResponse. Every call is
    appended to the returned list so tests can assert on outbound requests."""
    captured = []

    def fake_request(method, url, timeout=None, json=None, params=None, **kwargs):
        captured.append({"method": method, "url": url, "json": json, "params": params})
        return router(method, url, json, params)

    monkeypatch.setattr(frontend_app.requests, "request", fake_request)
    return captured


def list_router(*_args, **_kwargs):
    def router(method, url, json_body, params):
        assert method == "GET"
        assert url == f"{frontend_app.BACKEND_SERVICE_URL}/api/destinations"
        return DummyResponse(200, list(SEED_DESTINATIONS))

    return router


def client():
    return frontend_app.app.test_client()


# --- list / search / filter -------------------------------------------------

def test_index_renders_all_seeded_cities(monkeypatch):
    install_fake_backend(monkeypatch, list_router())
    response = client().get("/")
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    for destination in SEED_DESTINATIONS:
        assert destination["city"] in body


def test_search_is_case_insensitive(monkeypatch):
    install_fake_backend(monkeypatch, list_router())
    response = client().get("/?q=tokyo")
    body = response.get_data(as_text=True)
    assert "Tokyo" in body
    assert "Paris" not in body


def test_search_matches_description_not_just_city(monkeypatch):
    install_fake_backend(monkeypatch, list_router())
    response = client().get("/?q=renaissance")
    body = response.get_data(as_text=True)
    assert "Rome" in body
    assert "Tokyo" not in body


def test_country_filter_narrows_results_and_keeps_full_dropdown(monkeypatch):
    install_fake_backend(monkeypatch, list_router())
    response = client().get("/?country=Japan")
    body = response.get_data(as_text=True)
    assert "Tokyo" in body and "Kyoto" in body and "Osaka" in body
    assert "Paris" not in body
    # dropdown still offers every country, not just the ones currently shown
    assert '<option value="Australia"' in body
    assert '<option value="Japan" selected>' in body


def test_no_results_shows_empty_state_not_error(monkeypatch):
    install_fake_backend(monkeypatch, list_router())
    response = client().get("/?q=zzzznotarealplace")
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "No destinations match your search" in body
    assert "alert--error" not in body


def test_hx_request_returns_partial_fragment(monkeypatch):
    install_fake_backend(monkeypatch, list_router())
    response = client().get("/?q=Tokyo", headers={"HX-Request": "true"})
    body = response.get_data(as_text=True)
    assert "Tokyo" in body
    assert "<html" not in body


def test_plain_request_returns_full_page(monkeypatch):
    install_fake_backend(monkeypatch, list_router())
    response = client().get("/?q=Tokyo")
    body = response.get_data(as_text=True)
    assert "<html" in body


def test_backend_unreachable_returns_200_with_message_not_500(monkeypatch):
    def router(method, url, json_body, params):
        raise requests.exceptions.ConnectionError("connection refused")

    install_fake_backend(monkeypatch, router)
    response = client().get("/")
    assert response.status_code == 200
    assert frontend_app.UNAVAILABLE_MESSAGE in response.get_data(as_text=True)


# --- create ------------------------------------------------------------------

def test_create_destination_sends_correctly_typed_json(monkeypatch):
    def router(method, url, json_body, params):
        if method == "POST":
            assert url == f"{frontend_app.BACKEND_SERVICE_URL}/api/destinations"
            assert isinstance(json_body["categories"], list)
            assert json_body["categories"] == ["food", "beach"]
            assert isinstance(json_body["average_daily_cost"], float)
            assert json_body["average_daily_cost"] == 99.5
            assert isinstance(json_body["recommended_trip_length"], int)
            assert json_body["recommended_trip_length"] == 3
            return DummyResponse(201, {**SEED_DESTINATIONS[0], "destination_id": 42, "city": "Demo City"})
        return list_router()(method, url, json_body, params)

    install_fake_backend(monkeypatch, router)
    response = client().post(
        "/destinations/new",
        data={
            "city": "Demo City", "country": "Australia", "description": "Test",
            "average_daily_cost": "99.5", "recommended_trip_length": "3",
            "travel_style": "City break", "categories": "food, beach",
        },
    )
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/destinations/42")


def test_create_blank_numeric_fields_are_omitted(monkeypatch):
    def router(method, url, json_body, params):
        if method == "POST":
            assert "average_daily_cost" not in json_body
            assert "recommended_trip_length" not in json_body
            return DummyResponse(201, {**SEED_DESTINATIONS[0], "destination_id": 43})
        return list_router()(method, url, json_body, params)

    install_fake_backend(monkeypatch, router)
    response = client().post(
        "/destinations/new",
        data={"city": "Demo City", "country": "Australia", "average_daily_cost": "", "recommended_trip_length": ""},
    )
    assert response.status_code == 302


def test_create_invalid_numeric_is_forwarded_as_raw_string_no_exception(monkeypatch):
    """A frontend float()/int() call must never raise. Invalid input is
    forwarded verbatim so the backend's own 400 message is what surfaces."""

    def router(method, url, json_body, params):
        if method == "POST":
            assert json_body["average_daily_cost"] == "not-a-number"
            return DummyResponse(400, {"error": "average_daily_cost must be a number"})
        return list_router()(method, url, json_body, params)

    install_fake_backend(monkeypatch, router)
    response = client().post(
        "/destinations/new",
        data={"city": "Demo City", "country": "Australia", "average_daily_cost": "not-a-number"},
    )
    assert response.status_code == 422
    assert "average_daily_cost must be a number" in response.get_data(as_text=True)


def test_create_invalid_trip_length_forwarded_as_raw_string(monkeypatch):
    def router(method, url, json_body, params):
        if method == "POST":
            assert json_body["recommended_trip_length"] == "soon"
            return DummyResponse(400, {"error": "recommended_trip_length must be an integer"})
        return list_router()(method, url, json_body, params)

    install_fake_backend(monkeypatch, router)
    response = client().post(
        "/destinations/new",
        data={"city": "Demo City", "country": "Australia", "recommended_trip_length": "soon"},
    )
    assert response.status_code == 422
    assert "recommended_trip_length must be an integer" in response.get_data(as_text=True)


def test_create_validation_error_rerenders_form_and_preserves_input(monkeypatch):
    def router(method, url, json_body, params):
        if method == "POST":
            return DummyResponse(400, {"error": "missing required fields: country"})
        return list_router()(method, url, json_body, params)

    install_fake_backend(monkeypatch, router)
    response = client().post("/destinations/new", data={"city": "Demo City", "country": ""})
    assert response.status_code == 422
    body = response.get_data(as_text=True)
    assert "missing required fields: country" in body
    assert 'value="Demo City"' in body


# --- delete --------------------------------------------------------------

def test_delete_get_shows_confirmation_and_issues_no_delete(monkeypatch):
    def router(method, url, json_body, params):
        assert method == "GET"
        return DummyResponse(200, SEED_DESTINATIONS[0])

    captured = install_fake_backend(monkeypatch, router)
    response = client().get("/destinations/1/delete")
    assert response.status_code == 200
    assert "Delete this destination" in response.get_data(as_text=True)
    assert all(call["method"] != "DELETE" for call in captured)


def test_delete_post_issues_delete_then_redirects(monkeypatch):
    def router(method, url, json_body, params):
        if method == "DELETE":
            assert url == f"{frontend_app.BACKEND_SERVICE_URL}/api/destinations/1"
            return DummyResponse(204)
        return DummyResponse(200, SEED_DESTINATIONS[0])

    captured = install_fake_backend(monkeypatch, router)
    response = client().post("/destinations/1/delete")
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/")
    assert any(call["method"] == "DELETE" for call in captured)


# --- detail / not found ---------------------------------------------------

def test_detail_missing_destination_returns_404_not_traceback(monkeypatch):
    def router(method, url, json_body, params):
        return DummyResponse(404, {"error": "destination not found"})

    install_fake_backend(monkeypatch, router)
    response = client().get("/destinations/999999")
    assert response.status_code == 404
    assert "Destination not found" in response.get_data(as_text=True)


# --- edit ------------------------------------------------------------------

def test_edit_get_prefills_form_with_existing_values(monkeypatch):
    def router(method, url, json_body, params):
        assert method == "GET"
        return DummyResponse(200, SEED_DESTINATIONS[0])

    install_fake_backend(monkeypatch, router)
    response = client().get("/destinations/1/edit")
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert 'value="Tokyo"' in body


def test_edit_post_sends_partial_update_then_redirects_to_detail(monkeypatch):
    def router(method, url, json_body, params):
        if method == "PUT":
            assert url == f"{frontend_app.BACKEND_SERVICE_URL}/api/destinations/1"
            assert json_body["average_daily_cost"] == 275.0
            return DummyResponse(200, {**SEED_DESTINATIONS[0], "average_daily_cost": 275.0})
        return DummyResponse(200, SEED_DESTINATIONS[0])

    install_fake_backend(monkeypatch, router)
    response = client().post("/destinations/1/edit", data={"average_daily_cost": "275"})
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/destinations/1")


def test_edit_post_validation_error_rerenders_form_with_message(monkeypatch):
    def router(method, url, json_body, params):
        if method == "PUT":
            return DummyResponse(400, {"error": "average_daily_cost must be a number"})
        return DummyResponse(200, SEED_DESTINATIONS[0])

    install_fake_backend(monkeypatch, router)
    response = client().post("/destinations/1/edit", data={"average_daily_cost": "not-a-number"})
    assert response.status_code == 422
    assert "average_daily_cost must be a number" in response.get_data(as_text=True)
