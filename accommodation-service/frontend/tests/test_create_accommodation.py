from app import create_app
import requests


class DummyResponse:
    status_code = 201


def test_create_accommodation_normalizes_type(monkeypatch):
    captured = {}

    def fake_request(method, url, params=None, json=None, timeout=None):
        captured["method"] = method
        captured["url"] = url
        captured["json"] = json
        return DummyResponse()

    monkeypatch.setattr("app.requests.request", fake_request)

    app = create_app()
    with app.test_client() as client:
        response = client.post(
            "/accommodations",
            data={
                "name": "UTS Hotel",
                "destination_id": "dest-tokyo",
                "destination_city": "Tokyo",
                "type": "Hotel",
                "price_per_night": "500.0",
            },
        )

    assert response.status_code == 302
    assert captured["method"] == "POST"
    assert captured["json"]["type"] == "hotel"


def test_new_form_falls_back_to_text_fields_when_destinations_are_unavailable(monkeypatch):
    def unavailable_request(method, url, params=None, json=None, timeout=None):
        if url.endswith("/destinations"):
            raise requests.ConnectionError("destination service down")
        response = DummyResponse()
        response.status_code = 200
        return response

    monkeypatch.setattr("app.requests.request", unavailable_request)

    app = create_app()
    with app.test_client() as client:
        response = client.get("/accommodations/new")

    assert response.status_code == 200
    assert b'name="destination_city"' in response.data
    assert b'<select name="destination_city">' not in response.data