"""Wrapper around an Ollama-compatible chat completion endpoint.

Mirrors the shape of budget-service's services/llm_client.py (the only
existing AI client on this branch), using the /api/chat endpoint. Kept
deliberately small: no retries, no streaming, no JSON-mode parsing — the
destination comparison is prose in, prose out.
"""
import os

import requests

OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "qwen2.5:0.5b")
OLLAMA_TIMEOUT_SECONDS = float(os.environ.get("OLLAMA_TIMEOUT_SECONDS", "60"))


class LLMServiceError(Exception):
    """Raised when Ollama cannot be reached or returns an unexpected response."""


def create_chat_completion(messages, model=None, temperature=0.2):
    payload = {
        "model": model or OLLAMA_MODEL,
        "messages": messages,
        "stream": False,
        "options": {"temperature": temperature},
    }
    try:
        response = requests.post(
            f"{OLLAMA_BASE_URL}/api/chat", json=payload, timeout=OLLAMA_TIMEOUT_SECONDS
        )
    except requests.exceptions.RequestException as exc:
        raise LLMServiceError(str(exc)) from exc

    if response.status_code != 200:
        raise LLMServiceError(f"Ollama returned {response.status_code}: {response.text}")

    data = response.json()
    try:
        return data["message"]["content"]
    except (KeyError, TypeError) as exc:
        raise LLMServiceError(f"Unexpected Ollama response shape: {data}") from exc


COMPARISON_SYSTEM_PROMPT = (
    "You are a travel advisor for the VoyageAI app. Compare exactly the two "
    "destinations in DATA. Use only the facts in DATA - never invent prices, "
    "trip lengths, or attractions. Reply in under 180 words: one short "
    "paragraph per city, then a final line starting \"Recommendation:\" "
    "naming one city and why it best matches the traveller's interests."
)


def _format_destination(index, row):
    categories = ", ".join(row.get("categories") or []) or "none listed"
    cost = row.get("average_daily_cost")
    cost_text = f"${cost:.2f}" if cost is not None else "unknown"
    length = row.get("recommended_trip_length")
    length_text = f"{length} days" if length is not None else "unknown"
    return (
        f"{index}. {row.get('city')}, {row.get('country')}\n"
        f"   Description: {row.get('description') or 'No description available.'}\n"
        f"   Average daily cost: {cost_text}\n"
        f"   Recommended trip length: {length_text}\n"
        f"   Travel style: {row.get('travel_style') or 'unspecified'}\n"
        f"   Categories: {categories}"
    )


def build_comparison_prompt(row_a, row_b, preferences):
    """Build the (system, user) chat messages grounding the comparison in
    the two retrieved destination records. Never lets the model invent data
    beyond what is passed in."""
    data = "\n".join(
        [_format_destination(1, row_a), _format_destination(2, row_b)]
    )
    user_content = f"Traveller interests: {preferences}\n\nDATA:\n{data}"
    return [
        {"role": "system", "content": COMPARISON_SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]


def generate_comparison(row_a, row_b, preferences):
    messages = build_comparison_prompt(row_a, row_b, preferences)
    return create_chat_completion(messages, temperature=0.2)
