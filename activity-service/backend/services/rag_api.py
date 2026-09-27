"""HTTP client for the host-local RAG server (rag-server/rag_http_server.py) plus RAG mode gating.

Split from its routes (routes/rag_mode.py) as budget-service does, unlike
routes/mcp_mode.py which keeps both in one module.
"""
import os

import requests

RAG_SERVICE_URL = os.environ.get("RAG_SERVICE_URL", "http://host.docker.internal:6013")
RAG_ENABLED = os.environ.get("RAG_ENABLED", "true").strip().lower() in ("1", "true", "yes", "on")

try:
    RAG_TIMEOUT_SECONDS = int(os.environ.get("RAG_TIMEOUT_SECONDS", "120"))
except ValueError:
    RAG_TIMEOUT_SECONDS = 120


class RAGServiceError(Exception):
    """Raised when the RAG server cannot be reached or returns a non-JSON body."""


def rag_mode_is_enabled(req) -> bool:
    if not RAG_ENABLED:
        return False
    mode_header = req.headers.get("X-RAG-Mode", "on").strip().lower()
    return mode_header in ("1", "true", "yes", "on")


def call_rag_service(path: str, payload: dict) -> tuple[dict, int]:
    """POSTs to the RAG server. Returns (body, status_code) for any JSON reply,
    including the server's own 400/500 structured errors."""
    try:
        response = requests.post(f"{RAG_SERVICE_URL}/{path}", json=payload, timeout=RAG_TIMEOUT_SECONDS)
    except requests.exceptions.RequestException as exc:
        raise RAGServiceError(str(exc)) from exc

    try:
        return response.json(), response.status_code
    except ValueError as exc:
        raise RAGServiceError(f"invalid response from RAG server: {response.text[:200]}") from exc
