"""Session 1 smoke tests: prove the three-service skeleton is up and wired.

Run against a live stack:
    docker compose -f student-1/docker-compose.yml up -d --build
    pytest student-1/tests -v
"""
import requests

FRONTEND_URL = "http://localhost:3001"
BACKEND_URL = "http://localhost:5001"
DATABASE_URL = "http://localhost:6001"


def test_frontend_health():
    response = requests.get(f"{FRONTEND_URL}/health", timeout=5)
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_backend_health():
    response = requests.get(f"{BACKEND_URL}/health", timeout=5)
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_database_health():
    response = requests.get(f"{DATABASE_URL}/health", timeout=5)
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_backend_reaches_database():
    """Proves the backend->database hop is real, not a hardcoded 'ok'."""
    response = requests.get(f"{BACKEND_URL}/health", timeout=5)
    assert response.status_code == 200
    body = response.json()
    assert body["database"]["status"] == "ok"
