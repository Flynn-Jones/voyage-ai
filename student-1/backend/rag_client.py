"""Client for the ONE shared local RAG service (POST /answer).

Consumes the shared contract as-is: no retrieval, grounding or intent logic
lives here. Failures raise RAGClientError with a safe message; raw detail is
for backend logs only.
"""
import os

import requests

RAG_SERVICE_URL = os.environ.get("RAG_SERVICE_URL", "http://localhost:7002").rstrip("/")
RAG_ENABLED = os.environ.get("RAG_ENABLED", "true").strip().lower() in ("1", "true", "yes", "on")
try:
    RAG_TIMEOUT_SECONDS = float(os.environ.get("RAG_TIMEOUT_SECONDS", "120"))
except ValueError:
    RAG_TIMEOUT_SECONDS = 120.0

RAG_CALLER = "destination-service"
RAG_K = 5
GROUNDED_CONFIDENCE = ("High", "Medium", "Low")

INFRA_MESSAGES = {
    "llm_unavailable": "The shared RAG service could not reach its language model.",
    "retrieval_failed": "The shared RAG service could not retrieve context.",
}


class RAGClientError(Exception):
    """kind: unavailable | timeout | infrastructure | malformed."""

    def __init__(self, kind, message, error_type=None, detail=""):
        super().__init__(message)
        self.kind = kind
        self.message = message
        self.error_type = error_type
        self.detail = detail


def _malformed(detail):
    return RAGClientError("malformed", "The shared service returned an unexpected response.", detail=detail)


def answer(question):
    try:
        response = requests.post(
            f"{RAG_SERVICE_URL}/answer",
            json={"query": question, "k": RAG_K, "caller": RAG_CALLER},
            timeout=RAG_TIMEOUT_SECONDS,
        )
    except requests.exceptions.Timeout as exc:
        raise RAGClientError("timeout", "The shared RAG service timed out.", detail=str(exc)) from exc
    except requests.exceptions.RequestException as exc:
        raise RAGClientError(
            "unavailable", "The shared RAG service is unavailable. Please try again.", detail=str(exc)
        ) from exc

    try:
        data = response.json()
    except ValueError as exc:
        raise _malformed(f"non-JSON body (status {response.status_code})") from exc
    if not isinstance(data, dict):
        raise _malformed("body is not an object")

    status = data.get("status")
    if status == "error":
        error_type = data.get("error_type")
        raise RAGClientError(
            "infrastructure",
            INFRA_MESSAGES.get(error_type, "The shared RAG service reported an infrastructure error."),
            error_type=error_type if isinstance(error_type, str) else None,
            detail=str(data.get("error")),
        )

    if response.status_code != 200:
        raise _malformed(f"unexpected status {response.status_code} for {status!r}")

    citations = data.get("citations")
    if not isinstance(citations, list):
        raise _malformed("citations is not a list")

    if status == "success":
        text = data.get("answer")
        if not isinstance(text, str) or not text.strip():
            raise _malformed("missing answer")
        if data.get("confidence_category") not in GROUNDED_CONFIDENCE:
            raise _malformed("bad confidence_category")
        for c in citations:
            if not (isinstance(c, dict) and isinstance(c.get("chunk_id"), str) and isinstance(c.get("source_id"), str)):
                raise _malformed("bad citation")
    elif status == "insufficient_context":
        if data.get("confidence_category") != "Insufficient" or citations != []:
            raise _malformed("inconsistent insufficient_context")
        text = data.get("answer") if isinstance(data.get("answer"), str) else ""
    else:
        raise _malformed(f"unknown status {status!r}")

    summary = data.get("retrieval_summary")
    return {
        "status": status,
        "answer": text,
        "citations": [
            {"chunk_id": c["chunk_id"], "source_id": c["source_id"], "authority_tier": c.get("authority_tier")}
            for c in citations
        ],
        "confidence_category": data["confidence_category"],
        "retrieved_count": summary.get("retrieved_count") if isinstance(summary, dict) else None,
    }
