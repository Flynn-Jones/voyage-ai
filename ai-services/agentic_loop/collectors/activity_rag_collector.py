"""Executes Activity's RAG tasks against the ONE shared RAG service (scope="activity").

Activity's own rag-server was retired; its records now live in the shared
corpus under metadata.service="activity". This collector drives the live
shared server exactly as activity-service/backend/routes/rag_mode.py does
(POST /retrieve and /answer with scope="activity"), checks the Activity
prompts still exist, and reads the activity row of the shared
retrieval-metrics.md. Like rag_collector, it never imports rag_pipeline and
never refreshes the corpus (operator setup step).
"""
import os
import re
from pathlib import Path

import requests

RAG_SERVICE_URL = os.environ.get("RAG_SERVICE_URL", "http://localhost:7002").rstrip("/")
TIMEOUT_SECONDS = float(os.environ.get("AGENTIC_RAG_TIMEOUT_SECONDS", "130"))
SCOPE = "activity"
CALLER = "agentic_loop"
RETRIEVE_QUERY = "total number of activities"
COUNT_QUERY = "How many activities are there?"
# A record from activity-service/database/seed_mock_data.py (the demo seed).
ANSWER_QUERY = "What does the Harbour Sunset Kayak cost?"


def _post(path, body):
    response = requests.post(f"{RAG_SERVICE_URL}/{path}", json=body, timeout=TIMEOUT_SECONDS)
    return response.status_code, response.json()


def _latest_metrics(metrics_path: Path) -> str:
    if not metrics_path.exists():
        return "retrieval-metrics.md missing (run rag_eval.py)"
    match = re.search(r"^\| activity \| [^|]+ \| \d+ \| ([\d.]+) \| ([\d.na/]+) \|$",
                      metrics_path.read_text(encoding="utf-8"), re.MULTILINE)
    return f"activity mean P@5={match.group(1)}, mean R@5={match.group(2)}" if match else "activity row unreadable"


def collect(app_dir: Path, repo_root: Path) -> tuple[bool, str]:
    shared_rag = repo_root / "ai-services" / "rag-server"
    prompts_dir = repo_root / "activity-service" / "prompts" / "lab8"
    required_paths = [
        shared_rag / "rag_pipeline.py",
        shared_rag / "tool-contracts.md",
        prompts_dir / "implementation" / "rag_implementation_prompt.txt",
        prompts_dir / "review" / "rag_review_prompt.txt",
        prompts_dir / "review" / "rag_reasoning_prompt.txt",
    ]
    missing = [str(path.relative_to(repo_root)) for path in required_paths if not path.exists()]
    if missing:
        return False, "Activity RAG evidence incomplete. Missing: " + ", ".join(missing)
    if 'scope == ACTIVITY_SERVICE' not in (shared_rag / "rag_pipeline.py").read_text(encoding="utf-8"):
        return False, "shared rag_pipeline.py does not route scope=\"activity\""

    base = {"k": 5, "caller": CALLER, "scope": SCOPE}
    try:
        r_status, retrieval = _post("retrieve", {"query": RETRIEVE_QUERY, **base})
        c_status, count = _post("answer", {"query": COUNT_QUERY, **base})
        a_status, answer = _post("answer", {"query": ANSWER_QUERY, **base})
    except (requests.exceptions.RequestException, ValueError) as exc:
        return False, f"Shared RAG service unavailable at {RAG_SERVICE_URL}: {exc}"

    if r_status != 200 or retrieval.get("status") != "success":
        return False, f"scoped retrieve failed: HTTP {r_status} {retrieval.get('error')}"
    if c_status != 200 or count.get("answer_source") != "deterministic":
        return False, f"count question was not answered deterministically: HTTP {c_status} {count}"
    leaked = [r["chunk_id"] for r in retrieval["results"]
              if not r["chunk_id"].startswith(("activity_", "assignment_"))]
    if leaked:
        return False, f"scope=activity returned non-activity chunks: {leaked}"

    top = retrieval["results"][0] if retrieval["results"] else {}
    distance = top.get("distance")
    distance_text = f"{distance:.3f}" if isinstance(distance, (int, float)) else "n/a"
    return True, (
        f"Activity RAG evidence (shared server {RAG_SERVICE_URL}, scope=activity): "
        f"retrieve({RETRIEVE_QUERY!r}) -> HTTP {r_status}, mode={retrieval.get('retrieval_mode')}, "
        f"{len(retrieval['results'])} results, top={top.get('chunk_id')} ({top.get('authority_tier')}, "
        f"distance={distance_text}). "
        f"answer({COUNT_QUERY!r}) -> {count.get('answer')!r} [deterministic, "
        f"citations={[c['chunk_id'] for c in count.get('citations', [])]}]. "
        f"answer({ANSWER_QUERY!r}) -> HTTP {a_status}, status={answer.get('status')}, "
        f"answer_source={answer.get('answer_source')}, answer={answer.get('answer', answer.get('error'))!r}, "
        f"citations={[c['chunk_id'] for c in answer.get('citations', [])]}, "
        f"confidence={answer.get('confidence_category')}. "
        f"Latest eval: {_latest_metrics(shared_rag / 'retrieval-metrics.md')}."
    )
