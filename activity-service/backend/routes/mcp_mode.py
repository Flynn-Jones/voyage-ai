"""MCP mode routes: forwards tool calls to activity-service's local MCP server.

Mirrors budget-service's routes/mcp_mode.py + services/mcp_api.py (kept in
one module here): one POST route per read-only tool, gated by MCP_ENABLED and
the X-MCP-Mode header, forwarding to mcp-server/mcp_http_server.py over HTTP.

/api/activity/mcp/ask adds an LLM tool-selection step on top: the model picks
a tool + arguments using prompts/mcp/implementation/tool_selection_prompt.txt,
the selection is validated against the same registry the direct routes use,
and only then is the tool called. Following the lesson from AI Mode's
extract_intent, output that can't be parsed or names an unknown tool is
reported back as such — nothing is guessed and no tool is called.
"""
import json
import logging
import os
import re
from pathlib import Path

import requests
from flask import Blueprint, jsonify, request

from services import ai_client

mcp_mode_bp = Blueprint("mcp_mode", __name__)
logger = logging.getLogger(__name__)

MCP_SERVICE_URL = os.environ.get("MCP_SERVICE_URL", "http://localhost:7003")
MCP_ENABLED = os.environ.get("MCP_ENABLED", "true").strip().lower() in ("1", "true", "yes", "on")

try:
    MCP_TIMEOUT_SECONDS = int(os.environ.get("MCP_TIMEOUT_SECONDS", "30"))
except ValueError:
    MCP_TIMEOUT_SECONDS = 30

# <backend>/../prompts — activity-service/prompts locally, /prompts in the
# container (mounted read-only by docker-compose.yml).
PROMPT_DIR = Path(__file__).resolve().parent.parent.parent / "prompts"
TOOL_SELECTION_PROMPT_PATH = PROMPT_DIR / "mcp" / "implementation" / "tool_selection_prompt.txt"

# tool name -> required integer argument names. The single source of truth
# for what this bridge will forward, direct or LLM-selected.
TOOL_ARGUMENTS = {
    "list_activities": (),
    "get_activity": ("activity_id",),
    "get_activity_assignments": ("activity_id",),
    "list_assignments": (),
    "get_assignment": ("assignment_id",),
}


class MCPServiceError(Exception):
    """Raised when the MCP server cannot be reached or returns an unexpected error."""


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


def _positive_int(value):
    if isinstance(value, bool):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    if number < 1 or str(number) != str(value).strip():
        return None
    return number


def _build_arguments(tool_name, raw_arguments):
    """Returns (arguments, error): only the tool's required arguments, each a
    positive integer. Unknown extra keys are dropped rather than forwarded."""
    arguments = {}
    for name in TOOL_ARGUMENTS[tool_name]:
        number = _positive_int((raw_arguments or {}).get(name))
        if number is None:
            return None, f"{name} is required and must be a positive integer"
        arguments[name] = number
    return arguments, None


def _mcp_disabled_response():
    return jsonify({"status": "error", "error": "MCP mode is disabled."}), 403


def _result_status_code(result):
    """200 on success; 404 when the tool reported a missing record (matching
    /api/view_activity's convention); 502 for any other tool failure."""
    if result.get("status") == "success":
        return 200
    error = (result.get("result") or {}).get("error", "")
    return 404 if error.endswith("not found") else 502



def _run_tool_route(tool_name):
    if not mcp_mode_is_enabled(request):
        return _mcp_disabled_response()

    body = request.get_json(silent=True) or {}
    arguments, error = _build_arguments(tool_name, body)
    if error:
        return jsonify({"status": "error", "error": error}), 400

    # tool + arguments echoed back so callers see exactly what was forwarded
    # (validated ints, extra keys dropped), matching /ask's response shape.
    call = {"tool": tool_name, "arguments": arguments}
    try:
        result = call_tool(tool_name, arguments)
    except MCPServiceError as exc:
        return jsonify({"status": "error", "error": f"MCP server unavailable: {exc}", **call}), 502
    return jsonify({**result, **call}), _result_status_code(result)


@mcp_mode_bp.route("/api/activity/mcp/list-activities", methods=["POST"])
def mcp_list_activities():
    return _run_tool_route("list_activities")


@mcp_mode_bp.route("/api/activity/mcp/activity", methods=["POST"])
def mcp_get_activity():
    return _run_tool_route("get_activity")


@mcp_mode_bp.route("/api/activity/mcp/activity-assignments", methods=["POST"])
def mcp_get_activity_assignments():
    return _run_tool_route("get_activity_assignments")


@mcp_mode_bp.route("/api/activity/mcp/list-assignments", methods=["POST"])
def mcp_list_assignments():
    return _run_tool_route("list_assignments")


@mcp_mode_bp.route("/api/activity/mcp/assignment", methods=["POST"])
def mcp_get_assignment():
    return _run_tool_route("get_assignment")


def _strip_code_fence(raw):
    match = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", raw.strip(), re.DOTALL)
    return match.group(1) if match else raw.strip()


def parse_tool_selection(raw):
    """Returns (selection, problem). selection is {"tool", "arguments"}, with
    tool None when the model said no tool fits. problem is a user-facing
    reason no tool will be called, or None when selection is callable."""
    try:
        parsed = json.loads(_strip_code_fence(raw))
    except (json.JSONDecodeError, ValueError):
        return None, "the model's tool selection could not be read as JSON"
    if not isinstance(parsed, dict):
        return None, "the model's tool selection was not a JSON object"

    tool = parsed.get("tool")
    if tool in (None, "none"):
        return {"tool": None, "arguments": {}}, "no registered tool matches this request"
    if tool not in TOOL_ARGUMENTS:
        return None, f"the model selected an unknown tool: {tool!r}"

    raw_arguments = parsed.get("arguments")
    if raw_arguments is not None and not isinstance(raw_arguments, dict):
        return None, "the model's arguments were not a JSON object"

    arguments, error = _build_arguments(tool, raw_arguments)
    if error:
        return {"tool": tool, "arguments": raw_arguments or {}}, error
    return {"tool": tool, "arguments": arguments}, None


def load_tool_selection_prompt():
    return TOOL_SELECTION_PROMPT_PATH.read_text(encoding="utf-8").strip()


@mcp_mode_bp.route("/api/activity/mcp/ask", methods=["POST"])
def mcp_ask():
    if not mcp_mode_is_enabled(request):
        return _mcp_disabled_response()

    body = request.get_json(silent=True) or {}
    message = body.get("message", "")
    if not isinstance(message, str) or not message.strip():
        return jsonify({"status": "error", "error": "message is required"}), 400
    message = message.strip()

    try:
        prompt = load_tool_selection_prompt()
    except OSError as exc:
        return jsonify({"status": "error", "error": f"tool selection prompt unavailable: {exc}"}), 500

    logger.info("[MCP ASK] Selecting tool for message: %r", message)
    try:
        raw = ai_client._generate(
            f"{prompt}\n\nUser request: {message}", temperature=0.0, model=ai_client.OLLAMA_INTENT_MODEL
        )
    except ai_client.AIServiceError as exc:
        return jsonify({"status": "error", "error": f"AI tool selection unavailable: {exc}"}), 502

    selection, problem = parse_tool_selection(raw)
    logger.info("[MCP ASK] selection=%s problem=%s", selection, problem)
    if problem:
        return jsonify({
            "status": "no_tool_called",
            "error": problem,
            "tool": (selection or {}).get("tool"),
            "arguments": (selection or {}).get("arguments", {}),
            "model_output": raw,
        }), 200

    try:
        result = call_tool(selection["tool"], selection["arguments"])
    except MCPServiceError as exc:
        return jsonify({
            "status": "error",
            "error": f"MCP server unavailable: {exc}",
            "tool": selection["tool"],
            "arguments": selection["arguments"],
            "model_output": raw,
        }), 502

    return jsonify({
        **result,
        "tool": selection["tool"],
        "arguments": selection["arguments"],
        "model_output": raw,
    }), _result_status_code(result)
