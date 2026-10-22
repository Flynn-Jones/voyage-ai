"""Release 2 review modes: multi-agent-review, testing-review, cloud-deployment-review."""
import json

import pytest

import app_main
from collectors import cloud_collector, multi_agent_collector, testing_collector
from core import orchestrator
from core.review import ReviewResult, write_reports

# ---------------------------------------------------------------- helpers


def audit(run_id, step, agent, ts, workflow="example_readonly", **extra):
    return {"ts": ts, "run_id": run_id, "workflow": workflow, "step": step, "agent": agent,
            "input_summary": None, "output": None, "tools_called": [], "duration_ms": 100,
            "model": "qwen2.5:7b", "error": None, **extra}


def full_run(run_id, tools=("list_destinations",)):
    return [
        audit(run_id, "plan", "planner", "2026-10-20T00:00:01", source="llm"),
        audit(run_id, "work", "worker", "2026-10-20T00:00:02",
              tools_called=[{"tool": t, "ok": True} for t in tools]),
        audit(run_id, "review", "reviewer", "2026-10-20T00:00:03", source="fallback"),
        audit(run_id, "human_review", "human", "2026-10-20T00:00:04"),
        audit(run_id, "applied", "backend", "2026-10-20T00:00:05"),
    ]


@pytest.fixture
def ma_env(tmp_path, monkeypatch):
    log = tmp_path / "audit.jsonl"
    monkeypatch.setenv("MULTI_AGENT_AUDIT_LOG", str(log))
    state = {"runs": [], "workflows": [{"name": "example_readonly", "allowed_tools": ["list_destinations"]}],
             "down": False, "urls": []}

    def write(entries):
        log.write_text("".join(json.dumps(e) + "\n" for e in entries), encoding="utf-8")

    def fetch(url):
        state["urls"].append(url)
        if state["down"]:
            return None, "ConnectionError: refused"
        return (state["workflows"], None) if url.endswith("/workflows") else (state["runs"], None)

    state["write"] = write
    state["fetch"] = fetch
    return state


def ma_review(ma_env, tmp_path, **kw):
    return multi_agent_collector.review(tmp_path, fetch=ma_env["fetch"], **kw)


def statuses(result, check_id):
    return [c.status for c in result.checks if c.id == check_id]


# ---------------------------------------------------------- multi-agent-review


def test_multi_agent_complete_run_passes(ma_env, tmp_path):
    ma_env["runs"] = [{"run_id": "r1", "workflow": "example_readonly", "state": "applied"}]
    ma_env["write"](full_run("r1"))
    result = ma_review(ma_env, tmp_path)
    assert result.verdict == "PASS", result.evidence_text()
    assert statuses(result, "lifecycle") == ["pass"]
    assert statuses(result, "human_review") == ["pass"]
    assert statuses(result, "tool_allowlist") == ["pass"]
    assert result.metrics["fallback_rate"] == 0.5
    assert result.metrics["latency_ms"]["planner"]["p95"] == 100


def test_multi_agent_flags_missing_human_review_and_order(ma_env, tmp_path):
    ma_env["runs"] = [{"run_id": "r1", "workflow": "example_readonly", "state": "approved"},
                      {"run_id": "r2", "workflow": "example_readonly", "state": "awaiting_human_review"}]
    entries = full_run("r1")[:3]  # no human_review
    r2 = full_run("r2")[:3]
    r2[0]["ts"], r2[1]["ts"] = r2[1]["ts"], r2[0]["ts"]  # worker before planner
    ma_env["write"](entries + r2)
    result = ma_review(ma_env, tmp_path)
    assert result.verdict == "FAIL"
    by_subject = {(c.subject, c.id): c.status for c in result.checks}
    assert by_subject[("example_readonly/r1", "human_review")] == "fail"
    assert by_subject[("example_readonly/r2", "lifecycle")] == "fail"
    assert by_subject[("example_readonly/r2", "human_review")] == "warn"
    assert any("lifecycle evidence" in r for r in result.recommendations)


def test_multi_agent_flags_refused_and_non_allowlisted_tools(ma_env, tmp_path):
    ma_env["runs"] = [{"run_id": "r1", "workflow": "example_readonly", "state": "applied"},
                      {"run_id": "r2", "workflow": "example_readonly", "state": "failed", "error": "refused"}]
    refused = audit("r2", "tool_refused", "worker", "2026-10-20T00:00:02",
                    tools_called=[{"tool": "create_accommodation", "refused": True}])
    ma_env["write"](full_run("r1", tools=("list_destinations", "create_accommodation")) + [refused])
    result = ma_review(ma_env, tmp_path)
    details = {c.subject: c.detail for c in result.checks if c.id == "tool_allowlist"}
    assert "outside allowlist" in details["example_readonly/r1"]
    assert "refused" in details["example_readonly/r2"]
    assert statuses(result, "failure") == ["warn"]
    assert result.metrics["failure_rate"] == 0.5


def test_multi_agent_latency_warning(ma_env, tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTIC_LATENCY_WARN_MS", "50")
    ma_env["runs"] = [{"run_id": "r1", "workflow": "example_readonly", "state": "applied"}]
    ma_env["write"](full_run("r1"))
    result = ma_review(ma_env, tmp_path)
    assert set(statuses(result, "latency")) == {"warn"}


def test_multi_agent_server_down_uses_audit_only(ma_env, tmp_path):
    ma_env["down"] = True
    ma_env["write"](full_run("r1"))
    result = ma_review(ma_env, tmp_path)
    assert statuses(result, "server_reachable") == ["gap"]
    assert statuses(result, "lifecycle") == ["pass"]
    assert result.verdict == "INCOMPLETE"


def test_multi_agent_no_evidence_is_gaps_not_crash(ma_env, tmp_path):
    ma_env["down"] = True
    result = ma_review(ma_env, tmp_path)
    assert {c.id for c in result.checks} == {"server_reachable", "audit_log", "runs_found"}
    assert result.verdict == "INCOMPLETE"


def test_multi_agent_workflow_filter(ma_env, tmp_path):
    ma_env["write"](full_run("r1") + [audit("x", "plan", "planner", "t", workflow="budget_other")])
    ma_env["runs"] = [{"run_id": "r1", "workflow": "example_readonly", "state": "applied"}]
    ma_review(ma_env, tmp_path, workflow="example_readonly", limit=5)
    assert ma_env["urls"][0].endswith("/runs?limit=5&workflow=example_readonly")


# --------------------------------------------------------------- testing-review


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


JUNIT_OK = """<testsuites><testsuite name="s"><testcase name="test_get_items"/>
<testcase name="test_post_item"/></testsuite></testsuites>"""
JUNIT_FAIL = """<testsuite><testcase name="test_a"/><testcase name="test_b"><failure>x</failure></testcase></testsuite>"""


def test_testing_review_reports_per_student(tmp_path):
    base = tmp_path / "docs" / "release-2"
    write(base / "student-1" / "pre-commit-security-report.md", "# report\n- Result: PASS\n")
    write(base / "student-1" / "post-commit-cicd-report.md", "Result: PASS")
    write(base / "student-1" / "junit" / "results.xml", JUNIT_OK)

    write(base / "student-2" / "pre-commit-security-report.md", "Result: PASS (claimed)")
    write(base / "student-2" / "pre-commit-20261020T000000Z.txt", "ruff.....Failed\nexit status: 1 (FAIL)\n")
    write(base / "student-2" / "post-commit-cicd-report.md", "ran test_create_budget and test_list_budget")

    write(base / "student-3" / "post-commit-cicd-report.md", "Result: FAIL")
    write(base / "student-3" / "results.xml", JUNIT_FAIL)
    write(base / "student-4" / "results.xml", "<not-xml")

    result = testing_collector.review(tmp_path)
    by = {(c.subject, c.id): c for c in result.checks}
    assert by[("student-1", "precommit_scans")].status == "pass"
    assert by[("student-1", "endpoint_tests")].status == "pass"
    assert by[("student-2", "precommit_scans")].status == "fail"  # raw scan output beats the claim
    assert by[("student-2", "endpoint_tests")].status == "warn"   # report text, no JUnit
    assert by[("student-3", "cicd_report")].status == "fail"
    assert by[("student-3", "endpoint_tests")].status == "fail"
    assert by[("student-4", "junit_parse")].status == "warn"      # malformed XML never crashes
    assert by[("student-5", "precommit_report")].status == "gap"
    assert result.verdict == "FAIL"
    assert result.metrics["students"]["student-1"]["passing_tests"] == 2
    assert any(r.startswith("student-5:") for r in result.recommendations)


def test_testing_review_empty_repo_is_incomplete(tmp_path):
    result = testing_collector.review(tmp_path)
    assert result.verdict == "INCOMPLETE"
    assert len([c for c in result.checks if c.status == "gap"]) == 15  # 3 per student


# ------------------------------------------------------ cloud-deployment-review

SMOKE_OK = "\n".join(
    [f"PASS frontend:{n} 200" for n in ("home", "destination", "accommodation", "activity", "budget", "itinerary")]
    + [f"PASS crud:{n} create/read/update/delete" for n in ("destination", "accommodation", "activity", "budget", "itinerary")]
    + [f"PASS ai-mode:{n} real result" for n in ("destination", "accommodation", "activity", "budget", "itinerary")]
)
SERVICES_OK = "\n".join(
    f'  {flag}: "false"'  # plain YAML mapping, matching the real services.yml - not CFN Name/Value
    for flag in ("MCP_ENABLED", "RAG_ENABLED", "MULTI_AGENT_ENABLED", "AI_ENABLED"))


def cloud_repo(tmp_path, smoke=SMOKE_OK, services=SERVICES_OK):
    cloud = tmp_path / "docs" / "release-2" / "cloud"
    write(cloud / "cloud-deployment-report.md", "# report")
    write(cloud / "smoke-test-output-20261020T000000Z.txt", "header line\n" + smoke)
    write(cloud / "stack-outputs.json", json.dumps([{"OutputKey": "AlbUrl", "OutputValue": "http://alb.example"}]))
    write(tmp_path / "scripts" / "deploy" / "aws" / "services.yml", services)
    return tmp_path


def test_cloud_review_passes_with_full_evidence(tmp_path):
    result = cloud_collector.review(cloud_repo(tmp_path))
    assert result.verdict == "PASS", result.evidence_text()
    assert result.metrics["alb_url"] == "http://alb.example"
    assert result.metrics["smoke"] == {"frontend": 6, "crud": 5, "ai-mode": 5}


def test_cloud_review_fails_on_crud_failure(tmp_path):
    smoke = SMOKE_OK.replace("PASS crud:budget", "FAIL crud:budget")
    result = cloud_collector.review(cloud_repo(tmp_path, smoke))
    failing = {(c.id, c.subject) for c in result.checks if c.status == "fail"}
    assert ("crud", "smoke") in failing
    assert result.verdict == "FAIL"


def test_cloud_review_ai_flags_are_informational_not_pass_fail(tmp_path):
    # Phase 2 (docs/release-2/cloud/phase-2-ai-mode-plan.md) wired AI live for 4-5
    # backends - an enabled flag is the intentional state, not a finding to flag.
    services = SERVICES_OK.replace('RAG_ENABLED: "false"', 'RAG_ENABLED: "true"')
    result = cloud_collector.review(cloud_repo(tmp_path, services=services))
    ai_flag_checks = {(c.id, c.subject): c.status for c in result.checks if c.subject == "RAG_ENABLED"}
    assert ai_flag_checks[("ai_flags", "RAG_ENABLED")] == "pass"
    assert result.verdict == "PASS"


def test_cloud_review_missing_evidence_is_gaps(tmp_path):
    result = cloud_collector.review(tmp_path)
    assert {c.id for c in result.checks if c.status == "gap"} == {
        "deployment_report", "smoke_output", "alb_url", "ai_flags"}
    assert result.verdict == "INCOMPLETE"


# ------------------------------------------------------------- reports + CLI


def test_write_reports_names_and_summary(tmp_path):
    result = ReviewResult("testing-review", "Testing Review")
    result.add("x", "student-1", "pass", "ok")
    md, jsonl = write_reports(result, tmp_path, "20261020T000000Z")
    assert md.name == "testing-review-20261020T000000Z.md"
    assert jsonl.name == "testing-review-validation-20261020T000000Z.jsonl"
    lines = [json.loads(line) for line in jsonl.read_text(encoding="utf-8").splitlines()]
    assert lines[0]["type"] == "check" and lines[-1]["type"] == "summary"
    assert lines[-1]["verdict"] == "PASS"
    assert "**Verdict: PASS**" in md.read_text(encoding="utf-8")


@pytest.fixture
def cli_repo(tmp_path, monkeypatch):
    monkeypatch.setattr(app_main, "REPO_ROOT", tmp_path)
    calls = []

    def fake_call(system, user, review=False):
        calls.append(review)
        return ("advice", None)

    monkeypatch.setattr(orchestrator.ai, "call", fake_call)
    return tmp_path, calls


def reports(root):
    return sorted(p.name for p in (root / "docs" / "release-2" / "agentic-loop").glob("*"))


def test_cli_testing_review_writes_reports_and_llm_runs_on_fail(cli_repo, capsys):
    root, calls = cli_repo
    assert app_main.main(["--mode", "testing-review"]) == 1  # all gaps -> INCOMPLETE -> exit 1
    names = reports(root)
    assert len(names) == 2 and names[0].startswith("testing-review-")
    assert calls == [False, True]  # recommendations even when not passing
    text = (root / "docs" / "release-2" / "agentic-loop" / names[0]).read_text(encoding="utf-8")
    assert "Implementation recommendations" in text and "VERDICT INCOMPLETE" in capsys.readouterr().out


def test_cli_cloud_review_decision_handling(cli_repo, monkeypatch, capsys):
    root, _ = cli_repo
    cloud_repo(root)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    assert app_main.main(["--mode", "cloud-deployment-review", "--no-llm"]) == 2
    assert "decision is required" in capsys.readouterr().out
    md = next(p for p in (root / "docs/release-2/agentic-loop").glob("*.md"))
    assert "PENDING (required)" in md.read_text(encoding="utf-8")

    assert app_main.main(["--mode", "cloud-deployment-review", "--no-llm", "--decision", "approve",
                          "--decided-by", "lead"]) == 0
    assert app_main.main(["--mode", "cloud-deployment-review", "--no-llm", "--decision", "reject"]) == 1
    with pytest.raises(SystemExit) as exc:
        app_main.main(["--mode", "cloud-deployment-review", "--decision", "approve-with-conditions"])
    assert exc.value.code == 2


def test_cli_cloud_review_prompts_for_decision_on_tty(cli_repo, monkeypatch):
    root, _ = cli_repo
    answers = iter(["maybe", "approve-with-conditions", "", "fix budget CRUD first", "Alex"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    assert app_main.main(["--mode", "cloud-deployment-review", "--no-llm"]) == 0
    jsonl = next((root / "docs/release-2/agentic-loop").glob("*.jsonl"))
    summary = json.loads(jsonl.read_text(encoding="utf-8").splitlines()[-1])
    assert summary["decision"]["decision"] == "approve-with-conditions"
    assert summary["decision"]["conditions"] == "fix budget CRUD first"
    assert summary["decision"]["decided_by"] == "Alex"


def test_cli_multi_agent_review_runs_without_server(cli_repo, monkeypatch, capsys):
    root, _ = cli_repo
    monkeypatch.setenv("MULTI_AGENT_SERVICE_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("MULTI_AGENT_AUDIT_LOG", str(root / "missing.jsonl"))
    monkeypatch.setenv("AGENTIC_TIMEOUT_SECONDS", "1")
    assert app_main.main(["--mode", "multi-agent-review", "--no-llm", "--workflow", "example_readonly"]) == 1
    assert "VERDICT INCOMPLETE" in capsys.readouterr().out
    assert len(reports(root)) == 2


def test_release_1_all_mode_unchanged(monkeypatch):
    seen = []
    monkeypatch.setattr(orchestrator, "COLLECTORS", {k: (lambda k=k: lambda a, r: seen.append(k) or (True, k))()
                                                     for k in ("mcp", "rag", "activity_rag")})
    assert app_main.main(["--mode", "all", "--no-llm"]) == 0
    assert seen == ["mcp", "rag"]


def test_cli_survives_non_utf8_console(monkeypatch):
    # Found live: '\u2192' in output crashed `app_main.py` when stdout was a cp1252 pipe on Windows.
    import io
    import sys

    raw = io.BytesIO()
    console = io.TextIOWrapper(raw, encoding="cp1252")
    monkeypatch.setattr(sys, "stdout", console)
    app_main._tolerant_console()
    print("Planner \u2192 Worker")
    console.flush()
    assert raw.getvalue() == b"Planner ? Worker\r\n" or raw.getvalue() == b"Planner ? Worker\n"


def test_cli_cloud_review_eof_at_prompt_is_pending(cli_repo, monkeypatch):
    # Found live: stdin=NUL reports isatty() True on Windows, then input() raises EOFError.
    def eof(prompt=""):
        raise EOFError
    monkeypatch.setattr("builtins.input", eof)
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    assert app_main.main(["--mode", "cloud-deployment-review", "--no-llm"]) == 2
