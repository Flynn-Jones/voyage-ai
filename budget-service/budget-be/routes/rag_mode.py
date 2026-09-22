"""RAG mode routes: forwards retrieval/grounded-answer calls to the shared local RAG server."""
from flask import Blueprint, jsonify, request

from services import rag_api

rag_mode_bp = Blueprint("rag_mode", __name__)


def _rag_disabled_response():
    return jsonify({"status": "error", "error": "RAG mode is disabled."}), 403


@rag_mode_bp.route("/budget/rag/refresh", methods=["POST"])
def rag_refresh():
    if not rag_api.rag_mode_is_enabled(request):
        return _rag_disabled_response()

    try:
        return jsonify(rag_api.refresh_corpus(caller="budget-service"))
    except rag_api.RAGServiceError as exc:
        return jsonify({"status": "error", "error": f"RAG server unavailable: {exc}"}), 502


@rag_mode_bp.route("/budget/rag/retrieve", methods=["POST"])
def rag_retrieve():
    if not rag_api.rag_mode_is_enabled(request):
        return _rag_disabled_response()

    body = request.get_json(silent=True) or {}
    query = (body.get("query") or "").strip()
    if not query:
        return jsonify({"status": "error", "error": "query is required"}), 400

    try:
        return jsonify(rag_api.retrieve_context(query=query, k=int(body.get("k", 5)), caller="budget-service"))
    except rag_api.RAGServiceError as exc:
        return jsonify({"status": "error", "error": f"RAG server unavailable: {exc}"}), 502


@rag_mode_bp.route("/budget/rag/answer", methods=["POST"])
def rag_answer():
    if not rag_api.rag_mode_is_enabled(request):
        return _rag_disabled_response()

    body = request.get_json(silent=True) or {}
    query = (body.get("query") or "").strip()
    if not query:
        return jsonify({"status": "error", "error": "query is required"}), 400

    try:
        return jsonify(rag_api.answer_question(query=query, k=int(body.get("k", 5)), caller="budget-service"))
    except rag_api.RAGServiceError as exc:
        return jsonify({"status": "error", "error": f"RAG server unavailable: {exc}"}), 502
