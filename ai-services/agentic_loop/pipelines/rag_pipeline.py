def build_implementation_prompt(task_prompt: str, evidence: str) -> str:
    return f"""
{task_prompt}

Observed Evidence:
{evidence}

Reply in at most 40 words and stay evidence-based.
""".strip()


def build_review_prompt(implementation_output: str, evidence: str) -> str:
    return f"""
Implementation Assessment:
{implementation_output}

Observed Evidence:
{evidence}

Validate the RAG integration assessment against the evidence.
Identify any gaps or risks in retrieval quality or citation grounding.

Reply in at most 40 words and stay evidence-based.
""".strip()
