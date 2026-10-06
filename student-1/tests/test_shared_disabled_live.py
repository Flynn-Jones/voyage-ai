"""Live disabled-mode tests for the shared MCP/RAG integrations.

Run against a stack started with the integrations disabled, as CI does:
    DESTINATION_MCP_ENABLED=false DESTINATION_RAG_ENABLED=false \
        docker compose -f student-1/docker-compose.yml up -d --build
    DESTINATION_MCP_ENABLED=false DESTINATION_RAG_ENABLED=false \
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
