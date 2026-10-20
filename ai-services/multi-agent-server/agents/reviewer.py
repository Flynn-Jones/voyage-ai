"""Generic Reviewer agent: risk-rates the Worker's output before human review."""
from agents.base import AgentResult, LLMJsonAgent


class ReviewerAgent(LLMJsonAgent):
    name = "reviewer"
    system_prompt = (
        "You are the Reviewer agent in a Planner -> Worker -> Reviewer pipeline. "
        "Check the Worker's output against its evidence before a human reviews it. "
        'Respond with a single JSON object only: {"risk": "low|medium|high", '
        '"findings": [...], "recommendations": [...]}.'
    )

    def run(self, workflow, payload: dict, plan: dict, work: dict) -> AgentResult:
        return self._run(
            workflow.reviewer_prompt(payload, plan, work),
            workflow.validate_review,
            lambda: workflow.fallback_review(payload, plan, work),
        )
