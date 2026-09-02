import pytest

from app import create_app
from services import database_api


class DummyResponse:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text

    def json(self):
        return self._payload


def test_ai_health_reports_unreachable(monkeypatch):
    def fake_get(url, timeout):
        raise OSError("down")

    monkeypatch.setattr("services.llm_client.requests.get", fake_get)

    app = create_app()
    with app.test_client() as client:
        response = client.get("/ai/health")

    assert response.status_code == 200
    assert response.get_json()["ollama"] == "unreachable"
    assert response.get_json()["loaded"] is False


def test_ai_recommend_uses_db_forwarding_and_llm(monkeypatch):
    def fake_list_accommodations(params):
        assert params == {"destination_city": "Tokyo", "max_price": "150.0"}
        return [
            {"id": 1, "name": "Hotel A", "destination_city": "Tokyo", "description": "Nightlife close to Shinjuku", "amenities": ["wifi", "bar"]},
            {"id": 2, "name": "Hotel B", "destination_city": "Tokyo", "description": "Quiet family hotel", "amenities": ["pool"]},
        ]

    def fake_generate(prompt, model=None, timeout=None):
        assert "Hotel A" in prompt
        return {"recommendation": {"id": 1, "name": "Hotel A", "reason": "Best fit for nightlife and price."}}

    monkeypatch.setattr(database_api, "list_accommodations", fake_list_accommodations)
    monkeypatch.setattr("services.llm_client.generate_recommendation", fake_generate)

    app = create_app()
    with app.test_client() as client:
        response = client.post(
            "/ai/recommend",
            json={"destination_city": "Tokyo", "max_price": 150.0, "interests": ["nightlife"]},
        )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["plan"]
    assert payload["act_result_count"] == 2
    assert payload["recommendation"]["id"] == 1
    assert "nightlife" in payload["recommendation"]["reason"].lower()


def test_ai_recommend_uses_llm_ranking_before_final_selection(monkeypatch):
    def fake_list_accommodations(params):
        return [
            {"id": 1, "name": "Hotel A", "destination_city": "Tokyo", "description": "Nightlife close to Shinjuku", "amenities": ["wifi", "bar"]},
            {"id": 2, "name": "Hotel B", "destination_city": "Tokyo", "description": "Quiet family hotel", "amenities": ["pool"]},
        ]

    seen = []

    def fake_generate(prompt, model=None, timeout=None):
        seen.append(prompt)
        if "Rank the accommodation candidates" in prompt:
            return {"response": '[{"id": 2, "name": "Hotel B", "score": 0.99}, {"id": 1, "name": "Hotel A", "score": 0.72}]'}
        return {"recommendation": {"id": 2, "name": "Hotel B", "reason": "Best fit for a calm but well-located stay."}}

    monkeypatch.setattr(database_api, "list_accommodations", fake_list_accommodations)
    monkeypatch.setattr("services.llm_client.generate_recommendation", fake_generate)

    app = create_app()
    with app.test_client() as client:
        response = client.post(
            "/ai/recommend",
            json={"destination_city": "Tokyo", "max_price": 150.0, "interests": ["nightlife"]},
        )

    assert response.status_code == 200
    assert any("Rank the accommodation candidates" in prompt for prompt in seen)
    assert response.get_json()["recommendation"]["id"] == 2


def test_ai_recommend_supports_multiple_ranked_options(monkeypatch):
    def fake_list_accommodations(params):
        return [
            {"id": 1, "name": "Hotel A", "destination_city": "Tokyo", "description": "Nightlife close to Shinjuku", "amenities": ["wifi", "bar"]},
            {"id": 2, "name": "Hotel B", "destination_city": "Tokyo", "description": "Quiet family hotel", "amenities": ["pool"]},
        ]

    def fake_generate(prompt, model=None, timeout=None):
        if "Rank the accommodation candidates" in prompt:
            return {"response": '[{"id": 2, "name": "Hotel B", "score": 0.96}, {"id": 1, "name": "Hotel A", "score": 0.89}]'}
        return {"response": '[{"id": 2, "name": "Hotel B", "reason": "Best fit for a calm but well-located stay."}, {"id": 1, "name": "Hotel A", "reason": "Strong nightlife access within budget."}]'}

    monkeypatch.setattr(database_api, "list_accommodations", fake_list_accommodations)
    monkeypatch.setattr("services.llm_client.generate_recommendation", fake_generate)

    app = create_app()
    with app.test_client() as client:
        response = client.post(
            "/ai/recommend",
            json={"destination_city": "Tokyo", "max_price": 150.0, "interests": ["nightlife"]},
        )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["recommendation"]["id"] == 2
    assert len(payload["recommendations"]) == 2
    assert payload["recommendations"][0]["name"] == "Hotel B"


def test_ai_recommend_parses_consecutive_json_objects(monkeypatch):
    def fake_list_accommodations(params):
        return [{"id": 1, "name": "Hotel A"}, {"id": 2, "name": "Hotel B"}, {"id": 3, "name": "Hotel C"}]

    def fake_generate(prompt, model=None, timeout=None):
        if "Rank the accommodation candidates" in prompt:
            return {"response": '[{"id": 1, "name": "Hotel A", "score": 0.9}, {"id": 2, "name": "Hotel B", "score": 0.8}, {"id": 3, "name": "Hotel C", "score": 0.7}]'}
        return {"response": '{"id": 1, "name": "Hotel A", "reason": "Best match."} {"id": 2, "name": "Hotel B", "reason": "Second-best match."} {"id": 3, "name": "Hotel C", "reason": "Third-best match."}'}

    monkeypatch.setattr(database_api, "list_accommodations", fake_list_accommodations)
    monkeypatch.setattr("services.llm_client.generate_recommendation", fake_generate)

    app = create_app()
    with app.test_client() as client:
        response = client.post("/ai/recommend", json={"destination_city": "Tokyo", "interests": ["nightlife"]})

    assert response.status_code == 200
    assert len(response.get_json()["recommendations"]) == 3
