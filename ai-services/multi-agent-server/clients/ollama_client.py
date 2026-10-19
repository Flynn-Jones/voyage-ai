"""Minimal Ollama /api/chat client used by every agent.

Returns the raw assistant text; JSON parsing and validation belong to the
agents (agents/base.py) so retry/fallback is decided in one place.
"""
import requests

import config


class LLMError(RuntimeError):
    """Ollama was unreachable, returned non-2xx, or returned an empty reply."""


def chat(system_prompt: str, user_prompt: str, json_mode: bool = True) -> str:
    payload = {
        "model": config.ollama_model(),
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "stream": False,
        "options": {"temperature": 0},
    }
    if json_mode:
        payload["format"] = "json"
    try:
        response = requests.post(
            f"{config.ollama_base_url()}/api/chat", json=payload, timeout=config.ollama_timeout()
        )
    except requests.RequestException as exc:
        raise LLMError(f"ollama unreachable: {exc}") from exc
    if response.status_code >= 300:
        raise LLMError(f"ollama returned {response.status_code}: {response.text[:200]}")
    try:
        content = response.json()["message"]["content"]
    except (ValueError, KeyError, TypeError) as exc:
        raise LLMError(f"ollama returned an unexpected payload: {exc}") from exc
    if not content or not content.strip():
        raise LLMError("ollama returned an empty reply")
    return content


def reachable() -> bool:
    try:
        return requests.get(f"{config.ollama_base_url()}/api/tags", timeout=3).ok
    except requests.RequestException:
        return False
