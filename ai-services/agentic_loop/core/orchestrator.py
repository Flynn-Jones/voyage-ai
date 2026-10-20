"""Runs one agentic_loop validation mode: deterministic collector verdict -> optional LLM commentary.

The collector result is authoritative; the LLM layer only comments on a PASSED trace.
Release 2 review modes go through run_review_mode(): structured checks, LLM
recommendations on any verdict (still advisory), an optional human decision, and
report files under docs/release-2/agentic-loop/."""
from pathlib import Path

from collectors import (
    activity_rag_collector,
    cloud_collector,
    mcp_collector,
    multi_agent_collector,
    rag_collector,
    testing_collector,
)
from core import ai, prompts
from core.reporter import stage
from core.review import timestamp, write_reports
from pipelines import mcp_pipeline, rag_pipeline, review_pipeline

COLLECTORS = {
    "mcp": mcp_collector.collect,
    "rag": rag_collector.collect,
    "activity_rag": activity_rag_collector.collect,
}

PIPELINES = {
    "mcp": mcp_pipeline,
    "rag": rag_pipeline,
    "activity_rag": rag_pipeline,
    "multi-agent-review": review_pipeline,
    "testing-review": review_pipeline,
    "cloud-deployment-review": review_pipeline,
}

# Release 2: review(repo_root, **options) -> core.review.ReviewResult
REVIEW_COLLECTORS = {
    "multi-agent-review": multi_agent_collector.review,
    "testing-review": testing_collector.review,
    "cloud-deployment-review": cloud_collector.review,
}

DEFAULT_REPORT_DIR = Path("docs") / "release-2" / "agentic-loop"


def run_mode(mode, repo_root: Path, app_dir: Path, use_llm: bool = True) -> tuple[bool, str]:
    """Returns (passed, text). `passed` comes only from the collector."""
    stage(mode.label, "START", "Starting validation")

    stage(mode.label, "COLLECT", "Running collector")
    collector = COLLECTORS[mode.key]
    ok, evidence = collector(app_dir, repo_root)
    verdict = "PASS" if ok else "FAIL"
    stage(mode.label, "VERDICT", verdict)
    if not ok:
        return False, f"{evidence}\n\nVERDICT FAIL"
    result = f"{evidence}\n\nVERDICT PASS"
    if not use_llm:
        return True, result

    try:
        commentary = _llm_commentary(mode, evidence)
    except Exception as exc:  # optional layer must never alter the deterministic verdict
        stage(mode.label, "LLM", "Failed")
        commentary = f"LLM COMMENTARY UNAVAILABLE (verdict unaffected): {type(exc).__name__}: {str(exc)[:200]}"
    return True, f"{result}\n\n{commentary}"


def _llm_commentary(mode, evidence: str) -> str:
    pipeline = PIPELINES[mode.key]

    stage(mode.label, "PROMPTS", f"Loading prompt family: {mode.prompt_family}")
    task_prompt = prompts.read(mode.prompt_family, mode.implementation_prompts[0], mode.prompts_dir)
    system_prompt = (
        f"You are a precise {mode.label} integration validator. "
        "Use only supplied evidence and reply in at most 40 words."
    )
    implementation_user_prompt = pipeline.build_implementation_prompt(task_prompt, evidence)

    stage(mode.label, "LLM", f"Running {mode.label} implementation model (commentary only)")
    implementation_output, err = ai.call(system_prompt, implementation_user_prompt, review=False)
    if err:
        stage(mode.label, "LLM", "Failed")
        return f"LLM COMMENTARY UNAVAILABLE (verdict unaffected): {err}"

    review_prompt_text = "\n\n".join(prompts.read(mode.prompt_family, path, mode.prompts_dir) for path in mode.review_prompts)
    review_user_prompt = pipeline.build_review_prompt(implementation_output, evidence)

    stage(mode.label, "LLM", f"Running {mode.label} review model (commentary only)")
    review_output, review_err = ai.call(review_prompt_text, review_user_prompt, review=True)
    if review_err:
        review_output = f"LLM REVIEW UNAVAILABLE (verdict unaffected): {review_err}"
    stage(mode.label, "DONE", "Validation complete")

    return f"IMPLEMENTATION (LLM commentary): {implementation_output}\nREVIEW (LLM commentary): {review_output}"


def run_review_mode(mode, repo_root: Path, use_llm: bool = True, options: dict | None = None,
                    decide=None, out_dir: Path | None = None):
    """Release 2 review. Returns (result, (md_path, jsonl_path)).

    Missing evidence becomes gap checks inside the collector, so this never raises for
    absent files or unreachable services. `decide(result)` is called after the evidence
    and recommendations exist and returns the human decision dict, or None (pending)."""
    stage(mode.label, "START", "Starting review")
    stage(mode.label, "COLLECT", "Collecting evidence")
    result = REVIEW_COLLECTORS[mode.key](repo_root, **(options or {}))
    stage(mode.label, "VERDICT", f"{result.verdict} {result.counts()}")

    if use_llm:
        try:
            result.commentary = _review_commentary(mode, result.evidence_text())
        except Exception as exc:  # advisory layer must never break the review
            result.commentary = {"LLM": f"UNAVAILABLE (verdict unaffected): {type(exc).__name__}: {str(exc)[:200]}"}

    stamp = timestamp()
    if mode.requires_decision:
        result.decision = (decide(result) if decide else None) or {"decision": "PENDING (required)"}
        result.decision.setdefault("recorded_at", stamp)

    paths = write_reports(result, out_dir or (repo_root / DEFAULT_REPORT_DIR), stamp)
    stage(mode.label, "REPORT", f"{paths[0]} (+ {paths[1].name})")
    return result, paths


def _review_commentary(mode, evidence: str) -> dict:
    """Implementation model recommends, review model checks it. Runs on any verdict; advisory only."""
    pipeline = PIPELINES[mode.key]
    task_prompt = prompts.read(mode.prompt_family, mode.implementation_prompts[0], mode.prompts_dir)
    system_prompt = f"You are a precise {mode.label} advisor. Use only supplied evidence."
    stage(mode.label, "LLM", "Running implementation model (recommendations, advisory)")
    implementation, err = ai.call(system_prompt, pipeline.build_implementation_prompt(task_prompt, evidence))
    if err:
        return {"LLM": f"UNAVAILABLE (verdict unaffected): {err}"}

    review_prompt = "\n\n".join(prompts.read(mode.prompt_family, p, mode.prompts_dir) for p in mode.review_prompts)
    stage(mode.label, "LLM", "Running review model (advisory)")
    review, review_err = ai.call(review_prompt, pipeline.build_review_prompt(implementation, evidence), review=True)
    return {
        "Implementation recommendations": implementation,
        "Review recommendations": review if not review_err else f"UNAVAILABLE (verdict unaffected): {review_err}",
    }
