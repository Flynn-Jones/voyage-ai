"""Release 2 review modes: multi-agent-review, testing-review, cloud-deployment-review."""
import json
from pathlib import Path

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


REQUIRED = testing_collector.REQUIRED_ENDPOINT_TESTS
UNRELATED = ("test_health", "test_other_a", "test_other_b")


def junit_xml(classname, passed=(), failed=(), skipped=()):
    cases = [f'<testcase classname="{classname}" name="{n}"/>' for n in passed]
    cases += [f'<testcase classname="{classname}" name="{n}"><failure>boom</failure></testcase>' for n in failed]
    cases += [f'<testcase classname="{classname}" name="{n}"><skipped/></testcase>' for n in skipped]
    return f'<testsuites><testsuite name="s">{"".join(cases)}</testsuite></testsuites>'


def write_sources(root, skip=(), omit=None):
    """Synthetic endpoint test source for each student: `def <name>():` for every required test."""
    for student, (source, _module, names) in REQUIRED.items():
        if student not in skip:
            defs = [n for n in names if n != (omit or {}).get(student)]
            write(root / source, "".join(f"def {n}():\n    pass\n\n" for n in defs))


def required_junit(student, **kw):
    _source, module, names = REQUIRED[student]
    return junit_xml(f"tests.{module}", passed=kw.pop("passed", names), **kw)


def endpoint_checks(result, student):
    return {c.id: c for c in result.checks
            if c.subject == student and c.id in ("endpoint_tests", "endpoint_test_source", "endpoint_test_evidence")}


def test_testing_review_reports_per_student(tmp_path):
    base = tmp_path / "docs" / "release-2"
    write_sources(tmp_path)
    write(base / "student-1" / "pre-commit-security-report.md", "# report\n- Result: PASS\n")
    write(base / "student-1" / "post-commit-cicd-report.md", "Result: PASS")
    write(base / "student-1" / "junit" / "results.xml", required_junit("student-1", passed=(*REQUIRED["student-1"][2], *UNRELATED)))

    write(base / "student-2" / "pre-commit-security-report.md", "Result: PASS (claimed)")
    write(base / "student-2" / "pre-commit-20261020T000000Z.txt", "ruff.....Failed\nexit status: 1 (FAIL)\n")
    write(base / "student-2" / "post-commit-cicd-report.md",
          "ran test_post_valid_accommodation_is_201_and_reads_back and test_get_list_includes_created_record")

    write(base / "student-3" / "post-commit-cicd-report.md", "Result: FAIL")
    write(base / "student-3" / "results.xml",
          required_junit("student-3", passed=REQUIRED["student-3"][2][:1], failed=REQUIRED["student-3"][2][1:]))
    write(base / "student-4" / "results.xml", "<not-xml")

    result = testing_collector.review(tmp_path)
    by = {(c.subject, c.id): c for c in result.checks}
    assert by[("student-1", "precommit_scans")].status == "pass"
    assert by[("student-1", "endpoint_tests")].status == "pass"
    assert by[("student-2", "precommit_scans")].status == "fail"  # raw scan output beats the claim
    gap = by[("student-2", "endpoint_test_evidence")]            # report text is not execution evidence
    assert gap.status == "gap" and "not execution evidence" in gap.detail
    assert by[("student-3", "cicd_report")].status == "fail"
    assert by[("student-3", "endpoint_tests")].status == "fail"
    assert by[("student-4", "junit_parse")].status == "warn"      # malformed XML never crashes
    assert by[("student-4", "endpoint_test_evidence")].status == "gap"
    assert by[("student-5", "precommit_report")].status == "gap"
    assert result.verdict == "FAIL"
    assert result.metrics["students"]["student-1"]["required_tests"] == {n: "passed" for n in REQUIRED["student-1"][2]}
    assert any(r.startswith("student-5:") for r in result.recommendations)


def test_unrelated_passing_tests_are_not_required_test_evidence(tmp_path):
    write_sources(tmp_path)
    base = tmp_path / "docs" / "release-2" / "student-1"
    write(base / "results.xml", junit_xml("tests.test_destinations_api", passed=UNRELATED))
    write(base / "other.xml", junit_xml("tests.test_other", passed=REQUIRED["student-1"][2]))  # wrong module

    result = testing_collector.review(tmp_path)
    checks = endpoint_checks(result, "student-1")
    assert set(checks) == {"endpoint_test_evidence"} and checks["endpoint_test_evidence"].status == "gap"
    assert checks["endpoint_test_evidence"].evidence["tests"] == list(REQUIRED["student-1"][2])


def test_testing_review_distinguishes_missing_implementation_from_failed_and_unrun(tmp_path):
    omit = {"student-5": REQUIRED["student-5"][2][1]}
    write_sources(tmp_path, omit=omit)
    base = tmp_path / "docs" / "release-2"
    write(base / "student-4" / "r.xml", required_junit("student-4", passed=REQUIRED["student-4"][2][:1],
                                                        failed=REQUIRED["student-4"][2][1:]))
    write(base / "student-3" / "r.xml", required_junit("student-3", passed=REQUIRED["student-3"][2][:1]))
    write(base / "student-5" / "r.xml", required_junit("student-5", passed=REQUIRED["student-5"][2][:1]))

    result = testing_collector.review(tmp_path)
    assert endpoint_checks(result, "student-4")["endpoint_tests"].status == "fail"            # ran and failed
    assert endpoint_checks(result, "student-5")["endpoint_test_source"].status == "fail"      # not implemented
    assert endpoint_checks(result, "student-3")["endpoint_test_evidence"].status == "gap"     # implemented, not run
    states = result.metrics["students"]
    assert states["student-4"]["required_tests"][REQUIRED["student-4"][2][1]] == "failed"
    assert states["student-5"]["required_tests"][omit["student-5"]] == "not_implemented"
    assert states["student-3"]["required_tests"][REQUIRED["student-3"][2][1]] == "no_evidence"


def test_passing_junit_for_a_test_removed_from_source_is_not_a_pass(tmp_path):
    removed = REQUIRED["student-1"][2][1]
    write_sources(tmp_path, omit={"student-1": removed})
    write(tmp_path / "docs" / "release-2" / "student-1" / "stale.xml", required_junit("student-1"))  # both "passed"

    result = testing_collector.review(tmp_path)
    checks = endpoint_checks(result, "student-1")
    assert set(checks) == {"endpoint_test_source"} and checks["endpoint_test_source"].status == "fail"
    assert checks["endpoint_test_source"].evidence["tests"] == [removed]
    assert "stale" in checks["endpoint_test_source"].detail
    assert result.metrics["students"]["student-1"]["required_tests"] == {
        REQUIRED["student-1"][2][0]: "passed", removed: "not_implemented"}
    assert result.verdict == "FAIL"


def test_failed_junit_for_a_test_removed_from_source_is_not_implemented_not_failed(tmp_path):
    removed = REQUIRED["student-4"][2][0]
    write_sources(tmp_path, omit={"student-4": removed})
    write(tmp_path / "docs" / "release-2" / "student-4" / "stale.xml",
          required_junit("student-4", passed=REQUIRED["student-4"][2][1:], failed=(removed,)))
    checks = endpoint_checks(testing_collector.review(tmp_path), "student-4")
    assert set(checks) == {"endpoint_test_source"}


def test_testing_review_matches_parametrized_cases_and_skips(tmp_path):
    write_sources(tmp_path)
    base = tmp_path / "docs" / "release-2"
    first, second = REQUIRED["student-5"][2]
    cases = "".join(f'<testcase classname="test_endpoints" name="{first}[{i}]"/>' for i in range(3))
    write(base / "student-5" / "r.xml", f"<testsuite>{cases}"
          f'<testcase classname="test_endpoints" name="{second}[a]"/>'
          f'<testcase classname="test_endpoints" name="{second}[b]"><skipped/></testcase></testsuite>')
    result = testing_collector.review(tmp_path)
    assert result.metrics["students"]["student-5"]["required_tests"] == {first: "passed", second: "skipped"}
    assert endpoint_checks(result, "student-5")["endpoint_test_evidence"].status == "gap"

    write(base / "student-5" / "r.xml", f"<testsuite>{cases}"
          f'<testcase classname="test_endpoints" name="{second}[a]"/>'
          f'<testcase classname="test_endpoints" name="{second}[b]"><error>x</error></testcase></testsuite>')
    result = testing_collector.review(tmp_path)
    assert endpoint_checks(result, "student-5")["endpoint_tests"].status == "fail"


def test_testing_review_evidence_gap_asks_for_junit_not_new_tests(tmp_path):
    write_sources(tmp_path)
    result = testing_collector.review(tmp_path)
    recs = [r for r in result.recommendations if r.startswith("student-1:")]
    assert recs and "JUnit XML" in recs[0] and "test_seed_count" in recs[0]
    assert not any(word in r.lower() for r in result.recommendations for word in ("implement", "add "))  # sources exist


def test_required_endpoint_tests_match_the_student_workflows():
    workflows = Path(__file__).resolve().parents[3] / ".github" / "workflows"
    for student, (_source, _module, names) in REQUIRED.items():
        text = (workflows / f"{student}.yml").read_text(encoding="utf-8")
        assert all(f'"{name}"' in text for name in names), student


def test_testing_review_empty_repo_is_not_implemented_and_missing_reports(tmp_path):
    result = testing_collector.review(tmp_path)
    assert result.verdict == "FAIL"  # required endpoint tests absent from source
    assert len([c for c in result.checks if c.status == "gap"]) == 10  # precommit + cicd report per student
    assert len([c for c in result.checks if c.id == "endpoint_test_source" and c.status == "fail"]) == 5


# ------------------------------------------------------ cloud-deployment-review

SMOKE_OK = "\n".join(
    [f"PASS frontend:{n} 200" for n in ("home", "destination", "accommodation", "activity", "budget", "itinerary")]
    + [f"PASS read:{n} seeded read" for n in ("destination", "accommodation", "activity", "budget", "itinerary")]
    + [f"PASS ai-disabled:{n} disabled" for n in ("destination", "accommodation", "activity", "budget", "itinerary")]
)
SERVICES_OK = "\n".join(
    f'  {flag}: "false"'  # plain YAML mapping, matching the real services.yml - not CFN Name/Value
    for flag in ("MCP_ENABLED", "RAG_ENABLED", "MULTI_AGENT_ENABLED", "AI_ENABLED"))
SMOKE_STAMP = "20261020T000000Z"
ALB = "http://alb.example"
FEATURES = tuple(f"{n}-service" for n in ("destination", "accommodation", "activity", "budget", "itinerary"))


def lifecycle_evidence(**overrides):
    """A complete, valid lifecycle evidence document for the ALB/smoke run cloud_repo() builds."""
    doc = {"format": "voyageai-crud-lifecycle/1", "verified_at": "2026-10-20T01:00:00Z", "verified_by": "tester",
           "alb_url": ALB,
           "features": {f: {"tag": f"r2-crud-{f}", "create": "PASS", "read": "PASS", "update": "PASS",
                            "delete": "PASS", "notes": ""} for f in FEATURES}}
    doc.update(overrides)
    return doc


def write_run(tmp_path, run_id, alb, stack=True):
    """One smoke run's files, named as smoke-test.sh does: both carry the same run id."""
    cloud = tmp_path / "docs" / "release-2" / "cloud"
    write(cloud / f"smoke-test-output-{run_id}.txt", "header line\n" + SMOKE_OK)
    if stack:
        write(cloud / f"stack-outputs-{run_id}.json", json.dumps({"alb_url": alb, "generated": run_id}))


def cloud_repo(tmp_path, smoke=SMOKE_OK, services=SERVICES_OK, lifecycle="default"):
    cloud = tmp_path / "docs" / "release-2" / "cloud"
    write(cloud / "cloud-deployment-report.md", "# report")
    write(cloud / f"smoke-test-output-{SMOKE_STAMP}.txt", "header line\n" + smoke)
    write(cloud / f"stack-outputs-{SMOKE_STAMP}.json", json.dumps([{"OutputKey": "AlbUrl", "OutputValue": ALB}]))
    write(tmp_path / "scripts" / "deploy" / "aws" / "services.yml", services)
    if lifecycle is not None:
        doc = lifecycle_evidence() if lifecycle == "default" else lifecycle
        write(cloud / "crud-lifecycle-evidence-20261020T010000Z.json", json.dumps(doc))
    return tmp_path


def lifecycle_checks(result):
    return [c for c in result.checks if c.id == "crud_lifecycle"]


def test_cloud_review_passes_with_full_evidence(tmp_path):
    result = cloud_collector.review(cloud_repo(tmp_path))
    assert result.verdict == "PASS", result.evidence_text()
    assert result.metrics["alb_url"] == ALB
    assert result.metrics["smoke"] == {"frontend": 6, "read": 5, "ai-disabled": 5}
    assert result.metrics["crud_lifecycle"] == {f: "pass" for f in FEATURES}


def test_cloud_review_fails_on_read_and_enabled_ai(tmp_path):
    smoke = SMOKE_OK.replace("PASS read:budget", "FAIL read:budget")
    services = SERVICES_OK.replace('RAG_ENABLED: "false"', 'RAG_ENABLED: "true"')
    result = cloud_collector.review(cloud_repo(tmp_path, smoke, services))
    failing = {(c.id, c.subject) for c in result.checks if c.status == "fail"}
    assert ("read", "smoke") in failing and ("ai_flags", "RAG_ENABLED") in failing
    assert result.verdict == "FAIL"


def test_cloud_review_missing_evidence_is_gaps(tmp_path):
    result = cloud_collector.review(tmp_path)
    assert {c.id for c in result.checks if c.status == "gap"} == {
        "deployment_report", "smoke_output", "alb_url", "ai_flags", "crud_lifecycle"}
    assert result.verdict == "INCOMPLETE"


def test_cloud_review_reads_alone_are_not_crud_proof(tmp_path):
    result = cloud_collector.review(cloud_repo(tmp_path, lifecycle=None))
    assert [c.status for c in result.checks if c.id == "read"] == ["pass"]
    assert [c.status for c in lifecycle_checks(result)] == ["gap"]
    assert result.verdict == "INCOMPLETE"  # a gap, not an application failure
    assert not any(c.status == "fail" for c in result.checks)
    assert any("crud-lifecycle-evidence" in r for r in result.recommendations)


def test_cloud_review_legacy_crud_lines_count_as_reads_only(tmp_path):
    legacy = SMOKE_OK.replace("PASS read:", "PASS crud:")
    result = cloud_collector.review(cloud_repo(tmp_path, legacy, lifecycle=None))
    assert result.metrics["smoke"] == {"frontend": 6, "read": 5, "ai-disabled": 5}
    assert [c.status for c in lifecycle_checks(result)] == ["gap"]
    assert result.verdict == "INCOMPLETE"
    assert "legacy crud: label, read-only check" in cloud_collector.parse_smoke(legacy)["read"][0][2]


def test_cloud_review_lifecycle_needs_every_step_tag_and_feature(tmp_path):
    doc = lifecycle_evidence()
    feats = doc["features"]
    del feats["destination-service"]["update"]            # missing step
    feats["accommodation-service"]["tag"] = ""            # untagged
    feats["activity-service"]["delete"] = "FAIL"          # explicit failure
    del feats["budget-service"]                           # feature absent
    result = cloud_collector.review(cloud_repo(tmp_path, lifecycle=doc))
    by = {c.subject: c.status for c in lifecycle_checks(result)}
    assert by == {"destination-service": "gap", "accommodation-service": "gap", "activity-service": "fail",
                  "budget-service": "gap", "itinerary-service": "pass"}
    assert result.verdict == "FAIL"


def test_cloud_review_lifecycle_for_a_different_alb_is_a_gap(tmp_path):
    doc = lifecycle_evidence(alb_url="http://some-other-alb.example")
    result = cloud_collector.review(cloud_repo(tmp_path, lifecycle=doc))
    checks = lifecycle_checks(result)
    assert [c.status for c in checks] == ["gap"] and "does not match" in checks[0].detail
    assert result.metrics["crud_lifecycle"] == {f: "gap" for f in FEATURES}
    assert result.verdict == "INCOMPLETE"


def test_cloud_review_lifecycle_older_than_the_smoke_run_is_a_gap(tmp_path):
    doc = lifecycle_evidence(verified_at="2026-10-19T23:59:59Z")  # before the 20261020T000000Z smoke run
    result = cloud_collector.review(cloud_repo(tmp_path, lifecycle=doc))
    checks = lifecycle_checks(result)
    assert [c.status for c in checks] == ["gap"] and "older than the smoke run" in checks[0].detail
    assert result.verdict == "INCOMPLETE"


def test_cloud_review_pairs_stack_outputs_with_the_newest_smoke_run(tmp_path):
    cloud_repo(tmp_path)  # run 20261020T000000Z -> http://alb.example, matching the lifecycle evidence
    write_run(tmp_path, "20261019T000000Z", "http://old-alb.example")  # older run sorts first
    result = cloud_collector.review(tmp_path)
    assert result.metrics["alb_url"] == ALB
    assert result.verdict == "PASS", result.evidence_text()
    assert not any("old-alb" in s for s in result.sources)  # the unselected run's stack outputs are not consulted


def test_cloud_review_lifecycle_for_an_older_runs_alb_is_a_gap(tmp_path):
    cloud_repo(tmp_path)  # lifecycle evidence is bound to http://alb.example (run 20261020...)
    write_run(tmp_path, "20261021T000000Z", "http://new-alb.example")  # newer reviewed deployment
    result = cloud_collector.review(tmp_path)
    assert result.metrics["alb_url"] == "http://new-alb.example"
    checks = lifecycle_checks(result)
    assert [c.status for c in checks] == ["gap"] and "does not match" in checks[0].detail
    assert result.verdict == "INCOMPLETE"


def test_cloud_review_newest_smoke_run_without_its_stack_outputs_is_a_gap(tmp_path):
    cloud_repo(tmp_path)
    write_run(tmp_path, "20261021T000000Z", "", stack=False)  # newest run: no paired stack-outputs file
    result = cloud_collector.review(tmp_path)
    alb_checks = [c for c in result.checks if c.id == "alb_url"]
    assert [c.status for c in alb_checks] == ["gap"] and "stack-outputs-20261021T000000Z.json" in alb_checks[0].detail
    assert result.metrics["alb_url"] is None  # never falls back to an older run's ALB
    assert [c.status for c in lifecycle_checks(result)] == ["gap"]
    assert result.verdict == "INCOMPLETE"


def test_cloud_review_pairs_rerun_suffixes(tmp_path):
    cloud_repo(tmp_path)
    write_run(tmp_path, f"{SMOKE_STAMP}_01", "http://rerun-alb.example")  # same-second rerun sorts last
    assert cloud_collector.review(tmp_path).metrics["alb_url"] == "http://rerun-alb.example"


@pytest.mark.parametrize("overrides", [
    {"alb_url": ""}, {"verified_at": "yesterday"}, {"verified_at": "2026-10-20T01:00:00"},  # no offset
    {"format": "other/1"},
])
def test_cloud_review_unbound_lifecycle_evidence_is_a_gap(tmp_path, overrides):
    result = cloud_collector.review(cloud_repo(tmp_path, lifecycle=lifecycle_evidence(**overrides)))
    assert [c.status for c in lifecycle_checks(result)] == ["gap"]
    assert "not bound to the reviewed deployment" in lifecycle_checks(result)[0].detail


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
    assert app_main.main(["--mode", "testing-review"]) == 1  # no sources or reports -> FAIL -> exit 1
    names = reports(root)
    assert len(names) == 2 and names[0].startswith("testing-review-")
    assert calls == [False, True]  # recommendations even when not passing
    text = (root / "docs" / "release-2" / "agentic-loop" / names[0]).read_text(encoding="utf-8")
    assert "Implementation recommendations" in text and "VERDICT FAIL" in capsys.readouterr().out


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
