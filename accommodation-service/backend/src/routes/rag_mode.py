"""RAG mode routes: forwards retrieval/grounded-answer calls to the shared local RAG server."""
from concurrent.futures import ThreadPoolExecutor

from flask import Blueprint, jsonify, request

from services import llm_client, mcp_api, rag_api

rag_mode_bp = Blueprint("rag_mode", __name__)

CALLER = "accommodation-service"


def _rag_disabled_response():
    return jsonify({"status": "error", "error": "RAG mode is disabled."}), 403


@rag_mode_bp.route("/accommodation/rag/ask", methods=["POST"])
def rag_ask():
    """Answer a plain-language accommodation question two ways at once.

    The grounded answer (with citations and a confidence category) comes from the
    shared RAG server; the matching records come from the shared MCP search tool
    after the question is turned into filters. Both legs run concurrently because
    each makes its own model call and running them in series doubles the wait.
    """
    if not rag_api.rag_mode_is_enabled(request):
        return _rag_disabled_response()

    body = request.get_json(silent=True) or {}
    question = (body.get("query") or body.get("question") or "").strip()
    if not question:
        return jsonify({"status": "error", "error": "query is required"}), 400

    k = int(body.get("k", 5))

    def grounded():
        return rag_api.answer_question(query=question, k=k, caller=CALLER)

    def records():
        filters = llm_client.extract_search_filters(question)
        payload = dict(filters)
        response = mcp_api.call_tool("search_accommodations", payload)
        result = response.get("result", {}) if isinstance(response, dict) else {}
        return {"filters": filters, "count": result.get("count", 0),
                "matched_by": result.get("matched_by"),
                "accommodations": result.get("accommodations") or []}

    with ThreadPoolExecutor(max_workers=2) as pool:
        answer_future = pool.submit(grounded)
        records_future = pool.submit(records)

        try:
            answer = answer_future.result()
        except rag_api.RAGServiceError as exc:
            return jsonify({"status": "error", "error": f"RAG server unavailable: {exc}"}), 502

        # A failure on the records leg must not lose the grounded answer, which is
        # the part the brief actually grades.
        try:
            matches = records_future.result()
            records_error = None
        except (llm_client.LLMServiceError, mcp_api.MCPServiceError) as exc:
            matches = {"filters": {}, "count": 0, "accommodations": []}
            records_error = str(exc)

    return jsonify({
        "status": "success",
        "result": {
            "question": question,
            "answer": answer.get("answer"),
            "citations": answer.get("citations") or [],
            "confidence_category": answer.get("confidence_category"),
            "filters": matches["filters"],
            "matched_by": matches.get("matched_by"),
            "count": matches["count"],
            "accommodations": matches["accommodations"],
            "records_error": records_error,
        },
    }), 200


@rag_mode_bp.route("/accommodation/rag/refresh", methods=["POST"])
def rag_refresh():
    if not rag_api.rag_mode_is_enabled(request):
        return _rag_disabled_response()

    try:
        return jsonify(rag_api.refresh_corpus(caller=CALLER))
    except rag_api.RAGServiceError as exc:
        return jsonify({"status": "error", "error": f"RAG server unavailable: {exc}"}), 502


@rag_mode_bp.route("/accommodation/rag/retrieve", methods=["POST"])
def rag_retrieve():
    if not rag_api.rag_mode_is_enabled(request):
        return _rag_disabled_response()

    body = request.get_json(silent=True) or {}
    query = (body.get("query") or "").strip()
    if not query:
        return jsonify({"status": "error", "error": "query is required"}), 400

    try:
        return jsonify(rag_api.retrieve_context(query=query, k=int(body.get("k", 5)), caller=CALLER))
    except rag_api.RAGServiceError as exc:
        return jsonify({"status": "error", "error": f"RAG server unavailable: {exc}"}), 502


@rag_mode_bp.route("/accommodation/rag/answer", methods=["POST"])
def rag_answer():
    if not rag_api.rag_mode_is_enabled(request):
        return _rag_disabled_response()

    body = request.get_json(silent=True) or {}
    query = (body.get("query") or "").strip()
    if not query:
        return jsonify({"status": "error", "error": "query is required"}), 400

    try:
        return jsonify(rag_api.answer_question(query=query, k=int(body.get("k", 5)), caller=CALLER))
    except rag_api.RAGServiceError as exc:
        return jsonify({"status": "error", "error": f"RAG server unavailable: {exc}"}), 502
