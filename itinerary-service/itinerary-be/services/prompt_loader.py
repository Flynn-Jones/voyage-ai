"""Load the versioned prompts shipped with the backend image."""
from pathlib import Path


PROMPT_DIRECTORY = Path(__file__).resolve().parents[1] / "prompts" / "review"


def load_prompt(filename):
    return (PROMPT_DIRECTORY / filename).read_text(encoding="utf-8").strip()
