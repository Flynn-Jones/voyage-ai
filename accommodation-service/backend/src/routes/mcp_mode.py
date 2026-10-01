"""MCP mode routes: forwards tool calls to the shared local MCP server."""
import time

from flask import Blueprint, jsonify, request

from services import llm_client, mcp_api

mcp_mode_bp = Blueprint("mcp_mode", __name__)


def _mcp_disabled_response():
    return jsonify({"status": "error", "error": "MCP mode is disabled."}), 403


def _mcp_result_response(result):
    return jsonify(result), 200 if result.get("status") == "success" else 502


@mcp_mode_bp.route("/accommodation/mcp/by-destination", methods=["POST"])
def mcp_accommodation_by_destination():
    if not mcp_api.mcp_mode_is_enabled(request):
        return _mcp_disabled_response()

    body = request.get_json(silent=True) or {}
    destination = (body.get("destination") or "").strip()
    if not destination:
        return jsonify({"status": "error", "error": "destination is required"}), 400

    try:
        return _mcp_result_response(
            mcp_api.call_tool("get_accommodation_by_destination", {"destination": destination})
        )
    except mcp_api.MCPServiceError as exc:
        return jsonify({"status": "error", "error": f"MCP server unavailable: {exc}"}), 502


# Filters are dropped in this order when a search comes back empty: the most
# incidental constraint first, so the traveller keeps what they cared about most
# (destination and budget) for as long as possible. destination, min_price and
# max_price are never dropped.
RELAXATION_ORDER = ("amenities", "min_rating", "accommodation_type", "sort_by")


def _run_search(payload, trace):
    """Call the shared search tool once, recording the invocation in `trace`."""
    started = time.perf_counter()
    entry = {
        "tool": "search_accommodations",
        "server": mcp_api.MCP_SERVICE_URL,
        "arguments": {key: value for key, value in payload.items() if value is not None},
    }
    try:
        response = mcp_api.call_tool("search_accommodations", payload)
    except mcp_api.MCPServiceError as exc:
        entry.update(status="error", error=str(exc),
                     duration_ms=int((time.perf_counter() - started) * 1000))
        trace.append(entry)
        raise

    result = response.get("result", {}) if isinstance(response, dict) else {}
    entry.update(
        status=response.get("status", "error"),
        result_count=result.get("count"),
        matched_by=result.get("matched_by"),
        duration_ms=int((time.perf_counter() - started) * 1000),
    )
    trace.append(entry)
    return response, result


@mcp_mode_bp.route("/accommodation/mcp/search", methods=["POST"])
def mcp_search_accommodations():
    """Search accommodation by filters, or by a plain-language question.

    Send structured filters directly, or {"question": "..."} to have the local
    model turn it into filters first (PLAN) before the tool call (ACT). When a
    search returns nothing the most incidental filter is dropped and the tool is
    called again (ADAPT); every invocation is reported in `tool_calls`.
    """
    if not mcp_api.mcp_mode_is_enabled(request):
        return _mcp_disabled_response()

    body = request.get_json(silent=True) or {}
    question = (body.get("question") or "").strip()

    if question:
        try:
            filters = llm_client.extract_search_filters(question)
        except llm_client.LLMServiceError as exc:
            return jsonify({"status": "error", "error": f"Could not interpret the question: {exc}"}), 502
        interpreted_from = "question"
    else:
        filters = {key: body[key] for key in llm_client.FILTER_KEYS if body.get(key) is not None}
        interpreted_from = "filters"

    payload = dict(filters)
    if body.get("limit") is not None:
        payload["limit"] = body["limit"]

    trace = []
    dropped = []
    try:
        response, result = _run_search(payload, trace)
        if response.get("status") != "success":
            return jsonify(response), 502

        # ADAPT: nothing matched, so relax one filter at a time and try again.
        for key in RELAXATION_ORDER:
            if result.get("count"):
                break
            if key not in payload:
                continue
            payload.pop(key)
            dropped.append(key)
            response, result = _run_search(payload, trace)
            if response.get("status") != "success":
                return jsonify(response), 502
    except mcp_api.MCPServiceError as exc:
        return jsonify({"status": "error", "error": f"MCP server unavailable: {exc}",
                        "tool_calls": trace}), 502

    result["question"] = question or None
    result["interpreted_from"] = interpreted_from
    result["requested_filters"] = filters
    result["dropped_filters"] = dropped
    result["tool_calls"] = trace
    return jsonify({"status": "success", "result": result}), 200


@mcp_mode_bp.route("/accommodation/mcp/draft", methods=["POST"])
def mcp_draft_accommodation():
    """PLAN step: turn a plain-language description into draft record fields.

    Nothing is written here — the draft is returned for a human to review and
    correct in the form before /accommodation/mcp/create is called.
    """
    if not mcp_api.mcp_mode_is_enabled(request):
        return _mcp_disabled_response()

    body = request.get_json(silent=True) or {}
    text = (body.get("text") or "").strip()
    if not text:
        return jsonify({"status": "error", "error": "text is required"}), 400

    try:
        draft = llm_client.draft_accommodation(text)
    except llm_client.LLMServiceError as exc:
        return jsonify({"status": "error", "error": f"Could not interpret the description: {exc}"}), 502

    missing = [
        field for field in ("name", "destination_id", "price_per_night")
        if draft.get(field) in (None, "")
    ]
    return jsonify({
        "status": "success",
        "result": {"text": text, "draft": draft, "missing_required": missing},
    }), 200


@mcp_mode_bp.route("/accommodation/mcp/create", methods=["POST"])
def mcp_create_accommodation():
    """ACT step: create the record through the shared MCP server's write tool."""
    if not mcp_api.mcp_mode_is_enabled(request):
        return _mcp_disabled_response()

    body = request.get_json(silent=True) or {}
    payload = {key: body[key] for key in llm_client.DRAFT_KEYS if body.get(key) not in (None, "", [])}

    missing = [f for f in ("name", "destination_id", "price_per_night") if f not in payload]
    if missing:
        return jsonify({"status": "error", "error": "missing required field(s): " + ", ".join(missing)}), 400

    trace = []
    started = time.perf_counter()
    entry = {"tool": "create_accommodation", "server": mcp_api.MCP_SERVICE_URL, "arguments": payload}
    try:
        response = mcp_api.call_tool("create_accommodation", payload)
    except mcp_api.MCPServiceError as exc:
        entry.update(status="error", error=str(exc), duration_ms=int((time.perf_counter() - started) * 1000))
        trace.append(entry)
        return jsonify({"status": "error", "error": f"MCP server unavailable: {exc}", "tool_calls": trace}), 502

    result = response.get("result", {}) if isinstance(response, dict) else {}
    entry.update(status=response.get("status", "error"), created_id=result.get("id"),
                 duration_ms=int((time.perf_counter() - started) * 1000))
    trace.append(entry)

    if response.get("status") != "success":
        return jsonify({**response, "tool_calls": trace}), 502

    result["tool_calls"] = trace
    return jsonify({"status": "success", "result": result}), 201


@mcp_mode_bp.route("/accommodation/mcp/list-expenses", methods=["POST"])
def mcp_list_expenses():
    """Cross-feature read: budget expenses for a trip, via the shared MCP server."""
    if not mcp_api.mcp_mode_is_enabled(request):
        return _mcp_disabled_response()

    body = request.get_json(silent=True) or {}
    try:
        return _mcp_result_response(
            mcp_api.call_tool("list_expenses", {"trip_reference": body.get("trip_reference")})
        )
    except mcp_api.MCPServiceError as exc:
        return jsonify({"status": "error", "error": f"MCP server unavailable: {exc}"}), 502


@mcp_mode_bp.route("/accommodation/mcp/ci-report", methods=["POST"])
def mcp_ci_report():
    if not mcp_api.mcp_mode_is_enabled(request):
        return _mcp_disabled_response()

    body = request.get_json(silent=True) or {}
    try:
        return _mcp_result_response(
            mcp_api.call_tool("ci_report", {"feature": body.get("feature", "accommodation-service")})
        )
    except mcp_api.MCPServiceError as exc:
        return jsonify({"status": "error", "error": f"MCP server unavailable: {exc}"}), 502
