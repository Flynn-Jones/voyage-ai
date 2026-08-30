from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent
APP_DIR = BASE_DIR.parent
PROMPT_DIR = APP_DIR / "prompts"
BUDGET_IMPLEMENTATION_PROMPT_DIR = PROMPT_DIR / "budget-service" / "implementation"
BUDGET_REVIEW_PROMPT_DIR = PROMPT_DIR / "budget-service" / "review"


def load_prompt(filename):
    return (PROMPT_DIR / filename).read_text(encoding="utf-8").strip()


def load_budget_prompt(filename):
    prompt_dirs = [BUDGET_IMPLEMENTATION_PROMPT_DIR, BUDGET_REVIEW_PROMPT_DIR]

    for prompt_dir in prompt_dirs:
        candidate = prompt_dir / filename
        if candidate.exists():
            return candidate.read_text(encoding="utf-8").strip()

    return (BUDGET_IMPLEMENTATION_PROMPT_DIR / filename).read_text(encoding="utf-8").strip()