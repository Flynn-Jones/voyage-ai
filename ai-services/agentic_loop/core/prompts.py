"""Loads prompt text files from the repo-root prompts/ directory, or a mode's own prompts_dir."""
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent  # core -> agentic_loop -> ai-services -> repo root


def read(prompt_family: str, relative_path: str, prompts_dir: str = "prompts") -> str:
    path = REPO_ROOT / prompts_dir / prompt_family / relative_path
    return path.read_text(encoding="utf-8").strip()
