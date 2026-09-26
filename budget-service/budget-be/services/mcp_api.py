"""HTTP client wrapper around the shared local MCP server (not containerised)."""
import os

import requests

MCP_SERVICE_URL = os.environ.get("MCP_SERVICE_URL", "http://localhost:7001")
MCP_ENABLED = os.environ.get("MCP_ENABLED", "true").strip().lower() in ("1", "true", "yes", "on")

try:
    MCP_TIMEOUT_SECONDS = int(os.environ.get("MCP_TIMEOUT_SECONDS", "30"))
except ValueError:
    MCP_TIMEOUT_SECONDS = 30


class MCPServiceError(Exception):
    """Raised when the shared MCP server cannot be reached or returns an unexpected error."""


def mcp_mode_is_enabled(req) -> bool:
    if not MCP_ENABLED:
        return False
    mode_header = req.headers.get("X-MCP-Mode", "on").strip().lower()
    return mode_header in ("1", "true", "yes", "on")


def call_tool(tool_name: str, payload: dict) -> dict:
    try:
        response = requests.post(f"{MCP_SERVICE_URL}/{tool_name}", json=payload, timeout=MCP_TIMEOUT_SECONDS)
    except requests.exceptions.RequestException as exc:
        raise MCPServiceError(str(exc)) from exc

    try:
        data = response.json()
    except ValueError as exc:
        raise MCPServiceError(f"invalid response from MCP server: {response.text}") from exc

    if response.status_code in (404, 500):
        raise MCPServiceError(f"MCP server returned {response.status_code}: {data}")

    return data
