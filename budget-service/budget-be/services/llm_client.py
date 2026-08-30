"""Wrapper around an Ollama-compatible chat completion endpoint."""
import json
import os

import requests

from services import prompt_loader

OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "qwen2.5:7b")


class LLMServiceError(Exception):
    """Raised when the LLM backend cannot be reached or returns an unexpected response."""


def create_chat_completion(messages, model=None, temperature=0.0):
    payload = {
        "model": model or OLLAMA_MODEL,
        "messages": messages,
        "stream": False,
        "options": {"temperature": temperature},
    }
    try:
        response = requests.post(f"{OLLAMA_BASE_URL}/api/chat", json=payload, timeout=60)
    except requests.exceptions.RequestException as exc:
        raise LLMServiceError(str(exc)) from exc

    if response.status_code != 200:
        raise LLMServiceError(f"Ollama returned {response.status_code}: {response.text}")

    data = response.json()
    try:
        return data["message"]["content"]
    except (KeyError, TypeError) as exc:
        raise LLMServiceError(f"Unexpected Ollama response shape: {data}") from exc


def call_budget_agent(system_prompt_file, task_prompt_file, user_input):
    system_prompt = prompt_loader.load_budget_prompt(system_prompt_file)
    context_prompt = prompt_loader.load_budget_prompt("context_prompt.txt")
    task_prompt = prompt_loader.load_budget_prompt(task_prompt_file)

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "system", "content": context_prompt},
        {"role": "user", "content": f"{task_prompt}\n\nEvidence:\n{user_input}"},
    ]
    return create_chat_completion(messages, temperature=0.0)


INTENT_SYSTEM_PROMPT = (
    "Extract structured intent from a traveller's request to log a trip expense. "
    "Respond with ONLY a JSON object with keys: destination (string or null), "
    "category (string or null), expense (short string or null), "
    "trip_reference (string or null). No prose, no markdown fences."
)


def extract_expense_intent(user_prompt):
    messages = [
        {"role": "system", "content": INTENT_SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]
    raw = create_chat_completion(messages, temperature=0.0)
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise LLMServiceError(f"could not parse intent JSON from model output: {raw!r}") from exc
