"""Executes supported and unsupported tasks against the shared RAG service (GET /health, POST /answer).

PLAN - route a knowledge question to the shared RAG /answer endpoint.
ACT - POST the real request.
OBSERVE - record status, confidence, citations and retrieval summary.
ADAPT - deterministic checks against the final R1-P02 contract decide PASS/FAIL.
rag_pipeline is never imported and the corpus is never refreshed here (operator setup step).
AGENTIC_ITINERARY_TRIP switches to the opt-in Itinerary evidence
(collectors/itinerary_evidence.py), which drives the Itinerary backend over
live HTTP, including its own RAG refresh.
"""
import os

import requests

from core.reporter import Trace

RAG_SERVICE_URL = os.environ.get("RAG_SERVICE_URL", "http://localhost:7002").rstrip("/")
TIMEOUT_SECONDS = float(os.environ.get("AGENTIC_RAG_TIMEOUT_SECONDS", "130"))
CALLER = "agentic-loop"
K = 5
GROUNDED_CONFIDENCE = ("High", "Medium", "Low")
INSUFFICIENT_ANSWER = "Insufficient evidence to answer this question from the current corpus."
DESTINATION_SOURCE_PREFIX = "destination-db"

TASKS = [
    {
        "name": "supported",
        "question": "Which destination is known for street food and nightlife?",
        "expect": "grounded",
        "mentions": ["Osaka"],
    },
    {
        "name": "unsupported",
        "question": "What is the capital of Mars?",
        "expect": "insufficient",
        "mentions": [],
    },
]


def _is_str(value):
    return isinstance(value, str) and bool(value.strip())


def _retrieved_count(data):
    summary = data.get("retrieval_summary")
    value = summary.get("retrieved_count") if isinstance(summary, dict) else None
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def evaluate(task, http_status, data):
    """Return (None, conclusion) when the task is satisfied, else (reason, detail)."""
    if not isinstance(data, dict):
        return "malformed", "body is not a JSON object"
    status = data.get("status")
    if status == "error":
        error_type = data.get("error_type")
        return f"infrastructure:{error_type}", f"http={http_status} error={data.get('error')}"
    if status not in ("success", "insufficient_context"):
        return "malformed", f"unknown status {status!r}"
    if http_status != 200:
        return "malformed", f"http={http_status} for status {status!r}"
    citations = data.get("citations")
    if not isinstance(citations, list):
        return "malformed", "citations is not a list"
    retrieved = _retrieved_count(data)

    if task["expect"] == "grounded":
        if status != "success":
            return "unsatisfied", f"supported question returned {status}"
        answer = data.get("answer")
        if not _is_str(answer) or answer.strip().lower().startswith("insufficient evidence"):
            return "unsatisfied", "answer missing or an insufficient-evidence answer"
        if data.get("confidence_category") not in GROUNDED_CONFIDENCE:
            return "malformed", f"invalid confidence_category {data.get('confidence_category')!r}"
        if not citations:
            return "unsatisfied", "no citations for a grounded answer"
        if not all(isinstance(c, dict) and _is_str(c.get("chunk_id")) and _is_str(c.get("source_id")) for c in citations):
            return "malformed", "citation missing chunk_id/source_id"
        if not any(c["source_id"].startswith(DESTINATION_SOURCE_PREFIX) for c in citations):
            return "unsatisfied", "no Destination DB citation"
        if retrieved is None or retrieved <= 0 or retrieved != len(citations):
            return "malformed", f"retrieved_count={retrieved} inconsistent with {len(citations)} citations"
        missing = [m for m in task["mentions"] if m.lower() not in answer.lower()]
        if missing:
            return "unsatisfied", f"answer does not mention {missing}"
        return None, "grounded answer with Destination citations accepted"

    # unsupported question: only the insufficient-context contract is acceptable
    if status == "success":
        return "fabrication_risk", "unsupported question produced a success answer"
    if citations:
        return "fabrication_risk", f"insufficient_context carries {len(citations)} citations"
    if data.get("confidence_category") != "Insufficient":
        return "malformed", f"confidence_category={data.get('confidence_category')!r}, expected 'Insufficient'"
    if data.get("answer") != INSUFFICIENT_ANSWER:
        return "fabrication_risk", "answer is not the fixed insufficient-evidence text"
    if retrieved != 0:
        return "fabrication_risk", f"retrieved_count={retrieved}; relevant context was accepted"
    return None, "cannot be answered from the available grounded knowledge; refused rather than fabricating"


def _observe(http_status, data):
    if not isinstance(data, dict):
        return f"http={http_status} body={type(data).__name__}"
    citations = data.get("citations") if isinstance(data.get("citations"), list) else []
    ids = [f"{c.get('chunk_id')}@{c.get('source_id')}" for c in citations if isinstance(c, dict)]
    answer = data.get("answer")
    answer = (answer[:160] + "...") if isinstance(answer, str) and len(answer) > 160 else answer
    return (
        f"http={http_status} status={data.get('status')} error_type={data.get('error_type')} "
        f"confidence={data.get('confidence_category')} citations={len(citations)} {ids} "
        f"retrieval_summary={data.get('retrieval_summary')} answer={answer!r}"
    )


def run_task(task, trace):
    label = task["name"]
    trace.step("PLAN", f"[{label}] question {task['question']!r} needs grounded knowledge retrieval; "
                       f"select shared RAG POST /answer (expect {task['expect']})")
    trace.step("ACT", f"[{label}] POST {RAG_SERVICE_URL}/answer caller={CALLER!r} k={K}")
    try:
        response = requests.post(
            f"{RAG_SERVICE_URL}/answer",
            json={"query": task["question"], "k": K, "caller": CALLER},
            timeout=TIMEOUT_SECONDS,
        )
    except requests.exceptions.RequestException as exc:
        trace.step("OBSERVE", f"[{label}] request failed: {type(exc).__name__}")
        trace.step("ADAPT", f"[{label}] FAIL unavailable: shared RAG did not answer ({type(exc).__name__})")
        return False
    try:
        data = response.json()
    except ValueError:
        data = None
    trace.step("OBSERVE", f"[{label}] {_observe(response.status_code, data)}")
    reason, detail = evaluate(task, response.status_code, data)
    if reason:
        trace.step("ADAPT", f"[{label}] FAIL {reason}: {detail}")
        return False
    trace.step("ADAPT", f"[{label}] PASS: {detail}")
    return True


def collect(app_dir=None, repo_root=None) -> tuple:
    if os.getenv("AGENTIC_ITINERARY_TRIP"):
        # Opt-in Student 5 evidence through the Itinerary backend and the shared RAG service.
        from collectors.itinerary_evidence import collect as collect_itinerary
        return collect_itinerary("RAG")
    trace = Trace("RAG")
    trace.step("PLAN", f"check shared RAG health before running {len(TASKS)} tasks")
    try:
        health = requests.get(f"{RAG_SERVICE_URL}/health", timeout=10)
        body = health.json()
        healthy = health.status_code == 200 and isinstance(body, dict) and body.get("status") == "ok"
        detail = f"http={health.status_code} body={body}"
    except (requests.exceptions.RequestException, ValueError) as exc:
        healthy, detail = False, type(exc).__name__
    trace.step("OBSERVE", f"GET {RAG_SERVICE_URL}/health -> {detail}")
    if not healthy:
        trace.step("ADAPT", "FAIL unavailable: shared RAG unhealthy; tasks not attempted")
        return False, trace.text()
    results = [run_task(task, trace) for task in TASKS]
    return all(results), trace.text()
