"""Generic Planner agent: turns validated input into a JSON plan for the Worker."""
from agents.base import AgentResult, LLMJsonAgent


class PlannerAgent(LLMJsonAgent):
    name = "planner"
    system_prompt = (
        "You are the Planner agent in a Planner -> Worker -> Reviewer pipeline. "
        "Produce a plan the Worker can execute. Use only the tools you are told are allowed. "
        "Respond with a single JSON object only, no prose."
    )

    def run(self, workflow, payload: dict) -> AgentResult:
        prompt = (
            f"{workflow.planner_prompt(payload)}\n\n"
            f"Allowed tools: {', '.join(workflow.allowed_tools) or '(none)'}"
        )
        return self._run(
            prompt,
            lambda plan: workflow.validate_plan(plan, payload),
            lambda: workflow.fallback_plan(payload),
        )
