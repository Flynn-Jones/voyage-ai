"""Loads shared prompt text files from the repo-root prompts/ directory."""
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent  # core -> agentic_loop -> ai-services -> repo root
PROMPTS_DIR = REPO_ROOT / "prompts"


def read(prompt_family: str, relative_path: str) -> str:
    path = PROMPTS_DIR / prompt_family / relative_path
    return path.read_text(encoding="utf-8").strip()
