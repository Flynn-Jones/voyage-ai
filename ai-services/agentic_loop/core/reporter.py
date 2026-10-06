"""Console output for the agentic_loop CLI: stage tracing and the mode menu."""


def stage(label: str, phase: str, message: str) -> None:
    print(f"[{label}][{phase}] {message}")


def print_menu() -> None:
    print()
    print("=" * 70)
    print("VOYAGEAI SHARED AGENTIC LOOP - VALIDATION MENU")
    print("1 - MCP")
    print("2 - RAG")
    print("3 - Activity RAG")
    print("0 - Exit")
    print("=" * 70)


def print_prompt_map(prompt_map: dict) -> None:
    print("Prompt families:")
    for key, path in prompt_map.items():
        print(f"  {key} -> {path}")


class Trace:
    """Ordered PLAN/ACT/OBSERVE/ADAPT record; each step is printed via stage()."""

    def __init__(self, label: str):
        self.label = label
        self.steps: list[tuple[str, str]] = []

    def step(self, phase: str, message: str) -> None:
        self.steps.append((phase, message))
        stage(self.label, phase, message)

    def text(self) -> str:
        return "\n".join(f"[{self.label}][{phase}] {message}" for phase, message in self.steps)
