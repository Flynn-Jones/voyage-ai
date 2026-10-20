"""Client for the shared RAG server (see ai-services/rag-server/tool-contracts.md).

Endpoints: POST /retrieve and POST /answer with {"query", "k", "caller", ...}.
Scoped extensions (scope="itinerary" + trip_reference/day, scope="activity")
are passed through untouched as extra keyword arguments.

The RAG server answers 200 for success and insufficient_context, and 4xx/5xx
with a JSON body otherwise; this client always returns that body (adding
"http_status") and never raises for an HTTP error status.
"""
import requests

import config

CALLER = "multi-agent-server"
ENDPOINTS = ("retrieve", "answer")


class RAGUnavailableError(RuntimeError):
    """The RAG server could not be reached at all."""


def call(endpoint: str, query: str, k: int = 5, **scope) -> dict:
    if endpoint not in ENDPOINTS:
        raise ValueError(f"unknown RAG endpoint '{endpoint}', expected one of {ENDPOINTS}")
    payload = {"query": query, "k": k, "caller": CALLER, **scope}
    try:
        response = requests.post(
            f"{config.rag_service_url()}/{endpoint}", json=payload, timeout=config.service_timeout()
        )
    except requests.RequestException as exc:
        raise RAGUnavailableError(f"rag server unreachable: {exc}") from exc
    try:
        body = response.json()
    except ValueError:
        body = {"status": "error", "error": f"non-JSON response ({response.status_code})"}
    if not isinstance(body, dict):
        body = {"status": "error", "error": "unexpected RAG payload"}
    body["http_status"] = response.status_code
    return body


def reachable() -> bool:
    try:
        return requests.get(f"{config.rag_service_url()}/health", timeout=config.PROBE_TIMEOUT_SECONDS).ok
    except requests.RequestException:
        return False
