"""Calls the local Ollama runtime for the agentic_loop's implementation and review agents.

Two models by default so the loop mirrors a dual-agent pattern (one model proposes
an assessment, a second reviews it) -- set AGENTIC_REVIEW_MODEL to a different model
than AGENTIC_IMPLEMENTATION_MODEL (e.g. llama3.1:8b) if you have one pulled locally;
otherwise both fall back to the same model already used by AI-Mode.
"""
import os

import requests

OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
IMPLEMENTATION_MODEL = os.environ.get("AGENTIC_IMPLEMENTATION_MODEL", "qwen2.5:7b")
REVIEW_MODEL = os.environ.get("AGENTIC_REVIEW_MODEL", IMPLEMENTATION_MODEL)


def call(system_prompt: str, user_prompt: str, review: bool = False):
    """Returns (output_text, error_message). Exactly one is non-None."""
    model = REVIEW_MODEL if review else IMPLEMENTATION_MODEL
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "stream": False,
        "options": {"temperature": 0.0},
    }
    try:
        response = requests.post(f"{OLLAMA_BASE_URL}/api/chat", json=payload, timeout=120)
    except requests.exceptions.RequestException as exc:
        return None, f"Ollama unavailable ({model}): {exc}"

    if response.status_code != 200:
        return None, f"Ollama returned {response.status_code} for {model}: {response.text}"

    try:
        return response.json()["message"]["content"].strip(), None
    except (KeyError, TypeError):
        return None, f"Unexpected Ollama response shape from {model}: {response.text}"
