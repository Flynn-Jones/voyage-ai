"""Prompt builders for the Release 2 review modes (multi-agent, testing, cloud)."""


def build_implementation_prompt(task_prompt: str, evidence: str) -> str:
    return f"""
{task_prompt}

Observed Evidence (deterministic checks; PASS/WARN/GAP/FAIL):
{evidence}

Reply in at most 50 words and stay evidence-based.
""".strip()


def build_review_prompt(implementation_output: str, evidence: str) -> str:
    return f"""
Implementation Recommendations:
{implementation_output}

Observed Evidence (deterministic checks; PASS/WARN/GAP/FAIL):
{evidence}

Review the recommendations against the evidence.

Reply in at most 55 words and stay evidence-based.
""".strip()
