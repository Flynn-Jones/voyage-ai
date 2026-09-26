"""Mode registry for the shared agentic_loop.

Release 1 adds the "mcp" and "rag" validation modes described in the brief.
The registry is intentionally a plain dict keyed by mode so a later release
can add more modes (e.g. a Release 0 AI-Mode replay) without touching the
orchestrator's dispatch logic.
"""
from dataclasses import dataclass, field


@dataclass
class ModeConfig:
    key: str
    label: str
    prompt_family: str
    implementation_prompts: tuple
    review_prompts: tuple = field(default_factory=tuple)


def build_mode_config() -> dict:
    return {
        "mcp": ModeConfig(
            key="mcp",
            label="MCP",
            prompt_family="mcp",
            implementation_prompts=("implementation/tool_selection_prompt.txt",),
            review_prompts=("review/integration_review_prompt.txt",),
        ),
        "rag": ModeConfig(
            key="rag",
            label="RAG",
            prompt_family="rag",
            implementation_prompts=("implementation/rag_implementation_prompt.txt",),
            review_prompts=("review/rag_review_prompt.txt", "review/rag_reasoning_prompt.txt"),
        ),
    }
