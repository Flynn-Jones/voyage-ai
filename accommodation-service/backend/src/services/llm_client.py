"""Ollama client wrapper for accommodation recommendations."""
import json
import os
import re
import time
from typing import Any

import requests

from services import prompt_loader

OLLAMA_HOST = os.environ.get("OLLAMA_HOST", os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434"))
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "qwen2.5:7b")


class LLMServiceError(Exception):
    """Raised when the LLM backend cannot be reached or returns an unexpected response."""


def get_ollama_status():
    try:
        response = requests.get(f"{OLLAMA_HOST}/api/tags", timeout=10)
        data = response.json() if response.status_code == 200 else {}
        models = data.get("models", []) if isinstance(data, dict) else []
        loaded = any(model.get("name") == OLLAMA_MODEL for model in models)
        return {
            "ollama": "reachable" if response.status_code == 200 else "unreachable",
            "model": OLLAMA_MODEL,
            "loaded": loaded,
        }
    except (requests.exceptions.RequestException, OSError):
        return {"ollama": "unreachable", "model": OLLAMA_MODEL, "loaded": False}


def generate_recommendation(prompt: str, model: str | None = None, timeout: int = 180):
    payload = {
        "model": model or OLLAMA_MODEL,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": 0.0},
    }
    try:
        response = requests.post(f"{OLLAMA_HOST}/api/generate", json=payload, timeout=timeout)
    except requests.exceptions.RequestException as exc:
        raise LLMServiceError(str(exc)) from exc

    if response.status_code != 200:
        raise LLMServiceError(f"Ollama returned {response.status_code}: {response.text}")

    try:
        data = response.json()
        return data
    except ValueError as exc:
        raise LLMServiceError(f"Unexpected Ollama response: {response.text}") from exc


def _build_evidence(request_payload: dict, candidates: list[dict], candidates_label: str) -> str:
    return (
        "User request:\n"
        f"- destination_city: {request_payload.get('destination_city', '')}\n"
        f"- max_price: {request_payload.get('max_price', '')}\n"
        f"- interests: {', '.join(request_payload.get('interests', []))}\n\n"
        f"{candidates_label}:\n"
        f"{json.dumps(candidates, ensure_ascii=False)}"
    )


def call_accommodation_agent(
    system_prompt_file: str,
    task_prompt_file: str,
    request_payload: dict,
    candidates: list[dict],
    candidates_label: str = "Candidates",
    timeout: int = 180,
):
    """Layer system + context + task prompt files over the evidence, then call Ollama."""
    system_prompt = prompt_loader.load_accommodation_prompt(system_prompt_file)
    context_prompt = prompt_loader.load_accommodation_prompt("context_prompt.txt")
    task_prompt = prompt_loader.load_accommodation_prompt(task_prompt_file)
    evidence = _build_evidence(request_payload, candidates, candidates_label)

    prompt = f"{system_prompt}\n\n{context_prompt}\n\n{task_prompt}\n\nEvidence:\n{evidence}"
    return generate_recommendation(prompt, timeout=timeout)


ALLOWED_TYPES = {"hotel", "hostel", "ryokan", "apartment", "guesthouse"}
ALLOWED_SORTS = {"price_asc", "price_desc", "rating_desc", "name_asc", "created_at_desc"}
FILTER_KEYS = ("destination", "accommodation_type", "min_price", "max_price",
               "min_rating", "amenities", "sort_by")


def _coerce_number(value, low=None, high=None):
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if low is not None and number < low:
        return None
    if high is not None and number > high:
        return None
    return number


def _sanitise_filters(raw: dict) -> dict:
    """Keep only filters the search tool accepts, with values it can actually use.

    The model is asked for strict JSON but can still return a stray type or an
    out-of-range rating; anything that does not validate is dropped rather than
    passed through to the tool.
    """
    if not isinstance(raw, dict):
        return {}

    destination = raw.get("destination")
    destination = destination.strip() if isinstance(destination, str) and destination.strip() else None

    accommodation_type = raw.get("accommodation_type")
    accommodation_type = accommodation_type.strip().lower() if isinstance(accommodation_type, str) else None
    if accommodation_type not in ALLOWED_TYPES:
        accommodation_type = None

    sort_by = raw.get("sort_by")
    sort_by = sort_by.strip().lower() if isinstance(sort_by, str) else None
    if sort_by not in ALLOWED_SORTS:
        sort_by = None

    amenities = raw.get("amenities")
    if isinstance(amenities, list):
        amenities = ",".join(str(item).strip() for item in amenities if str(item).strip())
    amenities = amenities.strip() if isinstance(amenities, str) and amenities.strip() else None

    filters = {
        "destination": destination,
        "accommodation_type": accommodation_type,
        "min_price": _coerce_number(raw.get("min_price"), low=0),
        "max_price": _coerce_number(raw.get("max_price"), low=0),
        "min_rating": _coerce_number(raw.get("min_rating"), low=0, high=5),
        "amenities": amenities,
        "sort_by": sort_by,
    }
    return {key: value for key, value in filters.items() if value is not None}


ALLOWED_AMENITIES = {
    "24-Hour Front Desk", "Air Conditioning", "Bar", "Breakfast Included", "Gym",
    "Onsen", "Parking", "Pool", "Rooftop Terrace", "WiFi",
}
DRAFT_KEYS = ("name", "destination_city", "destination_id", "accommodation_type",
              "price_per_night", "rating", "location", "description", "amenities")


def _parse_json_object(raw, what):
    """Pull a JSON object out of a model response that may be wrapped in prose."""
    if isinstance(raw, dict):
        return raw
    cleaned = str(raw).replace("```json", "").replace("```", "").strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", cleaned, re.S)
        if not match:
            raise LLMServiceError(f"could not parse {what} JSON from model output: {cleaned!r}")
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError as exc:
            raise LLMServiceError(f"could not parse {what} JSON from model output: {cleaned!r}") from exc


def _sanitise_draft(raw: dict) -> dict:
    """Keep only accommodation fields the create tool accepts, with usable values."""
    if not isinstance(raw, dict):
        return {key: None for key in DRAFT_KEYS} | {"amenities": []}

    def text(key):
        value = raw.get(key)
        return value.strip() if isinstance(value, str) and value.strip() else None

    city = text("destination_city")
    destination_id = text("destination_id")
    if not destination_id and city:
        destination_id = "dest-" + city.lower().replace(" ", "-")

    accommodation_type = raw.get("accommodation_type")
    accommodation_type = accommodation_type.strip().lower() if isinstance(accommodation_type, str) else None
    if accommodation_type not in ALLOWED_TYPES:
        accommodation_type = None

    amenities = raw.get("amenities") or []
    if isinstance(amenities, str):
        amenities = [item.strip() for item in amenities.split(",")]
    # Only amenities the database actually knows about; anything else would be
    # silently dropped at insert time, so drop it here where it is visible.
    amenities = [item for item in (str(a).strip() for a in amenities) if item in ALLOWED_AMENITIES]

    return {
        "name": text("name"),
        "destination_city": city,
        "destination_id": destination_id,
        "accommodation_type": accommodation_type,
        "price_per_night": _coerce_number(raw.get("price_per_night"), low=0),
        "rating": _coerce_number(raw.get("rating"), low=0, high=5),
        "location": text("location"),
        "description": text("description"),
        "amenities": sorted(set(amenities)),
    }


def draft_accommodation(text: str) -> dict:
    """Turn a plain-language description into draft accommodation fields."""
    system_prompt = prompt_loader.load_accommodation_prompt("draft_extraction_system_prompt.txt")
    context_prompt = prompt_loader.load_accommodation_prompt("context_prompt.txt")
    task_prompt = prompt_loader.load_accommodation_prompt("draft_extraction_task_prompt.txt")

    prompt = f"{system_prompt}\n\n{context_prompt}\n\n{task_prompt}\n\nDescription: {text}"
    result = generate_recommendation(prompt, timeout=120)
    if not isinstance(result, dict):
        raise LLMServiceError(f"Unexpected Ollama response shape: {result!r}")

    return _sanitise_draft(_parse_json_object(result.get("response", ""), "accommodation draft"))


def extract_search_filters(question: str) -> dict:
    """Turn a plain-language accommodation question into search-tool filters."""
    system_prompt = prompt_loader.load_accommodation_prompt("filter_extraction_system_prompt.txt")
    context_prompt = prompt_loader.load_accommodation_prompt("context_prompt.txt")
    task_prompt = prompt_loader.load_accommodation_prompt("filter_extraction_task_prompt.txt")

    prompt = f"{system_prompt}\n\n{context_prompt}\n\n{task_prompt}\n\nQuestion: {question}"
    result = generate_recommendation(prompt, timeout=120)
    if not isinstance(result, dict):
        raise LLMServiceError(f"Unexpected Ollama response shape: {result!r}")

    raw = result.get("response", "")
    if isinstance(raw, dict):
        return _sanitise_filters(raw)

    cleaned = str(raw).replace("```json", "").replace("```", "").strip()
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        # Models sometimes wrap the object in a sentence; take the first {...} block.
        match = re.search(r"\{.*\}", cleaned, re.S)
        if not match:
            raise LLMServiceError(f"could not parse filter JSON from model output: {cleaned!r}")
        try:
            parsed = json.loads(match.group(0))
        except json.JSONDecodeError as exc:
            raise LLMServiceError(f"could not parse filter JSON from model output: {cleaned!r}") from exc

    return _sanitise_filters(parsed)


def _parse_json_recommendations(value: str) -> Any:
    cleaned = value.replace("```json", "").replace("```", "").strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        decoder = json.JSONDecoder()
        parsed = []
        position = 0
        while position < len(cleaned):
            while position < len(cleaned) and cleaned[position].isspace():
                position += 1
            if position >= len(cleaned):
                break
            try:
                item, end = decoder.raw_decode(cleaned, position)
            except json.JSONDecodeError as exc:
                raise LLMServiceError(f"Unexpected recommendation response: {value!r}") from exc
            parsed.append(item)
            position = end
        return parsed


def rank_accommodations(
    system_prompt_file: str,
    task_prompt_file: str,
    request_payload: dict,
    candidates: list[dict],
) -> list[dict]:
    if not candidates:
        return []

    start = time.perf_counter()
    result = call_accommodation_agent(
        system_prompt_file,
        task_prompt_file,
        request_payload,
        candidates,
        candidates_label="Candidates",
    )
    elapsed = time.perf_counter() - start
    if not isinstance(result, dict):
        raise LLMServiceError(f"Unexpected Ollama response shape: {result!r}")

    response_payload = result.get("response") or result.get("recommendation") or result.get("ranking") or []
    if isinstance(response_payload, str):
        try:
            parsed = json.loads(response_payload)
        except json.JSONDecodeError as exc:
            raise LLMServiceError(f"Unexpected ranking response: {response_payload!r}") from exc
        response_payload = parsed

    if not isinstance(response_payload, list):
        raise LLMServiceError(f"Unexpected ranking response shape: {response_payload!r}")

    ranked = []
    for item in response_payload:
        if not isinstance(item, dict):
            continue
        candidate_id = item.get("id")
        if candidate_id is None:
            continue
        ranked.append({
            "id": candidate_id,
            "name": item.get("name"),
            "score": item.get("score"),
            "reason": item.get("reason"),
            "_response_time_seconds": round(elapsed, 2),
        })

    return ranked


def recommend_accommodation(
    system_prompt_file: str,
    task_prompt_file: str,
    request_payload: dict,
    shortlist: list[dict],
) -> dict:
    start = time.perf_counter()
    result = call_accommodation_agent(
        system_prompt_file,
        task_prompt_file,
        request_payload,
        shortlist,
        candidates_label="Shortlist candidates",
    )
    elapsed = time.perf_counter() - start
    if not isinstance(result, dict):
        raise LLMServiceError(f"Unexpected Ollama response shape: {result!r}")
    recommendation = result.get("response") or result.get("recommendation") or {}
    if isinstance(recommendation, str):
        try:
            recommendation = _parse_json_recommendations(recommendation)
        except LLMServiceError:
            recommendation = {"reason": recommendation}

    raw_items = []
    if isinstance(recommendation, list):
        raw_items = recommendation
    elif isinstance(recommendation, dict):
        nested = recommendation.get("recommendations")
        if isinstance(nested, list):
            raw_items = nested
        elif isinstance(recommendation.get("recommendation"), list):
            raw_items = recommendation["recommendation"]
        else:
            raw_items = [recommendation]

    recommendations = []
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        normalized = dict(item)
        normalized.setdefault("response_time_seconds", round(elapsed, 2))
        recommendations.append(normalized)

    recommendations = recommendations[:3]

    if not recommendations:
        return {"recommendation": {"reason": str(recommendation), "response_time_seconds": round(elapsed, 2)}, "recommendations": []}

    primary = recommendations[0]
    return {"recommendation": primary, "recommendations": recommendations}
