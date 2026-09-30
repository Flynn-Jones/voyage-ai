"""HTTP client wrapper around the shared local RAG server (not containerised)."""
import os

import requests

RAG_SERVICE_URL = os.environ.get("RAG_SERVICE_URL", "http://localhost:7002")
RAG_ENABLED = os.environ.get("RAG_ENABLED", "true").strip().lower() in ("1", "true", "yes", "on")

try:
    RAG_TIMEOUT_SECONDS = int(os.environ.get("RAG_TIMEOUT_SECONDS", "120"))
except ValueError:
    RAG_TIMEOUT_SECONDS = 120


class RAGServiceError(Exception):
    """Raised when the shared RAG server cannot be reached or returns an unexpected error."""


def rag_mode_is_enabled(req) -> bool:
    if not RAG_ENABLED:
        return False
    mode_header = req.headers.get("X-RAG-Mode", "on").strip().lower()
    return mode_header in ("1", "true", "yes", "on")


def _post(path: str, payload: dict) -> dict:
    try:
        response = requests.post(f"{RAG_SERVICE_URL}{path}", json=payload, timeout=RAG_TIMEOUT_SECONDS)
    except requests.exceptions.RequestException as exc:
        raise RAGServiceError(str(exc)) from exc

    try:
        return response.json()
    except ValueError as exc:
        raise RAGServiceError(f"invalid response from RAG server: {response.text}") from exc


def refresh_corpus(caller: str = "budget-service") -> dict:
    return _post("/refresh", {"caller": caller})


def retrieve_context(query: str, k: int = 5, caller: str = "budget-service") -> dict:
    return _post("/retrieve", {"query": query, "k": k, "caller": caller})


def answer_question(query: str, k: int = 5, caller: str = "budget-service") -> dict:
    return _post("/answer", {"query": query, "k": k, "caller": caller})
