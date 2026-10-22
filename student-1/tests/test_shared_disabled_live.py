"""Live disabled-mode tests for the shared MCP/RAG integrations.

Run against a stack started with the integrations disabled, as CI does:
    export DESTINATION_AI_ENABLED=false DESTINATION_MCP_ENABLED=false \
           DESTINATION_RAG_ENABLED=false DESTINATION_MULTI_AGENT_ENABLED=false
    docker compose -f student-1/docker-compose.yml up -d --build
    pytest student-1/tests/test_shared_disabled_live.py -v

Each test runs only when its own DESTINATION_*_ENABLED variable is "false" in
the pytest environment, so enabled-mode runs skip them. A `disabled` response
(rather than `unavailable` or `timeout`) shows the backend never attempted an
outbound call to the shared service.
"""
import os

import pytest
import requests

pytestmark = pytest.mark.live

FRONTEND_URL = "http://localhost:3001"
BACKEND_URL = "http://localhost:5001"
HTMX = {"HX-Request": "true"}


def _flag_false(name):
    return os.environ.get(name, "").strip().lower() == "false"


ai_disabled = pytest.mark.skipif(
    not _flag_false("DESTINATION_AI_ENABLED"), reason="DESTINATION_AI_ENABLED is not false")
multi_agent_disabled = pytest.mark.skipif(
    not _flag_false("DESTINATION_MULTI_AGENT_ENABLED"), reason="DESTINATION_MULTI_AGENT_ENABLED is not false")
mcp_disabled = pytest.mark.skipif(
    not _flag_false("DESTINATION_MCP_ENABLED"), reason="DESTINATION_MCP_ENABLED is not false")
rag_disabled = pytest.mark.skipif(
    not _flag_false("DESTINATION_RAG_ENABLED"), reason="DESTINATION_RAG_ENABLED is not false")


@mcp_disabled
def test_backend_mcp_search_is_disabled():
    r = requests.post(f"{BACKEND_URL}/api/destinations/mcp-search", json={"country": "Japan"}, timeout=10)
    assert r.status_code == 503
    assert r.json()["status"] == "disabled"


@rag_disabled
def test_backend_rag_answer_is_disabled():
    r = requests.post(
        f"{BACKEND_URL}/api/destinations/rag-answer",
        json={"question": "Which destination is known for street food?"}, timeout=10)
    assert r.status_code == 503
    assert r.json()["status"] == "disabled"


@mcp_disabled
def test_frontend_mcp_page_renders_disabled_state():
    r = requests.post(f"{FRONTEND_URL}/mcp-lookup", data={"country": "Japan"}, headers=HTMX, timeout=10)
    assert r.status_code == 200
    assert "MCP lookup is disabled" in r.text


@rag_disabled
def test_frontend_rag_page_renders_disabled_state():
    r = requests.post(f"{FRONTEND_URL}/ask", data={"question": "Which destination is known for street food?"},
                      headers=HTMX, timeout=10)
    assert r.status_code == 200
    assert "RAG questions are disabled" in r.text


@ai_disabled
def test_backend_ai_compare_is_disabled():
    r = requests.post(f"{BACKEND_URL}/api/destinations/ai-compare",
                      json={"city_a": "Tokyo", "city_b": "Kyoto"}, timeout=10)
    assert r.status_code == 503
    assert r.json()["status"] == "disabled"


@ai_disabled
def test_frontend_compare_renders_disabled_state():
    r = requests.post(f"{FRONTEND_URL}/compare", data={"city_a": "Tokyo", "city_b": "Kyoto"},
                      headers=HTMX, timeout=10)
    assert r.status_code == 200
    assert "AI comparison is disabled." in r.text


@multi_agent_disabled
@pytest.mark.parametrize("method,path", [
    ("post", "/api/destinations/multi-agent/runs"),
    ("get", "/api/destinations/multi-agent/runs"),
    ("get", "/api/destinations/multi-agent/runs/abc123"),
    ("get", "/api/destinations/multi-agent/runs/abc123/history"),
    ("post", "/api/destinations/multi-agent/runs/abc123/review"),
])
def test_backend_multi_agent_routes_are_disabled(method, path):
    r = getattr(requests, method)(f"{BACKEND_URL}{path}", json={"interests": ["food"]}, timeout=10)
    assert r.status_code == 503
    assert r.json()["status"] == "disabled"


@multi_agent_disabled
def test_frontend_multi_agent_page_renders_disabled_state():
    r = requests.get(f"{FRONTEND_URL}/multi-agent", timeout=10)
    assert r.status_code == 200
    assert "Multi-Agent mode is disabled for Destination Manager." in r.text
    assert 'name="interests"' not in r.text


@ai_disabled
@multi_agent_disabled
@mcp_disabled
@rag_disabled
def test_backend_health_reports_every_ai_feature_off():
    body = requests.get(f"{BACKEND_URL}/health", timeout=10).json()
    assert body["status"] == "ok"
    assert body["features"] == {"ai": False, "mcp": False, "rag": False, "multi_agent": False}
