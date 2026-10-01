"""Client for the ONE shared local MCP service (Streamable HTTP at /mcp).

Destination only calls the read-only `list_destinations` tool. Every failure
is raised as MCPClientError with a safe, UI-presentable message; the raw
detail is kept for backend logs only.
"""
import math
import os

import requests

MCP_SERVICE_URL = os.environ.get("MCP_SERVICE_URL", "http://localhost:7001").rstrip("/")
MCP_ENABLED = os.environ.get("MCP_ENABLED", "true").strip().lower() in ("1", "true", "yes", "on")
try:
    MCP_TIMEOUT_SECONDS = float(os.environ.get("MCP_TIMEOUT_SECONDS", "10"))
except ValueError:
    MCP_TIMEOUT_SECONDS = 10.0

MCP_PATH = "/mcp"
MCP_HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
TOOL_NAME = "list_destinations"
EXPECTED_SOURCE = "destination-database"
DESTINATION_KEYS = (
    "destination_id", "city", "country", "description", "average_daily_cost",
    "recommended_trip_length", "travel_style", "categories",
)


class MCPClientError(Exception):
    """kind: unavailable | timeout | tool_error | malformed."""

    def __init__(self, kind, message, detail=""):
        super().__init__(message)
        self.kind = kind
        self.message = message
        self.detail = detail


def _safe_tool_message(text):
    if text.startswith("invalid "):
        return text
    if text.startswith("destination-db"):
        return "Destination data is unavailable to the shared MCP service."
    return "The shared MCP tool reported an error."


def _is_number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(float(value))
    except (OverflowError, ValueError, TypeError):
        return False


def _is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _valid_destination(d):
    """Shape of a Destination record as served by the database API.

    Every key must be present. destination_id/city/country/categories must
    be non-null; the other columns may be None.
    """
    def opt(key, check):
        return d[key] is None or check(d[key])

    return (
        all(key in d for key in DESTINATION_KEYS)
        and _is_int(d["destination_id"])
        and isinstance(d["city"], str)
        and isinstance(d["country"], str)
        and opt("description", lambda v: isinstance(v, str))
        and opt("average_daily_cost", _is_number)
        and opt("recommended_trip_length", _is_int)
        and opt("travel_style", lambda v: isinstance(v, str))
        and isinstance(d["categories"], list)
        and all(isinstance(c, str) for c in d["categories"])
    )


def list_destinations(filters):
    body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": TOOL_NAME, "arguments": filters},
    }
    try:
        response = requests.post(
            f"{MCP_SERVICE_URL}{MCP_PATH}", json=body, headers=MCP_HEADERS, timeout=MCP_TIMEOUT_SECONDS
        )
    except requests.exceptions.Timeout as exc:
        raise MCPClientError("timeout", "The shared MCP service timed out.", str(exc)) from exc
    except requests.exceptions.RequestException as exc:
        raise MCPClientError("unavailable", "The shared MCP service is unavailable. Please try again.", str(exc)) from exc

    if response.status_code != 200:
        raise MCPClientError(
            "unavailable", "The shared MCP service is unavailable. Please try again.",
            f"status {response.status_code}",
        )

    malformed = lambda detail: MCPClientError(  # noqa: E731
        "malformed", "The shared service returned an unexpected response.", detail
    )
    try:
        payload = response.json()
    except ValueError as exc:
        raise malformed("non-JSON body") from exc
    if not isinstance(payload, dict):
        raise malformed("body is not an object")

    if "error" in payload:
        raise MCPClientError("tool_error", "The shared MCP tool reported an error.", str(payload["error"]))
    result = payload.get("result")
    if not isinstance(result, dict):
        raise malformed("missing result")

    if result.get("isError") is True:
        text = ""
        content = result.get("content")
        if isinstance(content, list) and content and isinstance(content[0], dict):
            text = str(content[0].get("text") or "")
        raise MCPClientError("tool_error", _safe_tool_message(text), text)

    data = result.get("structuredContent")
    if not isinstance(data, dict):
        raise malformed("missing structuredContent")
    destinations = data.get("destinations")
    if (
        not isinstance(destinations, list)
        or not all(isinstance(d, dict) for d in destinations)
        or not isinstance(data.get("count"), int)
        or isinstance(data.get("count"), bool)
        or data["count"] != len(destinations)
        or not isinstance(data.get("filters"), dict)
    ):
        raise malformed("unexpected structuredContent shape")
    if data.get("source") != EXPECTED_SOURCE:
        raise malformed("unexpected structuredContent source")
    if not all(_valid_destination(d) for d in destinations):
        raise malformed("destination row failed validation")

    return {"filters": data["filters"], "count": data["count"], "destinations": destinations}
