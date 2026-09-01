"""Client wrapping calls to the team's approved LLM (Llama via Ollama):
activity summaries (generate_summary) and free-form activity chat (ask)."""
import os

import requests

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "llama3.1:8b")


class AIServiceError(Exception):
    """Raised when the AI service is unreachable or returns an unexpected error."""


def _generate(prompt):
    payload = {
        "model": OLLAMA_MODEL,
        "prompt": prompt,
        "stream": False,
    }
    try:
        response = requests.post(f"{OLLAMA_URL}/api/generate", json=payload, timeout=30)
    except requests.exceptions.RequestException as exc:
        raise AIServiceError(str(exc)) from exc

    if response.status_code != 200:
        raise AIServiceError(f"Ollama returned {response.status_code}")

    body = response.json()
    text = body.get("response", "").strip()
    if not text:
        raise AIServiceError("Ollama returned an empty response")
    return text


def _build_summary_prompt(activity):
    return (
        "Write a short, one-to-two sentence summary of this travel activity "
        "for a trip itinerary app.\n"
        f"Name: {activity.get('activity_name')}\n"
        f"Type: {activity.get('activity_type')}\n"
        f"Cost: {activity.get('activity_cost')}\n"
        f"Duration: {activity.get('duration')}\n"
    )


def generate_summary(activity):
    return _generate(_build_summary_prompt(activity))


def _build_chat_prompt(message, context=None):
    prompt = (
        "You are a helpful assistant for a travel activity planning app. "
        "Answer the user's question about activities concisely.\n"
    )
    if context:
        prompt += f"Relevant activity context: {context}\n"
    prompt += f"User: {message}\n"
    return prompt


def ask(message, context=None):
    """Send a user message (with optional activity context) to the LLM and return its reply."""
    return _generate(_build_chat_prompt(message, context))
