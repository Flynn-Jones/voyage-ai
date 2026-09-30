"""Offline unit tests for backend/llm_client.py.

No Docker and no live Ollama required: imports llm_client directly (it has
no dependency on Flask or the rest of the app) and monkeypatches its
`requests` module.

Focused on the failure and shape-handling branches nothing else covers —
the connection-error path is already exercised end to end by
test_ai_compare.py::test_ai_compare_ollama_unavailable_returns_502, so it is
not repeated here.
"""
import os
import sys

sys.path.insert(
    0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend")
)

import llm_client  # noqa: E402  (must follow the sys.path.insert above)

import pytest  # noqa: E402


class DummyResponse:
    def __init__(self, status_code, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self):
        if self._payload is None:
            raise ValueError("response has no JSON body")
        return self._payload


ROW_A = {
    "city": "Tokyo", "country": "Japan", "description": "Neon-lit metropolis.",
    "average_daily_cost": 150.0, "recommended_trip_length": 6,
    "travel_style": "City break", "categories": ["food", "nightlife"],
}
ROW_B_SPARSE = {
    "city": "Nowhere", "country": "Nowhereland", "description": None,
    "average_daily_cost": None, "recommended_trip_length": None,
    "travel_style": None, "categories": [],
}


def test_prompt_is_grounded_and_system_message_forbids_invention():
    messages = llm_client.build_comparison_prompt(ROW_A, ROW_B_SPARSE, "nightlife and food")

    assert messages[0]["role"] == "system"
    assert "never invent" in messages[0]["content"].lower()
    user_content = messages[1]["content"]
    assert "Tokyo" in user_content and "Nowhere" in user_content
    assert "$150.00" in user_content
    assert "nightlife and food" in user_content


def test_prompt_renders_missing_fields_as_unknown_not_a_crash():
    messages = llm_client.build_comparison_prompt(ROW_A, ROW_B_SPARSE, "food")
    user_content = messages[1]["content"]

    assert "unknown" in user_content  # ROW_B_SPARSE's cost/length
    assert "none listed" in user_content  # ROW_B_SPARSE's empty categories


def test_create_chat_completion_posts_expected_payload(monkeypatch):
    captured = {}

    def fake_post(url, json=None, timeout=None, **kwargs):
        captured["url"] = url
        captured["json"] = json
        return DummyResponse(200, {"message": {"content": "Tokyo wins."}})

    monkeypatch.setattr(llm_client.requests, "post", fake_post)

    result = llm_client.create_chat_completion([{"role": "user", "content": "hi"}])

    assert result == "Tokyo wins."
    assert captured["url"] == f"{llm_client.OLLAMA_BASE_URL}/api/chat"
    assert captured["json"]["model"] == llm_client.OLLAMA_MODEL
    assert captured["json"]["stream"] is False


def test_non_200_from_ollama_raises_llm_service_error(monkeypatch):
    monkeypatch.setattr(
        llm_client.requests, "post", lambda *a, **k: DummyResponse(500, text="internal error")
    )

    with pytest.raises(llm_client.LLMServiceError):
        llm_client.create_chat_completion([{"role": "user", "content": "hi"}])


def test_unexpected_response_shape_raises_llm_service_error(monkeypatch):
    monkeypatch.setattr(
        llm_client.requests, "post", lambda *a, **k: DummyResponse(200, {"unexpected": "shape"})
    )

    with pytest.raises(llm_client.LLMServiceError):
        llm_client.create_chat_completion([{"role": "user", "content": "hi"}])
