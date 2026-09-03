"""Live end-to-end tests for the Ollama destination comparison.

Requires the stack to be running:
    docker compose -f student-1/docker-compose.yml up -d --build
    pytest student-1/tests/test_ai_compare_live.py -v

Skips (rather than silently passing or failing) if Ollama isn't reachable
from this machine, so the suite stays honest on a machine without it.
"""
import requests
import pytest

BACKEND_URL = "http://localhost:5001"
OLLAMA_URL = "http://localhost:11434"


def _ollama_available():
    try:
        response = requests.get(f"{OLLAMA_URL}/api/tags", timeout=3)
        return response.status_code == 200
    except requests.exceptions.RequestException:
        return False


pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        not _ollama_available(), reason="Ollama is not reachable at http://localhost:11434"
    ),
]


def test_ai_compare_tokyo_kyoto_returns_grounded_comparison():
    response = requests.post(
        f"{BACKEND_URL}/api/destinations/ai-compare",
        json={"city_a": "Tokyo", "city_b": "Kyoto", "preferences": "nightlife and food"},
        timeout=120,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["comparison"]
    assert len(body["comparison"]) > 20
    cities = {d["city"] for d in body["destinations"]}
    assert cities == {"Tokyo", "Kyoto"}


def test_ai_compare_three_consecutive_calls_succeed():
    """Session 4's done-when list requires three consecutive comparisons to
    succeed — proves the endpoint is reliable, not a one-shot fluke."""
    for _ in range(3):
        response = requests.post(
            f"{BACKEND_URL}/api/destinations/ai-compare",
            json={"city_a": "Tokyo", "city_b": "Kyoto", "preferences": "nightlife and food"},
            timeout=120,
        )
        assert response.status_code == 200
        assert response.json()["comparison"]


def test_ai_compare_unknown_city_returns_404_live():
    response = requests.post(
        f"{BACKEND_URL}/api/destinations/ai-compare",
        json={"city_a": "Tokyo", "city_b": "Nowhereville"},
        timeout=10,
    )
    assert response.status_code == 404
