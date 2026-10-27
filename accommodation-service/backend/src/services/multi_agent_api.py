"""HTTP client wrapper around the shared Multi-Agent Server (port 7004, not containerised).

Accommodation only runs its own workflow, accommodation_shortlist. The workflow is read-only
(no write tool) and never writes to accommodation-db, so there is nothing for this backend to
apply beyond reporting the run closed.
"""
import os

import requests

MULTI_AGENT_SERVICE_URL = os.environ.get("MULTI_AGENT_SERVICE_URL", "http://host.docker.internal:7004").rstrip("/")
MULTI_AGENT_ENABLED = os.environ.get("MULTI_AGENT_ENABLED", "true").strip().lower() in ("1", "true", "yes", "on")
WORKFLOW = "accommodation_shortlist"

try:
    # A run waits on three Ollama calls (Planner, Reviewer, and up to one retry each).
    MULTI_AGENT_TIMEOUT_SECONDS = int(os.environ.get("MULTI_AGENT_TIMEOUT_SECONDS", "420"))
except ValueError:
    MULTI_AGENT_TIMEOUT_SECONDS = 420
class MultiAgentServiceError(Exception):
    """Raised when the Multi-Agent Server cannot be reached or returns a non-JSON reply."""


def multi_agent_mode_is_enabled() -> bool:
    return MULTI_AGENT_ENABLED


def _call(method: str, path: str, payload: dict | None = None) -> tuple[int, dict]:
    """Return (http_status, envelope). The envelope is {"status": "success"|"error", "result"|"error": ...}."""
    try:
        response = requests.request(method, f"{MULTI_AGENT_SERVICE_URL}{path}", json=payload,
                                    timeout=MULTI_AGENT_TIMEOUT_SECONDS)
    except requests.exceptions.Timeout as exc:
        raise MultiAgentServiceError(f"Multi-Agent Server timed out after {MULTI_AGENT_TIMEOUT_SECONDS}s") from exc
    except requests.exceptions.RequestException as exc:
        raise MultiAgentServiceError(f"Multi-Agent Server unreachable at {MULTI_AGENT_SERVICE_URL}: {exc}") from exc
    try:
        body = response.json()
    except ValueError as exc:
        raise MultiAgentServiceError(f"Multi-Agent Server returned non-JSON ({response.status_code})") from exc
    if not isinstance(body, dict) or body.get("status") not in ("success", "error"):
        raise MultiAgentServiceError(f"Multi-Agent Server returned an unexpected payload ({response.status_code})")
    return response.status_code, body


def start_run(run_input: dict, requested_by: str = "accommodation-be") -> tuple[int, dict]:
    return _call("POST", f"/workflows/{WORKFLOW}/runs", {"input": run_input, "requested_by": requested_by})


def get_run(run_id: str) -> tuple[int, dict]:
    return _call("GET", f"/runs/{run_id}")


def get_history(run_id: str) -> tuple[int, dict]:
    return _call("GET", f"/runs/{run_id}/history")


def submit_review(run_id: str, review: dict) -> tuple[int, dict]:
    return _call("POST", f"/runs/{run_id}/review", review)


def report_applied(run_id: str) -> tuple[int, dict]:
    return _call("POST", f"/runs/{run_id}/applied", {"created_ids": [], "target": "none (read-only workflow)"})
