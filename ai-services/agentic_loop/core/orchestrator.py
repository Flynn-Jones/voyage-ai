"""Runs one agentic_loop validation mode: deterministic collector verdict -> optional LLM commentary.

The collector result is authoritative; the LLM layer only comments on a PASSED trace."""
from pathlib import Path

from collectors import activity_rag_collector, mcp_collector, rag_collector
from core import ai, prompts
from core.reporter import stage
from pipelines import mcp_pipeline, rag_pipeline

COLLECTORS = {
    "mcp": mcp_collector.collect,
    "rag": rag_collector.collect,
    "activity_rag": activity_rag_collector.collect,
}

PIPELINES = {
    "mcp": mcp_pipeline,
    "rag": rag_pipeline,
    "activity_rag": rag_pipeline,
}


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
