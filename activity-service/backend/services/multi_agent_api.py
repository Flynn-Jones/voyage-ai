"""HTTP client wrapper around the shared Multi-Agent Server (port 7004, host-run, not containerised).

Activity only runs its own workflow, activity_schedule_check
(ai-services/multi-agent-server/workflows/). It is read-only and never writes to the activity
database, so after a human decision there is nothing to apply beyond reporting the run closed.
"""
import os

import requests

MULTI_AGENT_SERVICE_URL = os.environ.get("MULTI_AGENT_SERVICE_URL", "http://localhost:7004").rstrip("/")
MULTI_AGENT_ENABLED = os.environ.get("MULTI_AGENT_ENABLED", "true").strip().lower() in ("1", "true", "yes", "on")
WORKFLOW = "activity_schedule_check"

try:
    # A run waits on Planner, Worker and Reviewer Ollama calls (plus up to one retry each).
    MULTI_AGENT_TIMEOUT_SECONDS = int(os.environ.get("MULTI_AGENT_TIMEOUT_SECONDS", "420"))
except ValueError:
    MULTI_AGENT_TIMEOUT_SECONDS = 420
class MultiAgentServiceError(Exception):
    """Raised when the Multi-Agent Server cannot be reached or returns a non-JSON reply."""


def multi_agent_mode_is_enabled():
    return MULTI_AGENT_ENABLED


def _call(method, path, payload=None):
    """Return (http_status, envelope). The envelope is {"status": "success"|"error", "result"|"error": ...}."""
    try:
        response = requests.request(method, f"{MULTI_AGENT_SERVICE_URL}{path}", json=payload,
                                    timeout=MULTI_AGENT_TIMEOUT_SECONDS)
    except requests.exceptions.Timeout as exc:
        raise MultiAgentServiceError(f"timed out after {MULTI_AGENT_TIMEOUT_SECONDS}s") from exc
    except requests.exceptions.RequestException as exc:
        raise MultiAgentServiceError(f"unreachable at {MULTI_AGENT_SERVICE_URL}: {exc}") from exc
    try:
        body = response.json()
    except ValueError as exc:
        raise MultiAgentServiceError(f"returned non-JSON ({response.status_code})") from exc
    if not isinstance(body, dict) or body.get("status") not in ("success", "error"):
        raise MultiAgentServiceError(f"returned an unexpected payload ({response.status_code})")
    return response.status_code, body


def start_run(run_input, requested_by="activity-backend"):
    return _call("POST", f"/workflows/{WORKFLOW}/runs", {"input": run_input, "requested_by": requested_by})


def get_run(run_id):
    return _call("GET", f"/runs/{run_id}")


def get_history(run_id):
    return _call("GET", f"/runs/{run_id}/history")


def submit_review(run_id, review):
    return _call("POST", f"/runs/{run_id}/review", review)


def report_applied(run_id):
    return _call("POST", f"/runs/{run_id}/applied",
                 {"created_ids": [], "target": "none (read-only workflow; activity-db unchanged)"})
