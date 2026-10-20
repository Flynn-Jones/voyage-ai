"""CLI entrypoint for the shared VoyageAI agentic_loop.

Run from the repo root:
    python ai-services/agentic_loop/app_main.py

Interactive menu when run with no arguments; non-interactive with --mode:
    python ai-services/agentic_loop/app_main.py --mode {mcp,rag,activity_rag,all} [--no-llm]

Prints the PLAN/ACT/OBSERVE/ADAPT trace and a VERDICT per mode. Exit 0 only if
every selected deterministic validator passes; optional LLM commentary never
changes the verdict. Release 1 modes do not write report files.

Release 2 review modes write docs/release-2/agentic-loop/<mode>-<UTC>.md + .jsonl:
    --mode multi-agent-review [--workflow NAME] [--limit N]
    --mode testing-review
    --mode cloud-deployment-review --decision approve|approve-with-conditions|reject
           [--conditions TEXT] [--decided-by NAME]
Review exit codes: 0 PASS, 1 FAIL or INCOMPLETE. For cloud-deployment-review the
human decision decides: 0 approve(-with-conditions), 1 reject, 2 decision missing.
"""
import argparse
import getpass
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
REPO_ROOT = BASE_DIR.parent.parent  # ai-services/agentic_loop -> ai-services -> repo root
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

# E402: these imports need BASE_DIR on sys.path first (the CLI is run as a script from the repo root).
from config.review_config import build_mode_config  # noqa: E402
from core.orchestrator import run_mode  # noqa: E402
from core.reporter import print_menu, print_prompt_map  # noqa: E402


REVIEW_MODES = ("multi-agent-review", "testing-review", "cloud-deployment-review")
DECISIONS = ("approve", "approve-with-conditions", "reject")


def _menu_choice_to_key(choice: str):
    return {"1": "mcp", "2": "rag", "3": "activity_rag", "4": "multi-agent-review",
            "5": "testing-review", "6": "cloud-deployment-review"}.get(choice)


def _user() -> str:
    try:
        return getpass.getuser()
    except Exception:
        return "unknown"


def _make_decider(decision=None, conditions=None, decided_by=None, interactive=None):
    """Human release decision: from flags, else prompted on a TTY, else None (pending)."""
    def decide(result):
        if decision:
            return {"decision": decision, "conditions": conditions, "decided_by": decided_by or _user()}
        if not (interactive if interactive is not None else sys.stdin.isatty()):
            return None
        try:
            return _prompt_decision(result)
        except EOFError:  # e.g. stdin is NUL, which Windows reports as a TTY
            return None
    return decide


def _prompt_decision(result) -> dict:
    """Show the evidence and recommendations, then ask the human for the release decision."""
    print()
    print(result.evidence_text())
    for rec in result.recommendations:
        print(f"  > {rec}")
    for key, text in result.commentary.items():
        print(f"  {key}: {text}")
    while True:
        choice = input(f"Human release decision {list(DECISIONS)}: ").strip().lower()
        if choice in DECISIONS:
            break
        print("Please type one of: " + ", ".join(DECISIONS))
    given = None
    while choice == "approve-with-conditions" and not given:
        given = input("Conditions (required): ").strip()
    who = input(f"Decided by [{_user()}]: ").strip() or _user()
    return {"decision": choice, "conditions": given, "decided_by": who}


def _review_exit_code(mode, result) -> int:
    if mode.requires_decision:
        decided = (result.decision or {}).get("decision")
        if decided in ("approve", "approve-with-conditions"):
            return 0
        return 1 if decided == "reject" else 2
    return 0 if result.verdict == "PASS" else 1


def _run_review(mode, use_llm, options=None, decide=None, out_dir=None) -> int:
    result, (md_path, jsonl_path) = run_review_mode(
        mode, repo_root=REPO_ROOT, use_llm=use_llm, options=options, decide=decide, out_dir=out_dir)
    print()
    print(f"=== {mode.label} Result ===")
    print(result.evidence_text())
    if result.recommendations:
        print("Recommendations:")
        for rec in result.recommendations:
            print(f"  > {rec}")
    for key, text in result.commentary.items():
        print(f"{key} (LLM, advisory): {text}")
    if result.decision is not None:
        conditions = result.decision.get("conditions")
        print(f"HUMAN DECISION {result.decision.get('decision')}" + (f" - conditions: {conditions}" if conditions else ""))
        if str(result.decision.get("decision", "")).startswith("PENDING"):
            print("A human release decision is required: re-run with --decision approve|approve-with-conditions|reject")
    print(f"Report: {md_path}")
    print(f"Validation log: {jsonl_path}")
    print(f"VERDICT {result.verdict}")
    return _review_exit_code(mode, result)


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
            print("Invalid choice. Select 0-6.")
            continue

        mode = modes[mode_key]
        if mode.kind == "review":
            _run_review(mode, use_llm=True, decide=_make_decider(interactive=True))
            continue
        _, result = run_mode(mode, repo_root=REPO_ROOT, app_dir=REPO_ROOT)

        print()
        print(f"=== {mode.label} Result ===")
        print()
        print(_without_live_trace(result, mode.label))
        print()


def _tolerant_console() -> None:
    """LLM text and workflow names can hold any Unicode; a cp1252 Windows pipe must not crash the CLI."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")


def main(argv=None) -> int:
    _tolerant_console()
    parser = argparse.ArgumentParser(description="VoyageAI shared agentic_loop validator")
    parser.add_argument("--mode", choices=["mcp", "rag", "activity_rag", "all", *REVIEW_MODES])
    parser.add_argument("--no-llm", action="store_true", help="skip optional LLM commentary")
    review = parser.add_argument_group("Release 2 review modes")
    review.add_argument("--workflow", help="multi-agent-review: only this workflow")
    review.add_argument("--limit", type=int, default=200, help="multi-agent-review: max runs (default 200)")
    review.add_argument("--decision", choices=DECISIONS, help="cloud-deployment-review: human release decision")
    review.add_argument("--conditions", help="required with --decision approve-with-conditions")
    review.add_argument("--decided-by", help="name recorded with the decision (default: OS user)")
    review.add_argument("--out-dir", type=Path, help="report folder (default docs/release-2/agentic-loop)")
    args = parser.parse_args(argv)
    if args.decision == "approve-with-conditions" and not args.conditions:
        parser.error("--decision approve-with-conditions requires --conditions")

    if args.mode is None:
        _interactive()
        return 0
    if args.mode in REVIEW_MODES:
        mode = build_mode_config()[args.mode]
        options = {"workflow": args.workflow, "limit": args.limit} if args.mode == "multi-agent-review" else {}
        decider = _make_decider(args.decision, args.conditions, args.decided_by)
        return _run_review(mode, use_llm=not args.no_llm, options=options, decide=decider, out_dir=args.out_dir)
    keys = ["mcp", "rag"] if args.mode == "all" else [args.mode]
    return _run_selected(keys, use_llm=not args.no_llm)


if __name__ == "__main__":
    sys.exit(main())
