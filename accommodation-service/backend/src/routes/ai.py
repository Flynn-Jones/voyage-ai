import json
import logging
import time
from typing import Any

from flask import Blueprint, jsonify, request

from services import database_api, llm_client

bp = Blueprint("ai", __name__)
logger = logging.getLogger(__name__)


def _plan_request(payload: dict[str, Any]) -> dict[str, Any]:
    city = (payload.get("destination_city") or "").strip()
    interests = payload.get("interests") or []
    if isinstance(interests, str):
        interests = [interests]
    interests = [str(item).strip().lower() for item in interests if str(item).strip()]
    max_price = payload.get("max_price")
    if max_price is not None:
        try:
            max_price = float(max_price)
        except (TypeError, ValueError):
            max_price = None
    plan = {
        "destination_city": city,
        "max_price": max_price,
        "interests": interests,
    }
    logger.info("[PLAN] Derived plan: %s", plan)
    print(f"[PLAN] Derived plan: {plan}")
    return plan


def _query_filters(plan: dict[str, Any]) -> dict[str, Any]:
    filters: dict[str, Any] = {}
    if plan.get("destination_city"):
        filters["destination_city"] = plan["destination_city"]
    if plan.get("max_price") is not None:
        filters["max_price"] = str(plan["max_price"])
    return filters


def _rank_records(records: list[dict[str, Any]], plan: dict[str, Any]) -> list[dict[str, Any]]:
    if not records:
        return []

    try:
        ranked = llm_client.rank_accommodations(plan, records)
    except llm_client.LLMServiceError:
        logger.exception("[OBSERVE] LLM ranking failed; falling back to original record order")
        return records

    ranking_by_id = {item.get("id"): item for item in ranked if item.get("id") is not None}
    if not ranking_by_id:
        return records

    ordered = []
    for record in records:
        record_id = record.get("id")
        if record_id in ranking_by_id:
            score = ranking_by_id[record_id].get("score")
            ordered.append({**record, "_llm_rank": float(score) if isinstance(score, (int, float)) else 0.0})

    ordered.sort(key=lambda item: item["_llm_rank"], reverse=True)
    return [{k: v for k, v in item.items() if k != "_llm_rank"} for item in ordered]


@bp.get("/ai/health")
def ai_health():
    status = llm_client.get_ollama_status()
    return jsonify(status)


@bp.post("/ai/recommend")
def ai_recommend():
    payload = request.get_json(silent=True) or {}
    plan = _plan_request(payload)
    filters = _query_filters(plan)

    logger.info("[ACT] Fetching accommodations with filters: %s", filters)
    print(f"[ACT] Fetching accommodations with filters: {filters}")
    try:
        records = database_api.list_accommodations(filters)
    except database_api.DatabaseServiceError as exc:
        logger.exception("[ACT] Database lookup failed: %s", exc)
        return jsonify({"error": "Accommodation database is unavailable."}), 503

    if isinstance(records, dict):
        records = records.get("data", [])
    logger.info("[ACT] Received %d record(s)", len(records))
    print(f"[ACT] Received {len(records)} record(s)")

    shortlist = _rank_records(records, plan)
    logger.info("[OBSERVE] Shortlist before LLM: %s", [{"id": r.get("id"), "name": r.get("name")} for r in shortlist])
    print(f"[OBSERVE] Shortlist before LLM: {[{'id': r.get('id'), 'name': r.get('name')} for r in shortlist]}")

    if not shortlist:
        return jsonify({
            "plan": json.dumps(plan, ensure_ascii=False),
            "act_result_count": len(records),
            "observation": "No accommodation matched the interest keywords after filtering.",
            "recommendation": {"id": None, "name": None, "reason": "No accommodation matched the user's stated interests; please broaden the search."},
        })

    prompt_payload = {
        "destination_city": plan.get("destination_city"),
        "max_price": plan.get("max_price"),
        "interests": plan.get("interests") or [],
    }
    llm_start = time.perf_counter()
    logger.info("[ADAPT] Calling Ollama with shortlist of %d item(s)", len(shortlist))
    print(f"[ADAPT] Calling Ollama with shortlist of {len(shortlist)} item(s)")

    try:
        recommendation = llm_client.recommend_accommodation(prompt_payload, shortlist)
    except llm_client.LLMServiceError as exc:
        logger.exception("[ADAPT] Ollama recommendation failed: %s", exc)
        return jsonify({"error": "Accommodation recommendation service is unavailable."}), 503

    recommendations = recommendation.get("recommendations") if isinstance(recommendation, dict) else []
    if not isinstance(recommendations, list):
        recommendations = []
    if not recommendations and isinstance(recommendation, dict) and recommendation.get("id") is not None:
        recommendations = [recommendation]

    elapsed = time.perf_counter() - llm_start
    logger.info("[ADAPT] Ollama completed in %.2f seconds", elapsed)
    print(f"[ADAPT] Ollama completed in {elapsed:.2f} seconds")

    primary_recommendation = recommendations[0] if recommendations else (recommendation if isinstance(recommendation, dict) else {})

    result = {
        "plan": json.dumps(plan, ensure_ascii=False),
        "act_result_count": len(records),
        "observation": f"Matched {len(shortlist)} candidate(s) from the requested city and price window using the supplied interests.",
        "recommendation": primary_recommendation,
        "recommendations": recommendations,
    }
    return jsonify(result)
