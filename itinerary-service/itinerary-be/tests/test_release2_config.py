"""Release 2 switches and reserved Multi-Agent configuration; no external services."""
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import create_app
from services import database_api, enrichment, release1_api


def test_defaults_preserve_ai_and_leave_multi_agent_disabled(monkeypatch):
    for key in ("AI_ENABLED", "MULTI_AGENT_ENABLED", "MULTI_AGENT_SERVICE_URL", "MULTI_AGENT_TIMEOUT_SECONDS"):
        monkeypatch.delenv(key, raising=False)
    application = create_app()
    assert application.config["AI_ENABLED"] is True
    assert application.config["MULTI_AGENT_ENABLED"] is False
    assert application.config["MULTI_AGENT_SERVICE_URL"] == "http://localhost:7004"
    assert application.config["MULTI_AGENT_TIMEOUT_SECONDS"] == 240


def test_configuration_overrides_do_not_contact_multi_agent(monkeypatch):
    monkeypatch.setenv("AI_ENABLED", "false")
    monkeypatch.setenv("MULTI_AGENT_ENABLED", "true")
    monkeypatch.setenv("MULTI_AGENT_SERVICE_URL", "http://example:7004/")
    monkeypatch.setenv("MULTI_AGENT_TIMEOUT_SECONDS", "600")
    network = Mock(side_effect=AssertionError("configuration must not call a service"))
    monkeypatch.setattr(release1_api.requests.sessions.Session, "request", network)
    config = create_app().config
    assert config["AI_ENABLED"] is False
    assert config["MULTI_AGENT_ENABLED"] is True
    assert config["MULTI_AGENT_SERVICE_URL"] == "http://example:7004"
    assert config["MULTI_AGENT_TIMEOUT_SECONDS"] == 600
    network.assert_not_called()


@pytest.mark.parametrize("value", ["0", "-1", "invalid", "1.5", "nan", "inf", ""])
def test_invalid_multi_agent_timeout_uses_positive_default(monkeypatch, value):
    monkeypatch.setenv("MULTI_AGENT_TIMEOUT_SECONDS", value)
    assert create_app().config["MULTI_AGENT_TIMEOUT_SECONDS"] == 240


def test_all_ai_modes_disabled_preserves_crud_and_health(monkeypatch):
    for kind in ("AI", "MCP", "RAG", "MULTI_AGENT"):
        monkeypatch.setenv(f"{kind}_ENABLED", "false")
    monkeypatch.setattr(database_api, "get_items", lambda: [])
    monkeypatch.setattr(enrichment, "enrich_items", lambda items: items)
    network = Mock(side_effect=AssertionError("disabled modes must not contact a service"))
    monkeypatch.setattr(release1_api.requests.sessions.Session, "request", network)
    client = create_app().test_client()
    for route in ("ai-review", "mcp/itinerary", "rag/answer", "rag/retrieve", "rag/refresh"):
        result = client.post(f"/api/itinerary/{route}", json={})
        assert result.status_code == 403
        assert "disabled" in result.get_json()["error"].lower()
    assert client.get("/api/itinerary").get_json() == []
    assert client.get("/health").status_code == 200
    network.assert_not_called()
