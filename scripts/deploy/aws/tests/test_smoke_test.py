"""Offline tests for scripts/deploy/aws/smoke-test.sh.

`aws`, `curl` and (where a test needs a fixed timestamp) `date` are replaced by
stubs on PATH, so nothing touches AWS or any live URL. Evidence goes to
SMOKE_CLOUD_DIR and scratch files to TMPDIR, both under pytest's tmp_path, so
no test can write into the repository's docs/release-2/cloud.

Run from the repo root: .venv/bin/python -m pytest scripts/deploy/aws/tests
"""
import copy
import json
import os
import subprocess
import sys

import pytest

SCRIPT = os.path.join(os.path.dirname(__file__), "..", "smoke-test.sh")

# Stub aws: STUB_AWS_FAIL is a comma list of lookups that exit 255 (DNSName,
# LoadBalancerArn, list-services, get-caller-identity); STUB_AWS_OUT maps a lookup to
# the text it prints instead of its default.
AWS_STUB = f"""#!{sys.executable}
import json, os, sys
args = " ".join(sys.argv[1:])
defaults = {{
    "LoadBalancerArn": "arn:aws:elasticloadbalancing:test:alb/voyageai-alb",
    "DNSName": "alb.test",
    "list-services": "[]",
    "get-caller-identity": "123456789012",
}}
lookup = next(k for k in defaults if k in args)
if lookup in os.environ.get("STUB_AWS_FAIL", "").split(","):
    sys.stderr.write("stub aws: simulated failure for " + lookup + "\\n")
    sys.exit(255)
print(json.loads(os.environ.get("STUB_AWS_OUT", "{{}}")).get(lookup, defaults[lookup]))
"""

# Stub curl: the scenario (JSON in STUB_SCENARIO) maps "<port><path>" to
# {"code", "body", "pad", "exit"}. "exit" is curl's exit status, returned after the
# body and status were already produced (a transfer that died part-way);
# {"fail": true} is a transport error (prints 000 for -w, exits 7, no body).
CURL_STUB = f"""#!{sys.executable}
import json, os, sys
args = sys.argv[1:]
out = args[args.index("-o") + 1] if "-o" in args else None
url = [a for a in args if a.startswith("http://")][-1]
hostport, _, path = url[len("http://"):].partition("/")
entry = json.loads(os.environ["STUB_SCENARIO"])[hostport.split(":")[1] + "/" + path]
if entry.get("fail"):
    if "-w" in args:
        sys.stdout.write("000")
    sys.exit(7)
body = entry["body"] + " " * entry.get("pad", 0)
if out and out != "/dev/null":
    open(out, "w").write(body)
elif not out:
    sys.stdout.write(body)
if "-w" in args:
    sys.stdout.write(str(entry["code"]))
sys.exit(entry.get("exit", 0))
"""

DATE_STUB = f"""#!{sys.executable}
import os
print(os.environ["STUB_DATE"])
"""

PASSING = {
    **{f"{port}/": {"code": 200, "body": "ok"} for port in range(3000, 3006)},
    "3001/": {"code": 200, "body": "<td>Japan</td>"},
    "3002/": {"code": 200, "body": "<td>Paris</td>"},
    "3003/": {"code": 200, "body": '<tr id="activity-row-1">'},
    "3004/api/expenses": {"code": 200, "body": '<tr data-expense-id="1">'},
    "3005/api/itinerary": {"code": 200, "body": '[{"itinerary_item_id": 1}]'},
    # AI Mode is live in the cloud as of Phase 2: each AI route answers 200 with a real result.
    "3001/compare": {"code": 200, "body": '<div class="detail-grid">Tokyo vs Kyoto</div>'},
    "3002/recommend": {"code": 200,
                       "body": '<div class="recommendation-card-main"><h2>Top recommendations</h2></div>'},
    "3003/ai-mode/chat": {"code": 200,
                          "body": '<div class="act-chat__bubble act-chat__bubble--assistant">plenty to do</div>'},
    "3004/api/budget/ai-analyse": {"code": 200, "body": '{"analysis": "within budget"}'},
    "3005/api/itinerary/ai-review": {"code": 200, "body": '{"adapt": [], "summary": "fine"}'},
}
ITINERARY_AI = "3005/api/itinerary/ai-review"
ACTIVITY_AI = "3003/ai-mode/chat"
ACCOMMODATION_AI = "3002/recommend"


def run_smoke(tmp_path, aws_fail=(), aws_out=None, fixed_date=None, existing=None, **overrides):
    stubs = tmp_path / "stubs"
    stubs.mkdir(parents=True)
    stub_files = [("aws", AWS_STUB), ("curl", CURL_STUB)]
    if fixed_date:
        stub_files.append(("date", DATE_STUB))
    for name, text in stub_files:
        (stubs / name).write_text(text)
        (stubs / name).chmod(0o755)
    scenario = copy.deepcopy(PASSING)
    scenario.update(overrides)
    cloud_dir, scratch = tmp_path / "cloud", tmp_path / "scratch"
    cloud_dir.mkdir()
    scratch.mkdir()
    for name, content in (existing or {}).items():
        (cloud_dir / name).write_text(content)
    env = {
        **os.environ,
        "PATH": f"{stubs}{os.pathsep}{os.environ['PATH']}",
        "STUB_SCENARIO": json.dumps(scenario),
        "STUB_AWS_FAIL": ",".join(aws_fail),
        "STUB_AWS_OUT": json.dumps(aws_out or {}),
        "SMOKE_CLOUD_DIR": str(cloud_dir),
        "TMPDIR": str(scratch),
        "AWS_REGION": "ap-southeast-2",
    }
    if fixed_date:
        env["STUB_DATE"] = fixed_date
    proc = subprocess.run(["/bin/bash", SCRIPT], env=env, capture_output=True, text=True, timeout=60)
    return proc, cloud_dir, scratch


def evidence(cloud_dir, scratch):
    """Assert the three evidence files exist (JSON valid) and scratch was cleaned;
    returns (smoke_text, stack_outputs_dict, report_text)."""
    smoke_files = list(cloud_dir.glob("smoke-test-output-*.txt"))
    output_files = list(cloud_dir.glob("stack-outputs-*.json"))
    assert len(smoke_files) == 1 and len(output_files) == 1
    report = cloud_dir / "cloud-deployment-report.md"
    assert report.is_file()
    assert list(scratch.iterdir()) == []  # per-run temp dir removed by the trap
    return smoke_files[0].read_text(), json.loads(output_files[0].read_text()), report.read_text()


def evidence_report(cloud_dir):
    return (cloud_dir / "cloud-deployment-report.md").read_text()


def test_all_checks_passing_exits_zero(tmp_path):
    proc, cloud_dir, scratch = run_smoke(tmp_path)

    assert proc.returncode == 0, proc.stdout + proc.stderr
    smoke, outputs, report = evidence(cloud_dir, scratch)
    assert "FAIL" not in smoke
    assert "PASS ai-mode:itinerary-service ai-review returned a real result" in smoke
    assert "Deployment Status: Success" in report
    assert outputs["cluster_arn"] == "arn:aws:ecs:ap-southeast-2:123456789012:cluster/voyageai-cluster"
    assert outputs["alb_url"] == "http://alb.test" and outputs["service_arns"] == []


def test_large_response_body_does_not_false_fail(tmp_path):
    # echo "$body" | grep -q under pipefail can die of SIGPIPE on a big body.
    big = {"code": 200, "body": '<tr id="activity-row-1">', "pad": 3_000_000}
    proc, cloud_dir, scratch = run_smoke(tmp_path, **{"3003/": big})

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "PASS read:activity-service" in evidence(cloud_dir, scratch)[0]


def test_required_failure_exits_nonzero_and_keeps_evidence(tmp_path):
    proc, cloud_dir, scratch = run_smoke(tmp_path, **{"3003/": {"code": 200, "body": "<p>no activities</p>"}})

    assert proc.returncode != 0
    smoke, _, report = evidence(cloud_dir, scratch)
    assert "FAIL read:activity-service http_200 body_mismatch" in smoke
    assert "Partial (1 check(s) failing)" in report


@pytest.mark.parametrize("key", sorted(PASSING))
def test_matching_body_behind_http_503_fails(tmp_path, key):
    proc, cloud_dir, scratch = run_smoke(tmp_path, **{key: {**PASSING[key], "code": 503}})

    assert proc.returncode != 0
    smoke, _, _ = evidence(cloud_dir, scratch)
    assert [line for line in smoke.splitlines() if line.startswith("FAIL")] != []
    assert "http_503" in smoke and "Partial (" in evidence_report(cloud_dir)  # ports 3001-3003 carry 2 checks


@pytest.mark.parametrize("key", ["3000/", "3003/", "3002/recommend", ITINERARY_AI])
def test_curl_error_after_matching_body_and_status_fails(tmp_path, key):
    # Right status and right body, but curl itself exited 18 (partial transfer).
    proc, cloud_dir, scratch = run_smoke(tmp_path, **{key: {**PASSING[key], "exit": 18}})

    assert proc.returncode != 0
    smoke, _, _ = evidence(cloud_dir, scratch)
    assert "curl_exit_18" in smoke
    assert "Partial (" in evidence_report(cloud_dir)


@pytest.mark.parametrize("key", ["3003/", ITINERARY_AI])
def test_transport_failure_is_recorded_not_fatal(tmp_path, key):
    proc, cloud_dir, scratch = run_smoke(tmp_path, **{key: {"fail": True}})

    assert proc.returncode != 0
    assert "http_000 curl_exit_7" in evidence(cloud_dir, scratch)[0]  # reached the report despite curl exiting 7


def test_itinerary_502_fails(tmp_path):
    unavailable = {"code": 502, "body": '{"error": "AI review service is unavailable"}'}
    proc, cloud_dir, scratch = run_smoke(tmp_path, **{ITINERARY_AI: unavailable})

    assert proc.returncode != 0
    smoke, _, _ = evidence(cloud_dir, scratch)
    assert "FAIL ai-mode:itinerary-service http_502" in smoke
    assert "PASS ai-mode:itinerary-service" not in smoke


def test_itinerary_ai_disabled_403_fails(tmp_path):
    # The pre-Phase-2 "disabled" answer is now a regression, not a pass: services.yml sets
    # AI_ENABLED=true for itinerary-be, so a 403 means the live AI path is not working.
    disabled = {"code": 403, "body": '{"error":"AI Mode is disabled"}'}
    proc, cloud_dir, scratch = run_smoke(tmp_path, **{ITINERARY_AI: disabled})

    assert proc.returncode != 0
    smoke, _, _ = evidence(cloud_dir, scratch)
    assert "FAIL ai-mode:itinerary-service http_403" in smoke
    assert "PASS ai-mode:itinerary-service" not in smoke


@pytest.mark.parametrize("lookup, field", [
    ("DNSName", "alb_url"),
    ("LoadBalancerArn", "alb_arn"),
    ("list-services", "service_arns"),
    ("get-caller-identity", "cluster_arn"),
])
def test_aws_lookup_failure_still_writes_valid_evidence_and_fails(tmp_path, lookup, field):
    proc, cloud_dir, scratch = run_smoke(tmp_path, aws_fail=[lookup])

    assert proc.returncode != 0, proc.stdout + proc.stderr
    smoke, outputs, report = evidence(cloud_dir, scratch)
    assert "FAIL aws:" in smoke and "exit_255" in smoke
    assert outputs[field] is None  # unknown, not a made-up value
    assert "Partial" in report


def test_sts_failure_never_yields_a_cluster_arn(tmp_path):
    proc, cloud_dir, scratch = run_smoke(tmp_path, aws_fail=["get-caller-identity"])

    assert proc.returncode != 0
    _, outputs, _ = evidence(cloud_dir, scratch)
    assert outputs["cluster_arn"] is None
    assert "::cluster" not in next(cloud_dir.glob("stack-outputs-*.json")).read_text()


@pytest.mark.parametrize("text", ["None", "", "not-an-account"])
def test_sts_unusable_answer_fails(tmp_path, text):
    proc, cloud_dir, scratch = run_smoke(tmp_path, aws_out={"get-caller-identity": text})

    assert proc.returncode != 0
    assert evidence(cloud_dir, scratch)[1]["cluster_arn"] is None


def test_alb_lookup_failure_fails_every_http_check_without_calling_curl(tmp_path):
    proc, cloud_dir, scratch = run_smoke(tmp_path, aws_fail=["DNSName"])

    assert proc.returncode != 0
    smoke, outputs, report = evidence(cloud_dir, scratch)
    assert smoke.count("alb_unresolved") == 16  # 6 frontends + 5 read + 5 ai-mode
    assert "PASS" not in smoke
    assert outputs["alb_url"] is None and "ALB URL: unresolved" in report


def test_existing_evidence_is_never_overwritten(tmp_path):
    stamp = "20260101T000000Z"
    existing = {
        f"smoke-test-output-{stamp}.txt": "EARLIER SMOKE EVIDENCE\n",
        f"stack-outputs-{stamp}.json": '{"earlier": true}\n',
    }
    proc, cloud_dir, _ = run_smoke(tmp_path, fixed_date=stamp, existing=existing)

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert (cloud_dir / f"smoke-test-output-{stamp}.txt").read_text() == "EARLIER SMOKE EVIDENCE\n"
    assert (cloud_dir / f"stack-outputs-{stamp}.json").read_text() == '{"earlier": true}\n'
    assert (cloud_dir / f"smoke-test-output-{stamp}_01.txt").read_text().startswith("PASS")
    assert json.loads((cloud_dir / f"stack-outputs-{stamp}_01.json").read_text())["generated"] == stamp
    # The agentic_loop reader takes sorted(smoke-test-output*.txt)[-1]: that must be the new file.
    assert sorted(cloud_dir.glob("smoke-test-output*.txt"))[-1].name == f"smoke-test-output-{stamp}_01.txt"


def test_stale_stack_outputs_file_is_not_clobbered_either(tmp_path):
    stamp = "20260101T000000Z"
    existing = {f"stack-outputs-{stamp}.json": '{"earlier": true}\n'}
    proc, cloud_dir, _ = run_smoke(tmp_path, fixed_date=stamp, existing=existing)

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert (cloud_dir / f"stack-outputs-{stamp}.json").read_text() == '{"earlier": true}\n'
    assert (cloud_dir / f"stack-outputs-{stamp}_01.json").is_file()


# ---- list-services output must be a JSON array -------------------------------

@pytest.mark.parametrize("text", ["[not-json", "not json at all", '{"serviceArns": []}', '["arn:ok", 7]', '["arn:ok"] trailing'])
def test_malformed_list_services_output_fails_with_null_service_arns(tmp_path, text):
    proc, cloud_dir, scratch = run_smoke(tmp_path, aws_out={"list-services": text})

    assert proc.returncode == 1, proc.stdout + proc.stderr
    smoke, outputs, report = evidence(cloud_dir, scratch)  # smoke, valid stack JSON and report all written
    assert "FAIL aws:stack-service-arns" in smoke
    assert outputs["service_arns"] is None
    assert outputs["cluster_arn"] is not None and outputs["alb_url"] == "http://alb.test"  # other lookups unaffected
    assert "Partial (1 check(s) failing)" in report


def test_valid_list_services_output_is_kept(tmp_path):
    arns = '["arn:aws:ecs:ap-southeast-2:1:service/voyageai-cluster/a", "arn:aws:ecs:ap-southeast-2:1:service/voyageai-cluster/b"]'
    proc, cloud_dir, scratch = run_smoke(tmp_path, aws_out={"list-services": arns})

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert len(evidence(cloud_dir, scratch)[1]["service_arns"]) == 2


# ---- itinerary: a 200 alone is not proof; the body must carry a real review ----

@pytest.mark.parametrize("body", [
    '{"review": "looks fine"}',        # a 200, but no review payload
    '{"adapted": true}',               # near-miss key
    'adapt: []',                       # unquoted, not the JSON field
    '{"error":"AI Mode is disabled"}', # disabled message behind a 200
    "",                                # empty body
])
def test_itinerary_200_without_a_real_review_fails(tmp_path, body):
    proc, cloud_dir, scratch = run_smoke(tmp_path, **{ITINERARY_AI: {"code": 200, "body": body}})

    assert proc.returncode == 1, proc.stdout + proc.stderr
    smoke, _, _ = evidence(cloud_dir, scratch)
    assert "FAIL ai-mode:itinerary-service http_200 body_mismatch" in smoke
    assert "PASS ai-mode:itinerary-service" not in smoke


@pytest.mark.parametrize("body, why", [
    # _chat_exchange.html renders a backend failure through the same partial, at 200.
    ('<div class="act-chat__bubble act-chat__bubble--error">Could not get a response: '
     'AI mode is disabled.</div>', "error bubble"),
    # a 200 with no "reply" key in the JSON
    ('<div class="act-chat__bubble act-chat__bubble--assistant">No reply received.</div>',
     "no reply"),
])
def test_activity_chat_error_dressed_as_200_fails(tmp_path, body, why):
    proc, cloud_dir, scratch = run_smoke(tmp_path, **{ACTIVITY_AI: {"code": 200, "body": body}})

    assert proc.returncode != 0, why
    smoke, _, _ = evidence(cloud_dir, scratch)
    assert "FAIL ai-mode:activity-service" in smoke
    assert "PASS ai-mode:activity-service" not in smoke


@pytest.mark.parametrize("body", [
    # the failure panel still contains the word "recommendation"
    '<section class="panel recommendation-panel"><div class="alert alert--error">'
    '<strong>Recommendation failed</strong></div></section>',
    # ...so does the "nothing matched" panel, which really is served at 200
    '<section class="panel recommendation-panel"><div class="alert alert--warning">'
    '<strong>No recommendation available</strong></div></section>',
])
def test_accommodation_recommendation_panel_without_a_result_fails(tmp_path, body):
    proc, cloud_dir, scratch = run_smoke(tmp_path, **{ACCOMMODATION_AI: {"code": 200, "body": body}})

    assert proc.returncode != 0
    smoke, _, _ = evidence(cloud_dir, scratch)
    assert "FAIL ai-mode:accommodation-service http_200 body_mismatch" in smoke
    assert "PASS ai-mode:accommodation-service" not in smoke


def test_itinerary_review_with_extra_fields_and_spacing_passes(tmp_path):
    body = '{\n  "adapt": ["move day 1 earlier"],\n  "request_id": "abc"\n}'
    proc, cloud_dir, scratch = run_smoke(tmp_path, **{ITINERARY_AI: {"code": 200, "body": body}})

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "PASS ai-mode:itinerary-service ai-review returned a real result" in evidence(cloud_dir, scratch)[0]


# ---- an existing cloud-deployment-report.md is never silently erased -------------

def generated_report(tmp_path):
    """A report as an earlier run of the script wrote it (placeholder Release Decision)."""
    proc, cloud_dir, _ = run_smoke(tmp_path)
    assert proc.returncode == 0
    return (cloud_dir / "cloud-deployment-report.md").read_text()


def new_run_reports(cloud_dir):
    return sorted(p.name for p in cloud_dir.glob("cloud-deployment-report-*.md"))


def test_report_with_a_recorded_release_decision_is_preserved(tmp_path):
    decided = generated_report(tmp_path / "earlier").replace(
        "Status: PENDING", "Status: APPROVED by the release manager on 2026-10-23\n\nRisk accepted: activity seed."
    )
    assert "APPROVED" in decided
    proc, cloud_dir, scratch = run_smoke(tmp_path / "rerun", existing={"cloud-deployment-report.md": decided})

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert (cloud_dir / "cloud-deployment-report.md").read_text() == decided  # byte-identical
    assert "left untouched" in proc.stdout
    [fresh] = new_run_reports(cloud_dir)  # this run's report is kept beside it, not lost
    fresh_text = (cloud_dir / fresh).read_text()
    assert "Deployment Status: Success" in fresh_text and "Status: PENDING" in fresh_text
    assert list(scratch.iterdir()) == []


def test_hand_written_report_without_a_release_decision_section_is_preserved(tmp_path):
    handwritten = "# Cloud report\n\nALB: http://example.invalid\nWritten by hand.\n"
    proc, cloud_dir, _ = run_smoke(tmp_path, existing={"cloud-deployment-report.md": handwritten})

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert (cloud_dir / "cloud-deployment-report.md").read_text() == handwritten
    assert len(new_run_reports(cloud_dir)) == 1


def test_report_with_placeholder_decision_is_regenerated_in_place(tmp_path):
    previous = generated_report(tmp_path / "earlier")
    proc, cloud_dir, _ = run_smoke(tmp_path / "rerun", existing={"cloud-deployment-report.md": previous}, fixed_date="20270101T000000Z")

    assert proc.returncode == 0, proc.stdout + proc.stderr
    refreshed = (cloud_dir / "cloud-deployment-report.md").read_text()
    assert "Timestamp: 20270101T000000Z" in refreshed and refreshed != previous
    assert new_run_reports(cloud_dir) == []  # nothing to protect, so no extra file


def test_failing_run_also_preserves_a_decided_report_and_still_exits_nonzero(tmp_path):
    decided = "# Report\n\n## Release Decision\n\nStatus: APPROVED\n"
    proc, cloud_dir, scratch = run_smoke(
        tmp_path, existing={"cloud-deployment-report.md": decided}, **{"3003/": {"code": 200, "body": "no rows"}}
    )

    assert proc.returncode == 1
    assert (cloud_dir / "cloud-deployment-report.md").read_text() == decided
    [fresh] = new_run_reports(cloud_dir)
    assert "Partial (1 check(s) failing)" in (cloud_dir / fresh).read_text()
