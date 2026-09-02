"""Offline unit tests for frontend/app.py's /compare route.

No Docker required: loads frontend/app.py by path and monkeypatches its
`requests` module with a fake that records every outbound call and returns
canned responses, then drives it with Flask's test client. Covers both the
HTMX partial swap and the plain (no-JS) full-page POST path.
"""
import importlib.util
import os
import sys

MODULE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "frontend", "app.py"
)


def _load_frontend_app():
    spec = importlib.util.spec_from_file_location("destination_frontend_compare_app", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules["destination_frontend_compare_app"] = module
    spec.loader.exec_module(module)
    return module


frontend_app = _load_frontend_app()
frontend_app.app.testing = True

SEED_DESTINATIONS = [
    {"destination_id": 1, "city": "Tokyo", "country": "Japan",
     "description": "Neon-lit metropolis mixing ultramodern tech with historic temples.",
     "average_daily_cost": 150.0, "recommended_trip_length": 6, "travel_style": "City break",
     "categories": ["food", "culture", "nightlife", "shopping"]},
    {"destination_id": 2, "city": "Kyoto", "country": "Japan",
     "description": "Former imperial capital known for temples, shrines and gardens.",
     "average_daily_cost": 130.0, "recommended_trip_length": 4, "travel_style": "Cultural",
     "categories": ["culture", "history", "nature"]},
]

COMPARISON_RESULT = {
    "comparison": "Tokyo is lively with nightlife and food. Kyoto is calmer and cultural. "
                  "Recommendation: Tokyo.",
    "preferences": "nightlife and food",
    "model": "qwen2.5:0.5b",
    "elapsed_seconds": 1.23,
    "destinations": SEED_DESTINATIONS,
}


class DummyResponse:
    def __init__(self, status_code, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self):
        if self._payload is None:
            raise ValueError("response has no JSON body")
        return self._payload


def install_fake_backend(monkeypatch, router):
    captured = []

    def fake_request(method, url, timeout=None, json=None, params=None, **kwargs):
        captured.append({"method": method, "url": url, "json": json, "params": params})
        return router(method, url, json, params)

    monkeypatch.setattr(frontend_app.requests, "request", fake_request)
    return captured


def _success_router(method, url, json_body, params):
    if method == "GET" and url.endswith("/api/destinations"):
        return DummyResponse(200, SEED_DESTINATIONS)
    if method == "POST" and url.endswith("/api/destinations/ai-compare"):
        return DummyResponse(200, COMPARISON_RESULT)
    raise AssertionError(f"unexpected request: {method} {url}")


def _ai_unavailable_router(method, url, json_body, params):
    if method == "GET" and url.endswith("/api/destinations"):
        return DummyResponse(200, SEED_DESTINATIONS)
    if method == "POST" and url.endswith("/api/destinations/ai-compare"):
        return DummyResponse(502, {"error": "AI comparison service is unavailable."}, text="")
    raise AssertionError(f"unexpected request: {method} {url}")


def test_compare_get_populates_dropdowns_from_backend(monkeypatch):
    install_fake_backend(monkeypatch, _success_router)

    client = frontend_app.app.test_client()
    response = client.get("/compare")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "Tokyo" in body
    assert "Kyoto" in body
    assert '<select name="city_a"' in body
    assert '<select name="city_b"' in body


def test_compare_post_htmx_returns_partial_with_comparison(monkeypatch):
    install_fake_backend(monkeypatch, _success_router)

    client = frontend_app.app.test_client()
    response = client.post(
        "/compare",
        data={"city_a": "Tokyo", "city_b": "Kyoto", "preferences": "nightlife and food"},
        headers={"HX-Request": "true"},
    )

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "Tokyo is lively" in body
    assert "qwen2.5:0.5b" in body
    # A partial swap target, not a full page.
    assert "<html" not in body.lower()


def test_compare_post_plain_renders_full_page_with_dropdowns_and_result(monkeypatch):
    """The non-HTMX (no-JS) POST path must repopulate the dropdowns and echo
    the submitted selection, not come back with an empty form."""
    install_fake_backend(monkeypatch, _success_router)

    client = frontend_app.app.test_client()
    response = client.post(
        "/compare",
        data={"city_a": "Tokyo", "city_b": "Kyoto", "preferences": "nightlife and food"},
    )

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "<html" in body.lower()
    assert "Tokyo is lively" in body
    # Dropdowns are repopulated from the backend and the submitted cities
    # remain selected.
    assert '<option value="Tokyo" selected>' in body
    assert '<option value="Kyoto" selected>' in body


def test_compare_post_htmx_shows_readable_error_when_ai_unavailable(monkeypatch):
    install_fake_backend(monkeypatch, _ai_unavailable_router)

    client = frontend_app.app.test_client()
    response = client.post(
        "/compare",
        data={"city_a": "Tokyo", "city_b": "Kyoto", "preferences": "nightlife and food"},
        headers={"HX-Request": "true"},
    )

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "alert--error" in body
    assert "Destination service is unavailable" in body


def test_compare_post_plain_shows_readable_error_and_dropdowns_when_ai_unavailable(monkeypatch):
    """Same failure, but through the plain (no-JS) full-page path — the form
    must still render usably, not blank or 500."""
    install_fake_backend(monkeypatch, _ai_unavailable_router)

    client = frontend_app.app.test_client()
    response = client.post(
        "/compare",
        data={"city_a": "Tokyo", "city_b": "Kyoto", "preferences": "nightlife and food"},
    )

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "<html" in body.lower()
    assert "alert--error" in body
    assert '<select name="city_a"' in body
    assert "Tokyo" in body and "Kyoto" in body
