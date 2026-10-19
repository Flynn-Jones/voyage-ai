"""Terminal interface to a running Multi-Agent Server (talks HTTP, never imports the server).

  python cli.py workflows
  python cli.py run example_readonly --input '{"tools": ["list_destinations"]}'
  python cli.py show <run_id>
  python cli.py history <run_id>
  python cli.py review <run_id> --decision partial --approve 1,2 --reject 3 \
      --edit '3:{"x":1}' --note "..."
  python cli.py applied <run_id> --created 10,11 --target budget-db

Add --json to any command for the raw response envelope.
Server URL: --url, else MULTI_AGENT_SERVICE_URL, else http://localhost:7004.
"""
import argparse
import getpass
import json
import os
import sys

import requests

DEFAULT_URL = "http://localhost:7004"
TIMEOUT_SECONDS = 600  # a run waits on three Ollama calls


def _split_ids(value: str | None) -> list[str]:
    return [part.strip() for part in (value or "").split(",") if part.strip()]


def _parse_edit(raw: str) -> tuple[str, dict]:
    item_id, sep, fields = raw.partition(":")
    if not sep or not item_id.strip():
        raise argparse.ArgumentTypeError(f"--edit must look like 'ID:{{\"field\": value}}', got {raw!r}")
    try:
        changes = json.loads(fields)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"--edit {item_id}: invalid JSON: {exc}") from exc
    if not isinstance(changes, dict):
        raise argparse.ArgumentTypeError(f"--edit {item_id}: fields must be a JSON object")
    return item_id.strip(), changes


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="VoyageAI Multi-Agent Server CLI")
    parser.add_argument("--url", default=os.environ.get("MULTI_AGENT_SERVICE_URL", DEFAULT_URL))
    parser.add_argument("--json", action="store_true", help="print the raw JSON response")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("health")
    sub.add_parser("workflows")

    run = sub.add_parser("run")
    run.add_argument("workflow")
    run.add_argument("--input", default="{}", help="workflow input as a JSON object")
    run.add_argument("--requested-by", default=None)

    runs = sub.add_parser("runs")
    runs.add_argument("--workflow", default="")
    runs.add_argument("--limit", type=int, default=20)

    for name in ("show", "history"):
        sub.add_parser(name).add_argument("run_id")

    review = sub.add_parser("review")
    review.add_argument("run_id")
    review.add_argument("--decision", required=True, choices=["approve", "partial", "correct", "reject"])
    review.add_argument("--approve", default="", help="comma-separated item ids")
    review.add_argument("--reject", default="", help="comma-separated item ids")
    review.add_argument("--edit", action="append", type=_parse_edit, default=[],
                        help='ID:{"field": value}; repeatable')
    review.add_argument("--note", default=None)
    review.add_argument("--reviewer", default=None)

    applied = sub.add_parser("applied")
    applied.add_argument("run_id")
    applied.add_argument("--created", default="", help="comma-separated ids the backend created")
    applied.add_argument("--target", required=True, help='e.g. "budget-db"')
    return parser


def request(args) -> tuple[str, str, dict | None]:
    """Map a parsed command to (method, path, body)."""
    if args.command == "health":
        return "GET", "/health", None
    if args.command == "workflows":
        return "GET", "/workflows", None
    if args.command == "run":
        payload = json.loads(args.input)
        if not isinstance(payload, dict):
            raise ValueError("--input must be a JSON object")
        return "POST", f"/workflows/{args.workflow}/runs", {
            "input": payload, "requested_by": args.requested_by or f"cli:{_user()}"}
    if args.command == "runs":
        return "GET", f"/runs?workflow={args.workflow}&limit={args.limit}", None
    if args.command == "show":
        return "GET", f"/runs/{args.run_id}", None
    if args.command == "history":
        return "GET", f"/runs/{args.run_id}/history", None
    if args.command == "review":
        return "POST", f"/runs/{args.run_id}/review", {
            "decision": args.decision,
            "approved_ids": _split_ids(args.approve),
            "rejected_ids": _split_ids(args.reject),
            "edits": dict(args.edit),
            "note": args.note,
            "reviewer": args.reviewer or f"cli:{_user()}",
        }
    if args.command == "applied":
        return "POST", f"/runs/{args.run_id}/applied", {
            "created_ids": _split_ids(args.created), "target": args.target}
    raise ValueError(f"unknown command {args.command}")


def _user() -> str:
    try:
        return getpass.getuser()
    except Exception:
        return "unknown"


# ---- pretty printing ---------------------------------------------------------
def _dump(value) -> str:
    return json.dumps(value, indent=2, default=str)


def _print_items(items):
    for item in items or []:
        rest = {k: v for k, v in item.items() if k != "id"}
        print(f"    [{item.get('id')}] {json.dumps(rest, default=str)}")


def _print_run(run):
    print(f"Run {run.get('run_id')}  workflow={run.get('workflow')}  state={run.get('state')}")
    if run.get("requested_by"):
        print(f"  requested_by: {run['requested_by']}   created: {run.get('created_at')}")
    if run.get("error"):
        print(f"  error: {run['error']}")
    plan = run.get("plan")
    if plan:
        print("  PLAN")
        for step in plan.get("steps", []):
            print(f"    - {step.get('tool', step)} {json.dumps(step.get('args', {})) if isinstance(step, dict) else ''}")
        if plan.get("rationale"):
            print(f"    rationale: {plan['rationale']}")
    work = run.get("work")
    if work:
        print(f"  WORK  {work.get('summary', '')}")
        _print_items(work.get("items"))
    review = run.get("review")
    if review:
        print(f"  REVIEW  risk={review.get('risk')}")
        for finding in review.get("findings", []):
            print(f"    ! {finding}")
        for rec in review.get("recommendations", []):
            print(f"    > {rec}")
    if run.get("decision"):
        decision = run["decision"]
        print(f"  HUMAN  {decision.get('decision')} by {decision.get('reviewer')}: {decision.get('note') or ''}")
    if run.get("applied"):
        print(f"  APPLIED  {run['applied']}")
    if run.get("state") == "awaiting_human_review":
        print(f"\n  Next: python cli.py review {run.get('run_id')} --decision approve|partial|correct|reject")


def pretty(command: str, result) -> None:
    if command == "health":
        for key, value in result.items():
            print(f"{key:18} {value}")
    elif command == "workflows":
        for wf in result:
            print(f"{wf['name']}\n    {wf['description']}\n    tools: {', '.join(wf['allowed_tools'])}"
                  f"   rag: {wf.get('uses_rag')}")
    elif command in ("run", "show"):
        _print_run(result)
    elif command == "runs":
        for run in result:
            print(f"{run['run_id']}  {run['state']:22} {run['workflow']:28} {run['created_at']}")
    elif command == "history":
        for event in result:
            detail = {k: v for k, v in event.get("detail", {}).items() if v not in (None, [], {})}
            print(f"{event['seq']:>3}  {event['ts']}  {event['step']:14} {event.get('agent') or '':9}"
                  f" {event.get('state') or '':22} {json.dumps(detail, default=str)}")
    elif command == "review":
        print(f"Run {result['run_id']} -> {result['state']}")
        print("  Approved items for the backend to apply:")
        _print_items(result.get("approved_items"))
        if result.get("rejected_ids"):
            print(f"  Rejected ids: {', '.join(result['rejected_ids'])}")
    else:
        print(_dump(result))


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        method, path, body = request(args)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    try:
        response = requests.request(method, args.url.rstrip("/") + path, json=body, timeout=TIMEOUT_SECONDS)
        envelope = response.json()
    except requests.RequestException as exc:
        print(f"error: cannot reach the Multi-Agent Server at {args.url}: {exc}", file=sys.stderr)
        return 2
    except ValueError:
        print(f"error: non-JSON response ({response.status_code})", file=sys.stderr)
        return 1

    if args.json:
        print(_dump(envelope))
    elif envelope.get("status") == "success":
        pretty(args.command, envelope.get("result"))
    else:
        print(f"error ({response.status_code}): {_dump(envelope.get('error'))}", file=sys.stderr)
    return 0 if envelope.get("status") == "success" else 1


if __name__ == "__main__":
    sys.exit(main())
