"""Allowlisted, trip-scoped access to the shared local MCP/RAG services."""
from flask import Blueprint, jsonify, request

from services import release1_api

release1_bp = Blueprint("release1", __name__, url_prefix="/api/itinerary")


def scope(body):
    trip = body.get("trip_reference")
    if not isinstance(trip, str) or not trip.strip() or len(trip) > 120:
        raise ValueError("trip_reference must be a non-empty string of at most 120 characters")
    day = body.get("day")
    if day is not None and (type(day) is not int or day < 1):
        raise ValueError("day must be a positive integer")
    return {"trip_reference": trip.strip(), "day": day}


def handle(kind, action):
    if not release1_api.enabled(kind):
        return jsonify(status="error", error=f"{kind} is disabled"), 403
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify(status="error", error="body must be a JSON object"), 400
    try:
        payload = {} if action == "refresh" else scope(body)
        if kind == "RAG":
            payload.update(caller="itinerary-service")
            if action != "refresh":
                query = body.get("query")
                if not isinstance(query, str) or not query.strip() or len(query) > 1000:
                    raise ValueError("query must be a non-empty string of at most 1000 characters")
                k = body.get("k", 5)
                if type(k) is not int or not 1 <= k <= 20:
                    raise ValueError("k must be an integer from 1 to 20")
                payload.update(query=query.strip(), k=k, scope="itinerary")
    except ValueError as exc:
        return jsonify(status="error", error=str(exc)), 400
    try:
        result = release1_api.post(kind, action, payload)
        if kind == "MCP":
            data = result.get("result")
            if (not isinstance(data, dict) or not isinstance(data.get("items"), list)
                    or type(data.get("count")) is not int or data["count"] != len(data["items"])
                    or data.get("trip_reference") != payload["trip_reference"]
                    or any(not isinstance(i, dict) or i.get("trip_reference") != payload["trip_reference"]
                           or (payload["day"] is not None and i.get("day") != payload["day"])
                           for i in data["items"])):
                raise release1_api.UpstreamError("MCP returned an invalid or out-of-scope result")
        elif action == "answer":
            if (not isinstance(result.get("answer"), str) or not isinstance(result.get("citations"), list)
                    or result.get("confidence_category") not in ("High", "Medium", "Low", "Insufficient")
                    or any(not isinstance(c, dict) or not c.get("chunk_id") or not c.get("source_id")
                           for c in result["citations"])
                    or (result["confidence_category"] != "Insufficient" and not result["citations"])):
                raise release1_api.UpstreamError("RAG returned an invalid grounded response")
        elif action == "retrieve" and not isinstance(result.get("results"), list):
            raise release1_api.UpstreamError("RAG returned invalid retrieval results")
        elif action == "refresh":
            if result.get("itinerary_source_error"):
                raise release1_api.UpstreamError("RAG refresh could not read itinerary records")
            if type(result.get("itinerary_chunk_count")) is not int:
                raise release1_api.UpstreamError("RAG returned an invalid refresh result")
        return jsonify(result)
    except release1_api.UpstreamError as exc:
        return jsonify(status="error", error=str(exc)), 502


@release1_bp.post("/mcp/itinerary")
def mcp_itinerary():
    return handle("MCP", "get_itinerary")


@release1_bp.post("/rag/answer")
def rag_answer():
    return handle("RAG", "answer")


@release1_bp.post("/rag/retrieve")
def rag_retrieve():
    return handle("RAG", "retrieve")


@release1_bp.post("/rag/refresh")
def rag_refresh():
    return handle("RAG", "refresh")
