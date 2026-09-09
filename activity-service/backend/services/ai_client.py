"""Client wrapping calls to the team's approved LLM (Llama via Ollama):
activity summaries (generate_summary) and the Plan/Act/Observe/Adapt-grounded
activity chat (extract_intent / filter_candidates / classify_candidates /
generate_grounded_reply / format_no_match_reply / format_ambiguous_reply)."""
import json
import os
import re

import requests

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "llama3.1:8b")
# Plan-step intent extraction needs to reliably follow a structured-output
# instruction; OLLAMA_MODEL is tuned for speed on the final response instead
# and was observed hallucinating constraints the user never mentioned (see
# extract_intent). Defaults to a stronger model than OLLAMA_MODEL for this
# one call — override independently if needed.
OLLAMA_INTENT_MODEL = os.environ.get("OLLAMA_INTENT_MODEL", "qwen2.5:7b")


class AIServiceError(Exception):
    """Raised when the AI service is unreachable or returns an unexpected error."""


def _generate(prompt, temperature=None, model=None):
    payload = {
        "model": model or OLLAMA_MODEL,
        "prompt": prompt,
        "stream": False,
    }
    if temperature is not None:
        payload["options"] = {"temperature": temperature}
    try:
        response = requests.post(f"{OLLAMA_URL}/api/generate", json=payload, timeout=30)
    except requests.exceptions.RequestException as exc:
        raise AIServiceError(str(exc)) from exc

    if response.status_code != 200:
        raise AIServiceError(f"Ollama returned {response.status_code}")

    body = response.json()
    text = body.get("response", "").strip()
    if not text:
        raise AIServiceError("Ollama returned an empty response")
    return text


def _build_summary_prompt(activity):
    return (
        "Write a short, one-to-two sentence summary of this travel activity "
        "for a trip itinerary app.\n"
        f"Name: {activity.get('activity_name')}\n"
        f"Type: {activity.get('activity_type')}\n"
        f"Cost: {activity.get('activity_cost')}\n"
        f"Duration: {activity.get('duration')}\n"
    )


def generate_summary(activity):
    return _generate(_build_summary_prompt(activity))


DEFAULT_INTENT = {"max_cost": None, "max_duration_hours": None, "category": None, "keywords": []}

_INTENT_INSTRUCTIONS = (
    "You extract structured search constraints from a user's question about "
    "travel activities. Respond with ONLY a single JSON object and nothing "
    "else — no explanation, no markdown fences — matching exactly this shape:\n"
    '{"max_cost": <number or null>, "max_duration_hours": <number or null>, '
    '"category": <string or null>, "keywords": [<string>, ...]}\n'
    "Use null (or [] for keywords) for anything the user didn't mention. Pull "
    "any dollar amount into max_cost and any time span into "
    "max_duration_hours as plain numbers — never leave them as text inside "
    "keywords. keywords is only for topic/activity-type words that aren't a "
    "cost or duration.\n\n"
    "Examples:\n"
    'Q: "What'"'"'s a good half-day activity under $50?"\n'
    'A: {"max_cost": 50, "max_duration_hours": 4, "category": null, "keywords": []}\n'
    'Q: "Something cheap and adventurous, maybe under $30"\n'
    'A: {"max_cost": 30, "max_duration_hours": null, "category": "adventure", "keywords": []}\n'
    'Q: "Tell me about the aquarium"\n'
    'A: {"max_cost": null, "max_duration_hours": null, "category": null, "keywords": ["aquarium"]}\n\n'
    "User question: "
)


def extract_intent(message):
    """Plan step: ask the LLM to pull structured search constraints out of the
    user's raw message. Returns (intent, used_fallback) — used_fallback is
    True when the model's reply couldn't be read as the expected JSON shape,
    in which case intent is the unconstrained default (search everything)
    rather than something invented from a failed parse. Raises AIServiceError
    only when the model itself is unreachable — a genuinely distinct failure
    from "responded, but not with parseable JSON"."""
    raw = _generate(_INTENT_INSTRUCTIONS + message, temperature=0.0, model=OLLAMA_INTENT_MODEL)

    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return dict(DEFAULT_INTENT), True

    if not isinstance(parsed, dict):
        return dict(DEFAULT_INTENT), True

    keywords = parsed.get("keywords")
    return {
        "max_cost": _coerce_number(parsed.get("max_cost")),
        "max_duration_hours": _coerce_number(parsed.get("max_duration_hours")),
        "category": parsed.get("category") if isinstance(parsed.get("category"), str) else None,
        "keywords": [k for k in keywords if isinstance(k, str)] if isinstance(keywords, list) else [],
    }, False


def _coerce_number(value):
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value
    return None


_DURATION_NUMBER_RE = re.compile(r"(\d+(?:\.\d+)?)")


def _parse_duration_hours(duration_text):
    """Best-effort parse of the free-text `duration` field (e.g. "2 hours",
    "45 minutes", "half-day") into a number of hours. Returns None when it
    can't confidently parse — callers must fail open (keep the activity)
    rather than treat an unparsed duration as violating a constraint."""
    if not duration_text:
        return None
    text = duration_text.strip().lower()

    if "half" in text and "day" in text:
        return 4.0
    if "full" in text and "day" in text:
        return 8.0

    match = _DURATION_NUMBER_RE.search(text)
    if not match:
        return None
    value = float(match.group(1))

    if "min" in text:
        return value / 60.0
    return value  # default unit: hours


def filter_candidates(activities, intent):
    """Act step: filter the real, already-fetched activity list using the
    Plan step's extracted constraints. Pure Python — no LLM call, no network
    call (fetching `activities` is the caller's job, via
    db_client.get_activities()). A constraint that can't be evaluated for a
    given activity (e.g. unparseable duration text) fails open — kept, not
    dropped on a guess."""
    max_cost = intent.get("max_cost")
    max_duration_hours = intent.get("max_duration_hours")
    category = (intent.get("category") or "").strip().lower()
    keywords = [k.strip().lower() for k in (intent.get("keywords") or []) if k.strip()]

    results = []
    for activity in activities:
        if max_cost is not None:
            cost = activity.get("activity_cost")
            if isinstance(cost, (int, float)) and cost > max_cost:
                continue

        if max_duration_hours is not None:
            hours = _parse_duration_hours(activity.get("duration"))
            if hours is not None and hours > max_duration_hours:
                continue

        if category:
            activity_type = (activity.get("activity_type") or "").lower()
            if category not in activity_type:
                continue

        if keywords:
            haystack = f"{activity.get('activity_name', '')} {activity.get('activity_type', '')}".lower()
            if not any(kw in haystack for kw in keywords):
                continue

        results.append(activity)

    return results


def classify_candidates(candidates, many_threshold=5):
    """Observe step: classify what the Act step actually found, so Adapt
    doesn't skip straight to generating a response without checking. Returns
    {"status": "none"|"single"|"many", "count": n, "candidates": [...]}.
    "single" covers 1..many_threshold results — few enough to ground a
    direct answer in; "many" means too many to pick from without asking the
    user to narrow it down."""
    count = len(candidates)
    if count == 0:
        status = "none"
    elif count <= many_threshold:
        status = "single"
    else:
        status = "many"
    return {"status": status, "count": count, "candidates": candidates}


def _format_cost(activity):
    cost = activity.get("activity_cost")
    return f"${cost:g}" if isinstance(cost, (int, float)) else "cost unknown"


def _build_grounded_prompt(message, candidates):
    lines = [
        f"- {a.get('activity_name')} | type: {a.get('activity_type')} | "
        f"cost: {_format_cost(a)} | duration: {a.get('duration')}"
        for a in candidates
    ]
    return (
        "You are a helpful assistant for a travel activity planning app. "
        "Answer the user's question using ONLY the activities listed below. "
        "Do not mention, describe, or invent any activity that is not in "
        "this list — if the list doesn't fully answer the question, say so "
        "plainly rather than making something up.\n\n"
        f"Available activities:\n{chr(10).join(lines)}\n\n"
        f"User question: {message}\n"
    )


def generate_grounded_reply(message, candidates):
    """Adapt step (matches found): ask the LLM to answer using only the real
    candidate activities from the Act step — it physically cannot invent an
    activity that isn't in this list, since nothing else is in the prompt."""
    return _generate(_build_grounded_prompt(message, candidates))


def format_no_match_reply(intent, used_fallback=False):
    """Adapt step (zero matches): a deterministic, no-LLM-call reply — no
    real data exists to ground a generated answer in, so there's nothing an
    LLM call here could do except guess. References whichever constraints
    were actually applied and invites the user to relax them, rather than
    inventing a plausible-sounding activity to fill the gap."""
    if used_fallback:
        return (
            "I couldn't find anything, and I wasn't able to pick out clear "
            "search criteria from your message either. Could you try "
            "rephrasing — e.g. with a rough budget or activity type?"
        )

    constraints = []
    if intent.get("max_cost") is not None:
        constraints.append(f"under ${intent['max_cost']:g}")
    if intent.get("max_duration_hours") is not None:
        constraints.append(f"under {intent['max_duration_hours']:g} hours")
    if intent.get("category"):
        constraints.append(f"in the '{intent['category']}' category")
    if intent.get("keywords"):
        constraints.append("matching " + ", ".join(intent["keywords"]))

    if not constraints:
        return "I couldn't find any activities matching that. Could you tell me more about what you're looking for?"

    described = " and ".join(constraints)
    return f"Nothing matched {described}. Want me to check without that limit, or adjust it?"


def format_ambiguous_reply(candidates, top_n=5):
    """Adapt step (too many matches): a deterministic, no-LLM-call reply
    listing the top candidates and asking the user to narrow down, rather
    than picking one arbitrarily and presenting it as *the* answer."""
    shown = candidates[:top_n]
    lines = [f"- {a.get('activity_name')} ({_format_cost(a)}, {a.get('duration')})" for a in shown]
    remainder = len(candidates) - len(shown)
    tail = f"\n...and {remainder} more." if remainder > 0 else ""
    return (
        f"I found {len(candidates)} activities that could match — here are a few:\n"
        f"{chr(10).join(lines)}{tail}\n"
        "Want to narrow it down by budget, duration, or type?"
    )
