import requests

from app import create_app
from services import database_api


class DummyResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


def test_list_accommodations_forwards_query_params(monkeypatch):
    captured = {}

    def fake_request(method, url, timeout, **kwargs):
        captured["method"] = method
        captured["url"] = url
        captured["timeout"] = timeout
        captured["params"] = kwargs.get("params")
        return DummyResponse(200, {"total": 1, "data": [{"id": 1, "name": "Ocean View"}]})

    monkeypatch.setattr(database_api.requests, "request", fake_request)
    database_api.DATABASE_SERVICE_URL = "http://accommodation-db:6002"

    app = create_app()
    with app.test_client() as client:
        response = client.get("/accommodations?q=beach&min_price=80&limit=10")

    assert response.status_code == 200
    assert response.get_json() == {"total": 1, "data": [{"id": 1, "name": "Ocean View"}]}
    assert captured["url"] == "http://accommodation-db:6002/accommodations"
    assert captured["params"] == {"q": "beach", "min_price": "80", "limit": "10"}


def test_get_accommodation_propagates_not_found(monkeypatch):
    def fake_request(method, url, timeout, **kwargs):
        return DummyResponse(404, {"error": "Accommodation not found"})

    monkeypatch.setattr(database_api.requests, "request", fake_request)
    database_api.DATABASE_SERVICE_URL = "http://accommodation-db:6002"

    app = create_app()
    with app.test_client() as client:
        response = client.get("/accommodations/999")

    assert response.status_code == 404
    assert response.get_json() == {"error": "Accommodation not found"}


def test_backend_returns_502_when_database_is_unavailable(monkeypatch):
    def fake_request(method, url, timeout, **kwargs):
        raise requests.exceptions.ConnectionError("database down")

    monkeypatch.setattr(database_api.requests, "request", fake_request)
    database_api.DATABASE_SERVICE_URL = "http://accommodation-db:6002"

    app = create_app()
    with app.test_client() as client:
        response = client.get("/health")

    assert response.status_code == 502
    assert response.get_json() == {"error": "Accommodation database is unavailable."}
