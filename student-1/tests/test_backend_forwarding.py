"""Offline unit tests for backend/app.py's forwarding and error mapping to
the database service.

No Docker required: loads backend/app.py by path and monkeypatches its
`requests.request` call (same pattern as test_ai_compare.py) with a fake
that records every outbound call and returns canned responses.

Kept to the highest-value distinct mappings — one test per branch in
_request()/the route handlers that isn't already proven by
test_service_failure.py (the two 503-on-unreachable-database cases) or by
test_destinations_api.py (the full CRUD lifecycle against a live stack).
"""
import importlib.util
import os
import sys

MODULE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend", "app.py"
)
BACKEND_DIR = os.path.dirname(MODULE_PATH)
if BACKEND_DIR not in sys.path:
    # backend/app.py imports llm_client (a sibling module, not a package);
    # loading app.py by path doesn't add its own directory to sys.path the
    # way running it as __main__ would, so this needs to be explicit.
    sys.path.insert(0, BACKEND_DIR)


def _load_backend_app():
    spec = importlib.util.spec_from_file_location("destination_backend_forwarding_app", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules["destination_backend_forwarding_app"] = module
    spec.loader.exec_module(module)
    return module


backend_app = _load_backend_app()


class DummyResponse:
    def __init__(self, status_code, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self):
        if self._payload is None:
            raise ValueError("response has no JSON body")
        return self._payload


def install_fake_database(monkeypatch, router):
    """router(method, url, json_body, params) -> DummyResponse. Every call is
    appended to the returned list so tests can assert on outbound requests."""
    calls = []

    def fake_request(method, url, timeout=None, json=None, params=None, **kwargs):
        calls.append({"method": method, "url": url, "json": json, "params": params})
        return router(method, url, json, params)

    monkeypatch.setattr(backend_app.requests, "request", fake_request)
    return calls


def client():
    return backend_app.app.test_client()


def test_only_registered_list_params_are_forwarded(monkeypatch):
    def router(method, url, json_body, params):
        return DummyResponse(200, [])

    calls = install_fake_database(monkeypatch, router)
    client().get("/api/destinations?city=Tokyo&bogus=ignored")

    assert calls[0]["url"] == f"{backend_app.DATABASE_SERVICE_URL}/destinations"
    assert calls[0]["params"] == {"city": "Tokyo"}


def test_get_by_id_returns_database_payload_unchanged(monkeypatch):
    payload = {"destination_id": 1, "city": "Tokyo"}
    install_fake_database(monkeypatch, lambda method, url, j, p: DummyResponse(200, payload))

    response = client().get("/api/destinations/1")
    assert response.status_code == 200
    assert response.get_json() == payload


def test_get_by_id_database_404_maps_to_404(monkeypatch):
    install_fake_database(
        monkeypatch, lambda method, url, j, p: DummyResponse(404, {"error": "destination not found"})
    )

    response = client().get("/api/destinations/999999")
    assert response.status_code == 404
    assert response.get_json()["error"] == "destination not found"


def test_create_forwards_body_verbatim_and_returns_201(monkeypatch):
    body = {"city": "Demo City", "country": "Australia"}

    def router(method, url, json_body, params):
        assert method == "POST"
        assert json_body == body
        return DummyResponse(201, {**body, "destination_id": 42})

    install_fake_database(monkeypatch, router)
    response = client().post("/api/destinations", json=body)
    assert response.status_code == 201
    assert response.get_json()["destination_id"] == 42


def test_create_database_400_maps_to_400_preserving_message(monkeypatch):
    install_fake_database(
        monkeypatch,
        lambda method, url, j, p: DummyResponse(400, {"error": "missing required fields: city"}),
    )

    response = client().post("/api/destinations", json={"country": "Australia"})
    assert response.status_code == 400
    assert response.get_json()["error"] == "missing required fields: city"


def test_delete_returns_204_with_empty_body(monkeypatch):
    install_fake_database(monkeypatch, lambda method, url, j, p: DummyResponse(204))

    response = client().delete("/api/destinations/1")
    assert response.status_code == 204
    assert response.get_data() == b""


def test_database_500_maps_to_503_not_passed_through(monkeypatch):
    """The database returning a raw 500 must never leak to the client as a
    500 — it is folded into the same 'unavailable' contract as a connection
    failure."""
    install_fake_database(monkeypatch, lambda method, url, j, p: DummyResponse(500, text="boom"))

    response = client().get("/api/destinations")
    assert response.status_code == 503
    assert response.get_json()["error"] == "destination database unavailable"


def test_health_returns_502_when_database_unreachable(monkeypatch):
    """Deliberately different from the CRUD routes' 503: /health surfaces an
    unreachable database as 502, per backend/app.py's health handler."""
    import requests as real_requests

    def fake_request(method, url, timeout=None, **kwargs):
        raise real_requests.exceptions.ConnectionError("connection refused")

    monkeypatch.setattr(backend_app.requests, "request", fake_request)

    response = client().get("/health")
    assert response.status_code == 502
    assert response.get_json()["database"]["status"] == "unreachable"
