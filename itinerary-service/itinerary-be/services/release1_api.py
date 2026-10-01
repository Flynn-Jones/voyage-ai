"""Optional host-service clients. No calls are made during application startup."""
import os

import requests


class UpstreamError(Exception):
    pass


def enabled(kind):
    return os.getenv(f"{kind}_ENABLED", "true").lower() in ("1", "true", "yes", "on")


def post(kind, path, payload):
    port, default_timeout = (7001, 30) if kind == "MCP" else (7002, 120)
    base = os.getenv(f"{kind}_SERVICE_URL", f"http://localhost:{port}").rstrip("/")
    try:
        timeout = float(os.getenv(f"{kind}_TIMEOUT_SECONDS", str(default_timeout)))
        if timeout <= 0:
            raise ValueError()
    except ValueError:
        timeout = default_timeout
    try:
        response = requests.post(f"{base}/{path}", json=payload, timeout=timeout)
        body = response.json()
        if kind == "RAG" and isinstance(body, dict) and body.get("error_type") == "llm_unavailable":
            raise UpstreamError("RAG answer model is unavailable or returned an invalid response")
        if response.status_code != 200 or not isinstance(body, dict) or body.get("status") != "success":
            raise UpstreamError(f"{kind} service returned an error")
        return body
    except (requests.RequestException, ValueError) as exc:
        raise UpstreamError(f"{kind} service is unavailable or returned invalid JSON") from exc
