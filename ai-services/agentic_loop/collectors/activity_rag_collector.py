"""Checks activity-service/rag-server/ and runs its RAG tools for execution evidence.

Unlike rag_collector (file and string checks only), this one executes
refresh_corpus, one retrieve_context and one answer_question call, and reads
the latest P@5 / R@5 from retrieval-metrics.md, so the review agents judge
real behaviour rather than the presence of function names.
"""
import importlib.util
import re
import sys
from pathlib import Path

REQUIRED_TOOLS = ["refresh_corpus", "retrieve_context", "answer_question"]
RETRIEVE_QUERY = "total number of activities"
ANSWER_QUERY = "What does the Harbour Kayaking Tour cost?"


def _load_pipeline(rag_server_dir: Path):
    # rag_pipeline is also the name of this loop's pipelines module, hence the alias
    spec = importlib.util.spec_from_file_location("activity_rag_pipeline_check", rag_server_dir / "rag_pipeline.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["activity_rag_pipeline_check"] = module
    spec.loader.exec_module(module)
    return module


def _latest_metrics(metrics_path: Path) -> str:
    if not metrics_path.exists():
        return "retrieval-metrics.md missing (run rag_eval.py)"
    match = re.search(r"Mean P@5: ([\d.]+) · Mean R@5: ([\d.]+)", metrics_path.read_text(encoding="utf-8"))
    return f"mean P@5={match.group(1)}, mean R@5={match.group(2)}" if match else "metrics unreadable"


def collect(app_dir: Path, repo_root: Path) -> tuple[bool, str]:
    service_dir = repo_root / "activity-service"
    rag_server_dir = service_dir / "rag-server"
    prompts_dir = service_dir / "prompts" / "lab8"

    required_paths = [
        rag_server_dir / "rag_pipeline.py",
        rag_server_dir / "rag_server.py",
        rag_server_dir / "rag_http_server.py",
        rag_server_dir / "rag_eval.py",
        rag_server_dir / "tool-contracts.md",
        prompts_dir / "implementation" / "rag_implementation_prompt.txt",
        prompts_dir / "review" / "rag_review_prompt.txt",
        prompts_dir / "review" / "rag_reasoning_prompt.txt",
    ]
    missing = [str(path.relative_to(repo_root)) for path in required_paths if not path.exists()]
    if missing:
        return False, "Activity RAG evidence incomplete. Missing: " + ", ".join(missing)

    pipeline_text = (rag_server_dir / "rag_pipeline.py").read_text(encoding="utf-8")
    missing_tools = [tool for tool in REQUIRED_TOOLS if f"def {tool}" not in pipeline_text]
    if missing_tools:
        return False, "rag_pipeline.py missing required tools: " + ", ".join(missing_tools)

    try:
        pipeline = _load_pipeline(rag_server_dir)
        refresh = pipeline.refresh_corpus(caller="agentic_loop")
        retrieval = pipeline.retrieve_context(RETRIEVE_QUERY, 5, caller="agentic_loop")
        answer = pipeline.answer_question(ANSWER_QUERY, 5, caller="agentic_loop")
    except Exception as exc:
        return False, f"Activity RAG tool execution raised an error: {exc}"

    if refresh.get("status") != "success" or retrieval.get("status") != "success":
        return False, f"Activity RAG tools returned errors: refresh={refresh.get('error')}, retrieve={retrieval.get('error')}"

    top = retrieval["results"][0] if retrieval["results"] else {}
    answer_summary = (
        f"answer_question({ANSWER_QUERY!r}) -> status={answer.get('status')}, "
        f"answer_source={answer.get('answer_source')}, answer={answer.get('answer', answer.get('error'))!r}, "
        f"citations={[c['chunk_id'] for c in answer.get('citations', [])]}, "
        f"confidence={answer.get('confidence_category')}"
    )
    return True, (
        "Activity RAG evidence: activity-service/rag-server/ has rag_pipeline.py, rag_server.py (FastMCP stdio), "
        "rag_http_server.py and rag_eval.py; 3 tools defined. "
        f"refresh_corpus -> chunk_count={refresh['chunk_count']}, tiers={refresh['tier_counts']}, "
        f"embedding_mode={refresh['embedding_mode']}, vector_store={refresh['vector_store_status']}. "
        f"retrieve_context({RETRIEVE_QUERY!r}, k=5) -> retrieval_mode={retrieval['retrieval_mode']}, "
        f"{len(retrieval['results'])} results, top={top.get('chunk_id')} ({top.get('authority_tier')}, "
        f"distance={top.get('distance', 0):.3f}). {answer_summary}. "
        f"Latest eval: {_latest_metrics(rag_server_dir / 'retrieval-metrics.md')}."
    )
