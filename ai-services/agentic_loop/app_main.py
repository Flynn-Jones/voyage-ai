"""CLI entrypoint for the shared VoyageAI agentic_loop.

Run from the repo root:
    python ai-services/agentic_loop/app_main.py

Interactive menu when run with no arguments; non-interactive with --mode:
    python ai-services/agentic_loop/app_main.py --mode {mcp,rag,activity_rag,all} [--no-llm]

Prints the PLAN/ACT/OBSERVE/ADAPT trace and a VERDICT per mode. Exit 0 only if
every selected deterministic validator passes; optional LLM commentary never
changes the verdict. It does not write report files itself.
"""
import argparse
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
    return {"1": "mcp", "2": "rag", "3": "activity_rag"}.get(choice)


def _without_live_trace(text: str, label: str) -> str:
    """Drop trace lines already streamed live by stage(); keep verdict/commentary/other evidence."""
    lines = [ln for ln in text.splitlines() if not ln.startswith(f"[{label}][")]
    return "\n".join(lines).strip()


def _run_selected(mode_keys, use_llm) -> int:
    modes = build_mode_config()
    failed = []
    for key in mode_keys:
        mode = modes[key]
        passed, text = run_mode(mode, repo_root=REPO_ROOT, app_dir=REPO_ROOT, use_llm=use_llm)
        print()
        print(f"=== {mode.label} Result ===")
        print(_without_live_trace(text, mode.label))
        if not passed:
            failed.append(mode.label)
    print()
    print("OVERALL VERDICT " + ("FAIL (" + ", ".join(failed) + ")" if failed else "PASS"))
    return 1 if failed else 0


def _interactive() -> None:
    modes = build_mode_config()
    print_prompt_map({mode.label: str(REPO_ROOT / mode.prompts_dir / mode.prompt_family) for mode in modes.values()})

    while True:
        print_menu()
        choice = input("Choose a validation target: ").strip()

        if choice == "0":
            print("Exiting.")
            return

        mode_key = _menu_choice_to_key(choice)
        if not mode_key:
            print("Invalid choice. Select 0, 1, 2, or 3.")
            continue

        mode = modes[mode_key]
        _, result = run_mode(mode, repo_root=REPO_ROOT, app_dir=REPO_ROOT)

        print()
        print(f"=== {mode.label} Result ===")
        print()
        print(_without_live_trace(result, mode.label))
        print()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="VoyageAI shared agentic_loop validator")
    parser.add_argument("--mode", choices=["mcp", "rag", "activity_rag", "all"])
    parser.add_argument("--no-llm", action="store_true", help="skip optional LLM commentary")
    args = parser.parse_args(argv)

    if args.mode is None:
        _interactive()
        return 0
    keys = ["mcp", "rag"] if args.mode == "all" else [args.mode]
    return _run_selected(keys, use_llm=not args.no_llm)


if __name__ == "__main__":
    sys.exit(main())
