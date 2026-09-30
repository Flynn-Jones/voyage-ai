"""RAG mode routes: forwards refresh / retrieve / answer to the local RAG server.

Gated like MCP mode (RAG_ENABLED + the X-RAG-Mode header). Paths follow this
backend's /api/activity/<mode>/ prefix rather than a bare /rag/.
"""
from flask import Blueprint, jsonify, request

from services.rag_api import RAGServiceError, call_rag_service, rag_mode_is_enabled

rag_mode_bp = Blueprint("rag_mode", __name__)

MAX_K = 20


def _rag_disabled_response():
    return jsonify({"status": "error", "error": "RAG mode is disabled."}), 403


def _parse_k(body):
    """Returns (k, error). k defaults to 5; must be an integer from 1 to MAX_K."""
    raw = body.get("k", 5)
    if isinstance(raw, bool):
        return None, f"k must be an integer between 1 and {MAX_K}"
    try:
        k = int(str(raw).strip())
    except ValueError:
        return None, f"k must be an integer between 1 and {MAX_K}"
    if not 1 <= k <= MAX_K:
        return None, f"k must be an integer between 1 and {MAX_K}"
    return k, None


def _forward(path, payload):
    try:
        data, status_code = call_rag_service(path, payload)
    except RAGServiceError as exc:
        return jsonify({"status": "error", "error": f"RAG server unavailable: {exc}"}), 503
    # the RAG server's own validation errors stay 400; its tool errors are upstream failures
    return jsonify(data), 502 if status_code >= 500 else status_code


def _query_route(path):
    if not rag_mode_is_enabled(request):
        return _rag_disabled_response()

    body = request.get_json(silent=True) or {}
    query = body.get("query", "")
    if not isinstance(query, str) or not query.strip():
        return jsonify({"status": "error", "error": "query is required"}), 400
    k, error = _parse_k(body)
    if error:
        return jsonify({"status": "error", "error": error}), 400
    return _forward(path, {"query": query.strip(), "k": k, "caller": "backend"})


@rag_mode_bp.route("/api/activity/rag/refresh", methods=["POST"])
def rag_refresh():
    if not rag_mode_is_enabled(request):
        return _rag_disabled_response()
    return _forward("refresh", {"caller": "backend"})


@rag_mode_bp.route("/api/activity/rag/retrieve", methods=["POST"])
def rag_retrieve():
    return _query_route("retrieve")


@rag_mode_bp.route("/api/activity/rag/answer", methods=["POST"])
def rag_answer():
    return _query_route("answer")
