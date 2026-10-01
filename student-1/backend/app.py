"""Standalone entry point for the destination backend API (port 5001).

Session 1 scope proved the backend can reach the database service over the
network. Session 2 adds the /api/destinations CRUD routes, forwarded over
HTTP to the database service. Session 4 adds POST /api/destinations/ai-compare,
which retrieves two destination records through the database API and makes
one grounded Ollama call. This service must never open SQLite directly.
"""
import logging
import os
import sys
import time

import requests
from flask import Flask, jsonify, request

import llm_client
import mcp_client
import rag_client

PORT = int(os.environ.get("PORT", "5001"))
DATABASE_SERVICE_URL = os.environ.get("DATABASE_SERVICE_URL", "http://localhost:6001")

FORWARDED_LIST_PARAMS = ("city", "country", "travel_style")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    stream=sys.stdout,
    force=True,
)
logger = logging.getLogger(__name__)


class DatabaseServiceError(Exception):
    """Raised when the database service cannot be reached or returns an error."""


class NotFoundError(DatabaseServiceError):
    """Raised when the database service returns a 404 for the requested resource."""


class ValidationError(DatabaseServiceError):
    """Raised when the database service rejects a request as invalid (400)."""


def _read_error_message(response):
    try:
        payload = response.json()
    except ValueError:
        payload = None
    if isinstance(payload, dict) and "error" in payload:
        return payload["error"]
    return response.text or "invalid request"


def _request(method, path, **kwargs):
    try:
        response = requests.request(method, f"{DATABASE_SERVICE_URL}{path}", timeout=5, **kwargs)
    except requests.exceptions.RequestException as exc:
        raise DatabaseServiceError(str(exc)) from exc

    if response.status_code == 404:
        raise NotFoundError(_read_error_message(response))
    if response.status_code == 400:
        raise ValidationError(_read_error_message(response))
    if response.status_code >= 400:
        raise DatabaseServiceError(f"database returned {response.status_code} for {path}")

    return response

MCP_FILTER_KEYS = ("city", "country", "travel_style")
FILTER_MAX_LENGTH = 100
QUESTION_MIN_LENGTH = 3
QUESTION_MAX_LENGTH = 500
ERROR_HTTP_STATUS = {
    "unavailable": 503,
    "timeout": 504,
    "tool_error": 502,
    "infrastructure": 502,
    "malformed": 502,
}


def _has_control_chars(value, allow_whitespace=False):
    for ch in value:
        if allow_whitespace and ch in "\n\r\t":
            continue
        if ord(ch) < 32 or ord(ch) == 127:
            return True
    return False


def _client_error_response(exc):
    """Map a shared-service client error to the safe JSON envelope."""
    logger.warning("[SHARED] %s: %s", exc.kind, exc.detail or exc.message)
    status = "infrastructure_error" if exc.kind == "infrastructure" else exc.kind
    body = {"status": status, "error": exc.message}
    if getattr(exc, "error_type", None):
        body["error_type"] = exc.error_type
    return jsonify(body), ERROR_HTTP_STATUS[exc.kind]


def _invalid(message):
    return jsonify({"status": "invalid_request", "error": message}), 400


def create_app():
    app = Flask(__name__)

    @app.route("/")
    def root():
        return jsonify({"service": "destination-backend", "status": "ok"})

    @app.route("/health")
    def health():
        try:
            db_response = _request("GET", "/health")
        except DatabaseServiceError as exc:
            return jsonify(
                {
                    "service": "destination-backend",
                    "status": "unhealthy",
                    "database": {"status": "unreachable", "error": str(exc)},
                }
            ), 502

        return jsonify(
            {
                "service": "destination-backend",
                "status": "ok",
                "database": db_response.json(),
            }
        )

    @app.route("/api/destinations")
    def list_destinations():
        params = {
            key: value
            for key in FORWARDED_LIST_PARAMS
            if (value := request.args.get(key)) is not None
        }
        try:
            response = _request("GET", "/destinations", params=params)
        except DatabaseServiceError as exc:
            return jsonify({"error": "destination database unavailable"}), 503
        return jsonify(response.json())

    @app.route("/api/destinations/<int:destination_id>")
    def get_destination(destination_id):
        try:
            response = _request("GET", f"/destinations/{destination_id}")
        except NotFoundError as exc:
            return jsonify({"error": str(exc)}), 404
        except DatabaseServiceError:
            return jsonify({"error": "destination database unavailable"}), 503
        return jsonify(response.json())

    @app.route("/api/destinations", methods=["POST"])
    def create_destination():
        payload = request.get_json(silent=True) or {}
        try:
            response = _request("POST", "/destinations", json=payload)
        except ValidationError as exc:
            return jsonify({"error": str(exc)}), 400
        except DatabaseServiceError:
            return jsonify({"error": "destination database unavailable"}), 503
        return jsonify(response.json()), 201

    @app.route("/api/destinations/<int:destination_id>", methods=["PUT"])
    def update_destination(destination_id):
        payload = request.get_json(silent=True) or {}
        try:
            response = _request("PUT", f"/destinations/{destination_id}", json=payload)
        except NotFoundError as exc:
            return jsonify({"error": str(exc)}), 404
        except ValidationError as exc:
            return jsonify({"error": str(exc)}), 400
        except DatabaseServiceError:
            return jsonify({"error": "destination database unavailable"}), 503
        return jsonify(response.json())

    @app.route("/api/destinations/<int:destination_id>", methods=["DELETE"])
    def delete_destination(destination_id):
        try:
            _request("DELETE", f"/destinations/{destination_id}")
        except NotFoundError as exc:
            return jsonify({"error": str(exc)}), 404
        except DatabaseServiceError:
            return jsonify({"error": "destination database unavailable"}), 503
        return "", 204

    @app.route("/api/destinations/ai-compare", methods=["POST"])
    def ai_compare_destinations():
        body = request.get_json(silent=True) or {}
        city_a = (body.get("city_a") or "").strip()
        city_b = (body.get("city_b") or "").strip()
        preferences = (body.get("preferences") or "").strip() or "general travel interests"

        logger.info("[PLAN] compare city_a=%r city_b=%r preferences=%r", city_a, city_b, preferences)

        if not city_a or not city_b:
            logger.warning("[PLAN] missing city_a and/or city_b")
            return jsonify({"error": "city_a and city_b are required"}), 400
        if city_a.casefold() == city_b.casefold():
            logger.warning("[PLAN] city_a and city_b are the same destination")
            return jsonify({"error": "choose two different destinations"}), 400

        logger.info("[ACT] retrieving destinations from %s", DATABASE_SERVICE_URL)
        try:
            response = _request("GET", "/destinations")
        except DatabaseServiceError:
            logger.error("[ACT] destination database unavailable")
            return jsonify({"error": "destination database unavailable"}), 503
        all_rows = response.json()

        by_casefold = {row["city"].casefold(): row for row in all_rows}
        row_a = by_casefold.get(city_a.casefold())
        row_b = by_casefold.get(city_b.casefold())

        logger.info(
            "[OBSERVE] retrieved %d destinations; matched %s (id=%s) and %s (id=%s)",
            len(all_rows),
            row_a["city"] if row_a else None, row_a["destination_id"] if row_a else None,
            row_b["city"] if row_b else None, row_b["destination_id"] if row_b else None,
        )

        unknown = city_a if row_a is None else (city_b if row_b is None else None)
        if unknown is not None:
            logger.warning("[OBSERVE] unknown destination: %r", unknown)
            return jsonify({"error": f"unknown destination: {unknown}"}), 404

        messages = llm_client.build_comparison_prompt(row_a, row_b, preferences)
        prompt_chars = sum(len(m["content"]) for m in messages)
        logger.info(
            "[ADAPT] prompt=%d chars, calling ollama model=%s at %s",
            prompt_chars, llm_client.OLLAMA_MODEL, llm_client.OLLAMA_BASE_URL,
        )
        started = time.perf_counter()
        try:
            comparison = llm_client.create_chat_completion(messages, temperature=0.2)
        except llm_client.LLMServiceError as exc:
            logger.error("[ADAPT] ollama unavailable: %s", exc)
            return jsonify({"error": "AI comparison service is unavailable."}), 502
        elapsed = time.perf_counter() - started
        logger.info("[ADAPT] ollama returned %d chars in %.2fs", len(comparison), elapsed)

        return jsonify(
            {
                "comparison": comparison,
                "preferences": preferences,
                "model": llm_client.OLLAMA_MODEL,
                "elapsed_seconds": round(elapsed, 2),
                "destinations": [row_a, row_b],
            }
        )

    @app.route("/api/destinations/mcp-search", methods=["POST"])
    def mcp_search_destinations():
        if not mcp_client.MCP_ENABLED:
            return jsonify({"status": "disabled", "error": "MCP lookup is disabled."}), 503

        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            return _invalid("request body must be a JSON object")
        unknown = sorted(set(body) - set(MCP_FILTER_KEYS))
        if unknown:
            return _invalid(f"unknown field: {unknown[0]}")
        filters = {}
        for key in MCP_FILTER_KEYS:
            value = body.get(key)
            if value is None:
                continue
            if not isinstance(value, str):
                return _invalid(f"{key} must be a string")
            value = value.strip()
            if not value:
                continue
            if len(value) > FILTER_MAX_LENGTH:
                return _invalid(f"{key} must be at most {FILTER_MAX_LENGTH} characters")
            if _has_control_chars(value):
                return _invalid(f"{key} contains invalid characters")
            filters[key] = value

        logger.info("[MCP] list_destinations filters=%r", filters)
        try:
            result = mcp_client.list_destinations(filters)
        except mcp_client.MCPClientError as exc:
            return _client_error_response(exc)
        return jsonify(
            {
                "status": "success",
                "source": "shared-mcp",
                "tool": mcp_client.TOOL_NAME,
                "filters": result["filters"],
                "count": result["count"],
                "destinations": result["destinations"],
            }
        )

    @app.route("/api/destinations/rag-answer", methods=["POST"])
    def rag_answer_destinations():
        if not rag_client.RAG_ENABLED:
            return jsonify({"status": "disabled", "error": "RAG questions are disabled."}), 503

        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            return _invalid("request body must be a JSON object")
        question = body.get("question")
        if not isinstance(question, str):
            return _invalid("question is required")
        question = question.strip()
        if len(question) < QUESTION_MIN_LENGTH:
            return _invalid(f"question must be at least {QUESTION_MIN_LENGTH} characters")
        if len(question) > QUESTION_MAX_LENGTH:
            return _invalid(f"question must be at most {QUESTION_MAX_LENGTH} characters")
        if _has_control_chars(question, allow_whitespace=True):
            return _invalid("question contains invalid characters")

        logger.info("[RAG] answer question_chars=%d", len(question))
        try:
            result = rag_client.answer(question)
        except rag_client.RAGClientError as exc:
            return _client_error_response(exc)
        return jsonify({"question": question, **result})

    return app


app = create_app()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT, debug=False)
