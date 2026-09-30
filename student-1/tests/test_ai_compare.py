"""Offline unit tests for backend/app.py's POST /api/destinations/ai-compare.

No Docker and no live Ollama required: loads backend/app.py by path (there
is no package) and monkeypatches its `requests` module (for the database
call) and llm_client's `requests` module (for the Ollama call) with fakes
that record every outbound call and return canned responses.
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
    spec = importlib.util.spec_from_file_location("destination_backend_ai_app", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules["destination_backend_ai_app"] = module
    spec.loader.exec_module(module)
    return module


backend_app = _load_backend_app()

SEED_ROWS = [
    {
        "destination_id": 1, "city": "Tokyo", "country": "Japan",
        "description": "Neon-lit metropolis mixing ultramodern tech with historic temples.",
        "average_daily_cost": 150.0, "recommended_trip_length": 6,
        "travel_style": "City break", "categories": ["food", "culture", "nightlife", "shopping"],
    },
    {
        "destination_id": 2, "city": "Kyoto", "country": "Japan",
        "description": "Former imperial capital known for temples, shrines and gardens.",
        "average_daily_cost": 130.0, "recommended_trip_length": 4,
        "travel_style": "Cultural", "categories": ["culture", "history", "nature"],
    },
]


class DummyResponse:
    def __init__(self, status_code, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self):
        if self._payload is None:
            raise ValueError("response has no JSON body")
        return self._payload


def install_fake_database(monkeypatch, rows=SEED_ROWS):
    """Fakes the backend's requests.request(...) call to the database
    service. GET /destinations returns `rows`; everything else 404s."""
    calls = []

    def fake_request(method, url, timeout=None, json=None, params=None, **kwargs):
        calls.append({"method": method, "url": url})
        if method == "GET" and url.endswith("/destinations"):
            return DummyResponse(200, list(rows))
        return DummyResponse(404, {"error": "not found"})

    monkeypatch.setattr(backend_app.requests, "request", fake_request)
    return calls


def install_fake_ollama(monkeypatch, content="Tokyo is lively. Kyoto is calm. Recommendation: Tokyo."):
    """Fakes llm_client's requests.post(...) call to Ollama's /api/chat."""
    calls = []

    def fake_post(url, json=None, timeout=None, **kwargs):
        calls.append({"url": url, "json": json, "timeout": timeout})
        return DummyResponse(200, {"message": {"content": content}})

    monkeypatch.setattr(backend_app.llm_client.requests, "post", fake_post)
    return calls


def install_unreachable_ollama(monkeypatch):
    import requests as real_requests

    def fake_post(url, json=None, timeout=None, **kwargs):
        raise real_requests.exceptions.ConnectionError("Connection refused")

    monkeypatch.setattr(backend_app.llm_client.requests, "post", fake_post)


def install_unreachable_database(monkeypatch):
    import requests as real_requests

    def fake_request(method, url, timeout=None, **kwargs):
        raise real_requests.exceptions.ConnectionError("Connection refused")

    monkeypatch.setattr(backend_app.requests, "request", fake_request)


def test_ai_compare_success_calls_ollama_exactly_once(monkeypatch):
    install_fake_database(monkeypatch)
    ollama_calls = install_fake_ollama(monkeypatch)

    client = backend_app.app.test_client()
    response = client.post(
        "/api/destinations/ai-compare",
        json={"city_a": "Tokyo", "city_b": "Kyoto", "preferences": "nightlife and food"},
    )

    assert response.status_code == 200
    body = response.get_json()
    assert body["comparison"]
    assert body["model"] == backend_app.llm_client.OLLAMA_MODEL
    assert [d["city"] for d in body["destinations"]] == ["Tokyo", "Kyoto"]
    assert len(ollama_calls) == 1


def test_ai_compare_prompt_is_grounded_in_retrieved_data(monkeypatch):
    install_fake_database(monkeypatch)
    ollama_calls = install_fake_ollama(monkeypatch)

    client = backend_app.app.test_client()
    client.post(
        "/api/destinations/ai-compare",
        json={"city_a": "Tokyo", "city_b": "Kyoto", "preferences": "nightlife and food"},
    )

    sent_messages = ollama_calls[0]["json"]["messages"]
    full_prompt = " ".join(m["content"] for m in sent_messages)
    assert "150.0" in full_prompt or "150.00" in full_prompt
    assert "history" in full_prompt  # a Kyoto category
    assert "nightlife and food" in full_prompt


def test_ai_compare_missing_city_returns_400(monkeypatch):
    install_fake_database(monkeypatch)
    install_fake_ollama(monkeypatch)

    client = backend_app.app.test_client()
    response = client.post("/api/destinations/ai-compare", json={"city_a": "Tokyo"})

    assert response.status_code == 400
    assert "error" in response.get_json()


def test_ai_compare_same_city_returns_400(monkeypatch):
    install_fake_database(monkeypatch)
    install_fake_ollama(monkeypatch)

    client = backend_app.app.test_client()
    response = client.post(
        "/api/destinations/ai-compare", json={"city_a": "Tokyo", "city_b": "Tokyo"}
    )

    assert response.status_code == 400


def test_ai_compare_unknown_city_returns_404(monkeypatch):
    install_fake_database(monkeypatch)
    ollama_calls = install_fake_ollama(monkeypatch)

    client = backend_app.app.test_client()
    response = client.post(
        "/api/destinations/ai-compare", json={"city_a": "Tokyo", "city_b": "Kyotoo"}
    )

    assert response.status_code == 404
    assert "Kyotoo" in response.get_json()["error"]
    assert len(ollama_calls) == 0  # never calls Ollama for an unresolved city


def test_ai_compare_city_names_are_case_insensitive(monkeypatch):
    install_fake_database(monkeypatch)
    install_fake_ollama(monkeypatch)

    client = backend_app.app.test_client()
    response = client.post(
        "/api/destinations/ai-compare", json={"city_a": "tokyo", "city_b": "KYOTO"}
    )

    assert response.status_code == 200


def test_ai_compare_ollama_unavailable_returns_502(monkeypatch):
    install_fake_database(monkeypatch)
    install_unreachable_ollama(monkeypatch)

    client = backend_app.app.test_client()
    response = client.post(
        "/api/destinations/ai-compare",
        json={"city_a": "Tokyo", "city_b": "Kyoto", "preferences": "nightlife and food"},
    )

    assert response.status_code == 502
    assert response.get_json()["error"] == "AI comparison service is unavailable."


def test_ai_compare_database_unavailable_returns_503(monkeypatch):
    install_unreachable_database(monkeypatch)
    install_fake_ollama(monkeypatch)

    client = backend_app.app.test_client()
    response = client.post(
        "/api/destinations/ai-compare",
        json={"city_a": "Tokyo", "city_b": "Kyoto", "preferences": "nightlife and food"},
    )

    assert response.status_code == 503
    assert response.get_json()["error"] == "destination database unavailable"
