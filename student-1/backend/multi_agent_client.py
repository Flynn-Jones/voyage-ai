"""Client for the ONE shared local Multi-Agent Server (destination_recommend workflow).

The workflow is read-only: the server never writes destination-db and this
backend never applies anything, it only forwards the human's decision and
reports the run closed. Every failure is raised as MultiAgentClientError with
a safe, UI-presentable message; the raw detail is kept for backend logs only.
"""
import os

import requests

MULTI_AGENT_SERVICE_URL = os.environ.get("MULTI_AGENT_SERVICE_URL", "http://localhost:7004").rstrip("/")
MULTI_AGENT_ENABLED = os.environ.get("MULTI_AGENT_ENABLED", "true").strip().lower() in ("1", "true", "yes", "on")
try:
    # A run waits on up to four Ollama calls (Planner and Reviewer, each with one retry).
    MULTI_AGENT_TIMEOUT_SECONDS = float(os.environ.get("MULTI_AGENT_TIMEOUT_SECONDS", "420"))
except ValueError:
    MULTI_AGENT_TIMEOUT_SECONDS = 420.0
WORKFLOW = "destination_recommend"
REQUESTED_BY = "destination-backend"
APPLIED_TARGET = "none (read-only destination_recommend)"


class MultiAgentClientError(Exception):
    """kind: unavailable | timeout | malformed."""

    def __init__(self, kind, message, detail=""):
        super().__init__(message)
        self.kind = kind
        self.message = message
        self.detail = detail


def _call(method, path, payload=None, params=None):
    """Return (http_status, envelope); the envelope is {"status": "success"|"error", ...}."""
    try:
        response = requests.request(
            method, f"{MULTI_AGENT_SERVICE_URL}{path}", json=payload, params=params,
            timeout=MULTI_AGENT_TIMEOUT_SECONDS,
        )
    except requests.exceptions.Timeout as exc:
        raise MultiAgentClientError("timeout", "The shared Multi-Agent Server timed out.", str(exc)) from exc
    except requests.exceptions.RequestException as exc:
        raise MultiAgentClientError(
            "unavailable", "The shared Multi-Agent Server is unavailable. Please try again.", str(exc)
        ) from exc
    try:
        body = response.json()
    except ValueError as exc:
        raise MultiAgentClientError(
            "malformed", "The shared service returned an unexpected response.", f"non-JSON ({response.status_code})"
        ) from exc
    if not isinstance(body, dict) or body.get("status") not in ("success", "error"):
        raise MultiAgentClientError(
            "malformed", "The shared service returned an unexpected response.", f"bad envelope ({response.status_code})"
        )
    return response.status_code, body


def start_run(run_input):
    return _call("POST", f"/workflows/{WORKFLOW}/runs", {"input": run_input, "requested_by": REQUESTED_BY})


def list_runs(limit):
    return _call("GET", "/runs", params={"workflow": WORKFLOW, "limit": limit})


def get_run(run_id):
    return _call("GET", f"/runs/{run_id}")


def get_history(run_id):
    return _call("GET", f"/runs/{run_id}/history")


def submit_review(run_id, review):
    return _call("POST", f"/runs/{run_id}/review", review)


def report_applied(run_id):
    return _call("POST", f"/runs/{run_id}/applied", {"created_ids": [], "target": APPLIED_TARGET})
