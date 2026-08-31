"""Client for generating a short AI activity summary via Ollama."""
import os

import requests

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "llama3")


class AIServiceError(Exception):
    """Raised when the AI service is unreachable or returns an unexpected error."""


def _build_prompt(activity):
    return (
        "Write a short, one-to-two sentence summary of this travel activity "
        "for a trip itinerary app.\n"
        f"Name: {activity.get('activity_name')}\n"
        f"Type: {activity.get('activity_type')}\n"
        f"Cost: {activity.get('activity_cost')}\n"
        f"Duration: {activity.get('duration')}\n"
    )


def generate_summary(activity):
    payload = {
        "model": OLLAMA_MODEL,
        "prompt": _build_prompt(activity),
        "stream": False,
    }
    try:
        response = requests.post(f"{OLLAMA_URL}/api/generate", json=payload, timeout=30)
    except requests.exceptions.RequestException as exc:
        raise AIServiceError(str(exc)) from exc

    if response.status_code != 200:
        raise AIServiceError(f"Ollama returned {response.status_code}")

    body = response.json()
    summary = body.get("response", "").strip()
    if not summary:
        raise AIServiceError("Ollama returned an empty response")
    return summary
