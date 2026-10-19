"""multi-agent-review: audits Multi-Agent Server runs (Release 2).

OBSERVE  GET /runs (+ /workflows for allowlists) from MULTI_AGENT_SERVICE_URL and
         the append-only audit log (MULTI_AGENT_AUDIT_LOG, default
         ai-services/multi-agent-server/logs/audit.jsonl). If the server is down the
         review continues from the audit log alone and records a gap.
CHECK    every run went Planner → Worker → Reviewer → Human Review (in order), no
         refused or non-allowlisted tool calls, failures, latency and fallback rate.
Only HTTP and the log file are read; nothing from the server is imported.
"""
import json
import os
from collections import defaultdict
from pathlib import Path

import requests

from core.review import ReviewResult

MODE = "multi-agent-review"
AGENT_STEPS = ("plan", "work", "review")
DECIDED_STATES = ("approved", "partially_accepted", "corrected", "rejected", "applied")
TIMEOUT_SECONDS = float(os.environ.get("AGENTIC_TIMEOUT_SECONDS", "15"))


def _service_url() -> str:
    return os.environ.get("MULTI_AGENT_SERVICE_URL", "http://localhost:7004").rstrip("/")


def _audit_path(repo_root: Path) -> Path:
    default = repo_root / "ai-services" / "multi-agent-server" / "logs" / "audit.jsonl"
    return Path(os.environ.get("MULTI_AGENT_AUDIT_LOG", str(default)))


def fetch_json(url: str):
    """Return (result, error). Unwraps the {"status","result"|"error"} envelope."""
    try:
        response = requests.get(url, timeout=TIMEOUT_SECONDS)
        body = response.json()
    except (requests.RequestException, ValueError) as exc:
        return None, f"{type(exc).__name__}: {exc}"
    if not isinstance(body, dict) or body.get("status") != "success":
        return None, f"HTTP {response.status_code}: {body.get('error') if isinstance(body, dict) else body}"
    return body.get("result"), None


def read_audit(path: Path):
    """Return (entries, error). Malformed lines are counted, not fatal."""
    if not path.exists():
        return [], f"audit log not found: {path}"
    entries, bad = [], 0
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                entries.append(json.loads(line))
            except ValueError:
                bad += 1
    return entries, (f"{bad} malformed audit line(s) skipped" if bad else None)


def _percentile(values, pct):
    ordered = sorted(values)
    if not ordered:
        return None
    index = min(len(ordered) - 1, max(0, round(pct / 100 * (len(ordered) - 1))))
    return ordered[index]


def review(repo_root: Path, workflow: str | None = None, limit: int = 200, fetch=fetch_json) -> ReviewResult:
    result = ReviewResult(MODE, "Multi-Agent Review")
    base = _service_url()
    latency_warn_ms = int(os.environ.get("AGENTIC_LATENCY_WARN_MS", "60000"))

    # ---- OBSERVE ------------------------------------------------------------
    query = f"/runs?limit={limit}" + (f"&workflow={workflow}" if workflow else "")
    runs, runs_error = fetch(base + query)
    workflows, wf_error = fetch(base + "/workflows")
    result.sources += [base + query, base + "/workflows"]
    if runs_error:
        result.gap("server_reachable", "multi-agent-server", f"GET /runs failed ({runs_error}); using audit log only")
        runs = None
    allowlists = {w["name"]: set(w.get("allowed_tools", [])) for w in (workflows or []) if isinstance(w, dict)}
    if wf_error and not runs_error:
        result.gap("workflow_allowlists", "multi-agent-server", f"GET /workflows failed ({wf_error}); "
                   "allowlist cross-check skipped")

    audit_path = _audit_path(repo_root)
    result.sources.append(str(audit_path))
    entries, audit_error = read_audit(audit_path)
    if audit_error and not entries:
        result.gap("audit_log", "audit.jsonl", audit_error)
    elif audit_error:
        result.add("audit_log", "audit.jsonl", "warn", audit_error)
    by_run = defaultdict(list)
    for entry in entries:
        if workflow and entry.get("workflow") != workflow:
            continue
        by_run[entry.get("run_id")].append(entry)
    for run_entries in by_run.values():
        run_entries.sort(key=lambda e: e.get("ts") or "")

    if runs is None:  # reconstruct from the audit log
        runs = [{"run_id": rid, "workflow": es[0].get("workflow"), "state": None} for rid, es in by_run.items()]
    if not runs:
        result.gap("runs_found", workflow or "all workflows", "no runs found; start one with "
                   "`python ai-services/multi-agent-server/cli.py run <workflow>`")

    # ---- CHECK each run -----------------------------------------------------
    states = defaultdict(int)
    latencies = defaultdict(list)
    llm_steps = fallbacks = 0
    for run in runs:
        run_id, state, wf = run.get("run_id"), run.get("state"), run.get("workflow")
        states[state or "unknown"] += 1
        subject = f"{wf}/{run_id}"
        run_entries = by_run.get(run_id, [])
        steps = [e.get("step") for e in run_entries]
        for e in run_entries:
            if e.get("agent") in ("planner", "worker", "reviewer") and isinstance(e.get("duration_ms"), (int, float)):
                latencies[e["agent"]].append(e["duration_ms"])
            if e.get("step") in ("plan", "review") and e.get("source"):
                llm_steps += 1
                fallbacks += e.get("source") == "fallback"

        if not run_entries:
            result.gap("audit_entries", subject, "run has no audit entries (log rotated or written elsewhere?)")
            continue
        if state == "running":
            result.add("lifecycle", subject, "warn", "run still in progress")
            continue
        if state == "failed":
            result.add("failure", subject, "warn", f"run failed: {run.get('error') or _last_error(run_entries)}")
        else:
            agent_steps = [s for s in steps if s in AGENT_STEPS]
            if agent_steps[:3] == list(AGENT_STEPS):
                result.add("lifecycle", subject, "pass", "planner -> worker -> reviewer in order")
            else:
                result.add("lifecycle", subject, "fail", f"agent steps were {agent_steps}, expected plan -> work -> review")
            if state in DECIDED_STATES or (state is None and "human_review" in steps):
                if "human_review" in steps:
                    result.add("human_review", subject, "pass", f"human decision recorded ({state or 'from audit'})")
                else:
                    result.add("human_review", subject, "fail", f"state '{state}' but no human_review audit entry")
            elif state == "awaiting_human_review" or state is None:
                result.add("human_review", subject, "warn", "awaiting human review")
            if state == "applied" and "applied" not in steps:
                result.add("applied", subject, "fail", "state 'applied' but no applied audit entry")

        refused = [c.get("tool") for e in run_entries for c in e.get("tools_called") or [] if c.get("refused")]
        allowed = allowlists.get(wf)
        outside = sorted({
            c.get("tool") for e in run_entries for c in e.get("tools_called") or []
            if allowed is not None and not c.get("refused") and c.get("tool")
            and c.get("tool") != "llm" and not str(c.get("tool")).startswith("rag:") and c.get("tool") not in allowed
        })
        if refused:
            result.add("tool_allowlist", subject, "fail", f"refused tool call(s): {sorted(set(refused))}")
        elif outside:
            result.add("tool_allowlist", subject, "fail", f"tool(s) outside allowlist were executed: {outside}")
        else:
            result.add("tool_allowlist", subject, "pass", "no disallowed tool calls")

    # ---- metrics + aggregate checks ----------------------------------------
    total = len(runs)
    failed = states.get("failed", 0)
    result.metrics = {
        "runs": total,
        "by_state": dict(states),
        "failure_rate": round(failed / total, 3) if total else None,
        "fallback_rate": round(fallbacks / llm_steps, 3) if llm_steps else None,
        "latency_ms": {agent: {"p50": _percentile(v, 50), "p95": _percentile(v, 95), "max": max(v), "n": len(v)}
                       for agent, v in sorted(latencies.items())},
    }
    for agent, stats in result.metrics["latency_ms"].items():
        status = "warn" if stats["p95"] > latency_warn_ms else "pass"
        result.add("latency", agent, status, f"p95 {stats['p95']} ms (warn above {latency_warn_ms} ms)")
    if llm_steps:
        rate = result.metrics["fallback_rate"]
        result.add("fallback_rate", "planner+reviewer", "warn" if rate > 0.5 else "pass",
                   f"{fallbacks}/{llm_steps} LLM steps used the deterministic fallback")

    result.recommendations = _recommendations(result)
    return result


def _last_error(entries):
    return next((e.get("error") for e in reversed(entries) if e.get("error")), "unknown error")


def _recommendations(result: ReviewResult) -> list[str]:
    recs = []
    ids = {(c.id, c.status) for c in result.checks}
    if ("tool_allowlist", "fail") in ids:
        recs.append("Implementation: a workflow called a tool outside allowed_tools; either justify and add it to "
                    "allowed_tools (read-only preferred) or remove the call from work().")
    if ("lifecycle", "fail") in ids or ("human_review", "fail") in ids or ("applied", "fail") in ids:
        recs.append("Review: lifecycle evidence is incomplete; check the audit log is not truncated and that "
                    "backends only apply runs after POST /runs/<id>/review.")
    if any(c.id == "failure" for c in result.checks):
        recs.append("Implementation: inspect failed runs with `cli.py history <run_id>`; make work() return "
                    "{'error': ...} items instead of raising for expected upstream failures.")
    if any(c.id == "latency" and c.status == "warn" for c in result.checks):
        recs.append("Implementation: slow agents; shorten prompts, cap tool calls in the plan, or raise "
                    "OLLAMA_TIMEOUT_SECONDS only if the model is genuinely slow on this host.")
    if any(c.id == "fallback_rate" and c.status == "warn" for c in result.checks):
        recs.append("Implementation: most LLM steps fell back; check Ollama is up and the prompts ask for the exact "
                    "JSON shape validate_plan/validate_review expect.")
    if any(c.id == "human_review" and c.status == "warn" for c in result.checks):
        recs.append("Review: some runs still await a human decision; review or reject them before release.")
    if any(c.status == "gap" for c in result.checks):
        recs.append("Evidence: start the Multi-Agent Server and run each workflow at least once so the review has "
                    "runs and audit entries to check.")
    return recs
