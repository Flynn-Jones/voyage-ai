"""MCP mode routes: forwards tool calls to the shared local MCP server."""
from flask import Blueprint, jsonify, request

from services import mcp_api
from views import mcp_formatters

mcp_mode_bp = Blueprint("mcp_mode", __name__)


def _mcp_disabled_response():
    return jsonify({"status": "error", "error": "MCP mode is disabled."}), 403


def _mcp_result_response(result):
    return jsonify(result), 200 if result.get("status") == "success" else 502


@mcp_mode_bp.route("/budget/mcp/list-expenses", methods=["POST"])
def mcp_list_expenses():
    if not mcp_api.mcp_mode_is_enabled(request):
        return _mcp_disabled_response()

    body = request.get_json(silent=True) or {}
    try:
        return _mcp_result_response(mcp_api.call_tool("list_expenses", {"trip_reference": body.get("trip_reference")}))
    except mcp_api.MCPServiceError as exc:
        return jsonify({"status": "error", "error": f"MCP server unavailable: {exc}"}), 502


@mcp_mode_bp.route("/budget/mcp/accommodation-by-destination", methods=["POST"])
def mcp_accommodation_rates():
    """Look up nightly rates for a destination so a stay can be logged as an expense."""
    if not mcp_api.mcp_mode_is_enabled(request):
        return _mcp_disabled_response()

    body = request.get_json(silent=True) or {}
    destination = (body.get("destination") or "").strip()
    if not destination:
        return jsonify({"status": "error", "error": "destination is required"}), 400

    try:
        response = mcp_api.call_tool("get_accommodation_by_destination", {"destination": destination})
    except mcp_api.MCPServiceError as exc:
        return jsonify({"status": "error", "error": f"MCP server unavailable: {exc}"}), 502

    if response.get("status") != "success":
        return jsonify(response), 502

    rates = mcp_formatters.format_accommodation_rates(response.get("result", {}))
    if "error" in rates:
        return jsonify({"status": "error", "error": rates["error"]}), 502

    return jsonify({"status": "success", "result": rates}), 200


@mcp_mode_bp.route("/budget/mcp/project-files", methods=["POST"])
def mcp_project_files():
    if not mcp_api.mcp_mode_is_enabled(request):
        return _mcp_disabled_response()

    body = request.get_json(silent=True) or {}
    try:
        return _mcp_result_response(
            mcp_api.call_tool("project_files", {"directory_path": body.get("directory_path", ".")})
        )
    except mcp_api.MCPServiceError as exc:
        return jsonify({"status": "error", "error": f"MCP server unavailable: {exc}"}), 502


@mcp_mode_bp.route("/budget/mcp/ci-report", methods=["POST"])
def mcp_ci_report():
    if not mcp_api.mcp_mode_is_enabled(request):
        return _mcp_disabled_response()

    body = request.get_json(silent=True) or {}
    try:
        return _mcp_result_response(mcp_api.call_tool("ci_report", {"feature": body.get("feature", "budget-service")}))
    except mcp_api.MCPServiceError as exc:
        return jsonify({"status": "error", "error": f"MCP server unavailable: {exc}"}), 502
