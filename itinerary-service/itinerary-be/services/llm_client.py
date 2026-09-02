"""Small Ollama adapter for grounded itinerary recommendations."""
import json
import os

import requests

from services.prompt_loader import load_prompt


class LLMServiceError(Exception):
    """Ollama was unavailable or returned an unusable response."""


def review_itinerary(user_prompt, day, items, observations):
    base_url = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")
    model = os.environ.get("OLLAMA_MODEL", "qwen2.5:7b")
    try:
        timeout = float(os.environ.get("OLLAMA_TIMEOUT_SECONDS", "60"))
    except ValueError:
        timeout = 60

    context = {"requested_day": day, "itinerary_items": items, "observations": observations}
    task = load_prompt("task_prompt.txt").format(
        user_prompt=user_prompt,
        context=json.dumps(context, ensure_ascii=False, indent=2),
    )
    try:
        response = requests.post(
            f"{base_url}/api/chat",
            json={
                "model": model,
                "stream": False,
                "messages": [
                    {"role": "system", "content": load_prompt("system_prompt.txt")},
                    {"role": "user", "content": task},
                ],
            },
            timeout=timeout,
        )
        response.raise_for_status()
        body = response.json()
    except (requests.RequestException, ValueError) as error:
        raise LLMServiceError("Ollama is unavailable or returned invalid JSON") from error

    try:
        recommendation = body["message"]["content"].strip()
    except (KeyError, TypeError, AttributeError) as error:
        raise LLMServiceError("Ollama returned an unexpected response") from error
    if not recommendation:
        raise LLMServiceError("Ollama returned an empty recommendation")
    return {"model": model, "recommendation": recommendation}
