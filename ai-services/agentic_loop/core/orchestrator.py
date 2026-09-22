"""Runs one agentic_loop validation mode: OBSERVE (collector) -> LLM implementation -> LLM review."""
from pathlib import Path

from collectors import mcp_collector, rag_collector
from core import ai, prompts
from core.reporter import stage
from pipelines import mcp_pipeline, rag_pipeline

COLLECTORS = {
    "mcp": mcp_collector.collect,
    "rag": rag_collector.collect,
}

PIPELINES = {
    "mcp": mcp_pipeline,
    "rag": rag_pipeline,
}


def run_mode(mode, repo_root: Path, app_dir: Path) -> str:
    stage(mode.label, "START", "Starting review flow")

    stage(mode.label, "OBSERVE", "Collecting evidence")
    collector = COLLECTORS[mode.key]
    ok, evidence = collector(app_dir, repo_root)
    stage(mode.label, "OBSERVE", "Complete" if ok else "Incomplete")
    if not ok:
        return f"OBSERVE FAILED: {evidence}"

    pipeline = PIPELINES[mode.key]

    stage(mode.label, "PROMPTS", f"Loading prompt family: {mode.prompt_family}")
    task_prompt = prompts.read(mode.prompt_family, mode.implementation_prompts[0])
    system_prompt = (
        f"You are a precise {mode.label} integration validator. "
        "Use only supplied evidence and reply in at most 40 words."
    )
    implementation_user_prompt = pipeline.build_implementation_prompt(task_prompt, evidence)
    stage(mode.label, "PROMPTS", f"Loaded {mode.label} implementation prompt")

    stage(mode.label, "LLM", f"Running {mode.label} implementation model")
    implementation_output, err = ai.call(system_prompt, implementation_user_prompt, review=False)
    if err:
        stage(mode.label, "LLM", "Failed")
        return f"OBSERVE: {evidence}\n\nMODEL FAILED: {err}"
    stage(mode.label, "LLM", f"{mode.label} implementation model complete")

    review_prompt_text = "\n\n".join(prompts.read(mode.prompt_family, path) for path in mode.review_prompts)
    review_user_prompt = pipeline.build_review_prompt(implementation_output, evidence)
    stage(mode.label, "PROMPTS", f"Loaded {mode.label} review prompt(s)")

    stage(mode.label, "LLM", f"Running {mode.label} review model")
    review_output, review_err = ai.call(review_prompt_text, review_user_prompt, review=True)
    if review_err:
        review_output = review_err
        stage(mode.label, "LLM", "Review model failed")
    else:
        stage(mode.label, "LLM", "Review model complete")

    stage(mode.label, "DONE", "Review complete")

    return f"OBSERVE: {evidence}\n\nIMPLEMENTATION: {implementation_output}\nREVIEW: {review_output}"
