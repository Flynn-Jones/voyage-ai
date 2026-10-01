from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
APP_DIR = BASE_DIR.parent
PROMPT_DIR = APP_DIR / "prompts"
ACCOMMODATION_IMPLEMENTATION_PROMPT_DIR = PROMPT_DIR / "accommodation-service" / "implementation"
ACCOMMODATION_REVIEW_PROMPT_DIR = PROMPT_DIR / "accommodation-service" / "review"


def load_prompt(filename: str) -> str:
    return (PROMPT_DIR / filename).read_text(encoding="utf-8").strip()


def load_accommodation_prompt(filename: str) -> str:
    prompt_dirs = [ACCOMMODATION_IMPLEMENTATION_PROMPT_DIR, ACCOMMODATION_REVIEW_PROMPT_DIR]

    for prompt_dir in prompt_dirs:
        candidate = prompt_dir / filename
        if candidate.exists():
            return candidate.read_text(encoding="utf-8").strip()

    return (ACCOMMODATION_IMPLEMENTATION_PROMPT_DIR / filename).read_text(encoding="utf-8").strip()
