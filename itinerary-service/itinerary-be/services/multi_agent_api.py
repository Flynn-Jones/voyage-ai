"""HTTP-only adapter to the existing shared server; no state/database ownership."""
import requests
from flask import current_app

from services.multi_agent_validation import WORKFLOW


class UpstreamError(Exception):
    def __init__(self, message, status=502):
        super().__init__(message)
        self.status = status


def call(method, path, payload=None):
    try:
        response = requests.request(
            method, current_app.config["MULTI_AGENT_SERVICE_URL"] + path,
            json=payload, timeout=current_app.config["MULTI_AGENT_TIMEOUT_SECONDS"],
        )
        body = response.json()
    except requests.Timeout as exc:
        raise UpstreamError("Multi-Agent Server timed out", 504) from exc
    except (requests.RequestException, ValueError) as exc:
        raise UpstreamError("Multi-Agent Server unavailable or returned invalid JSON") from exc
    if not isinstance(body, dict):
        raise UpstreamError("Multi-Agent Server returned a malformed envelope")
    if response.status_code in (400, 404, 409, 500):
        if body.get("status") != "error" or not isinstance(body.get("error"), (str, dict)):
            raise UpstreamError("Multi-Agent Server returned a malformed error")
        message = body["error"]
        if isinstance(message, dict):
            message = message.get("message", "Workflow failed")
        raise UpstreamError(str(message), 502 if response.status_code == 500 else response.status_code)
    expected = 201 if path == f"/workflows/{WORKFLOW}/runs" else 200
    if response.status_code != expected or body.get("status") != "success" or "result" not in body:
        raise UpstreamError("Multi-Agent Server returned an unexpected response")
    return body["result"]
