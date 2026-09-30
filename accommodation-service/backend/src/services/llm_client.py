"""Ollama client wrapper for accommodation recommendations."""
import json
import os
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
