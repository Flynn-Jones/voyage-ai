"""CLI entrypoint for the shared VoyageAI agentic_loop.

Run from the repo root:
    python ai-services/agentic_loop/app_main.py

Prints OBSERVE/IMPLEMENTATION/REVIEW to the console for each mode; it does not
write report files itself -- copy the printed output into reports/ as the
Release 1 validation evidence.
"""
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
REPO_ROOT = BASE_DIR.parent.parent  # ai-services/agentic_loop -> ai-services -> repo root
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from config.review_config import build_mode_config
from core.orchestrator import run_mode
from core.reporter import print_menu, print_prompt_map


def _menu_choice_to_key(choice: str):
    return {"1": "mcp", "2": "rag"}.get(choice)


def main() -> None:
    modes = build_mode_config()
    print_prompt_map({mode.label: str(REPO_ROOT / "prompts" / mode.prompt_family) for mode in modes.values()})

    while True:
        print_menu()
        choice = input("Choose a validation target: ").strip()

        if choice == "0":
            print("Exiting.")
            return

        mode_key = _menu_choice_to_key(choice)
        if not mode_key:
            print("Invalid choice. Select 0, 1, or 2.")
            continue

        mode = modes[mode_key]
        result = run_mode(mode, repo_root=REPO_ROOT, app_dir=REPO_ROOT)

        print()
        print(f"=== {mode.label} Result ===")
        print()
        print(result)
        print()


if __name__ == "__main__":
    main()
