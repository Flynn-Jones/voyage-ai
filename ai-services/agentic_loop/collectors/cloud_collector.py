"""cloud-deployment-review: evidence that the AWS deployment works.

OBSERVE  docs/release-2/cloud/
           cloud-deployment-report.md
           smoke-test-output*.txt   (scripts/deploy/aws/smoke-test.sh; newest file wins)
           stack-outputs-<run>.json (scripts/deploy/aws/smoke-test.sh: ALB/cluster/service ARNs
                                     via plain `aws elbv2`/`aws ecs` calls - no CloudFormation stack;
                                     only the file sharing the selected smoke run's id is used)
           crud-lifecycle-evidence*.json (filled by hand after live verification; newest wins)
         scripts/deploy/aws/services.yml (AI flags - see note below)
CHECK    every frontend answers, the seeded read works on every backend, a complete tagged
         create/read/update/delete lifecycle is evidenced for every feature, each backend's
         AI path returns a real AI-host-generated result, and the ALB URL is recorded.

Phase 2 (docs/release-2/cloud/phase-2-ai-mode-plan.md) wired AI Mode live for the 5 feature
backends against a dedicated EC2 "AI host" - MCP/RAG/AI_ENABLED are "true" for
destination/accommodation/activity/budget, and MULTI_AGENT_ENABLED additionally for all but
itinerary (no Multi-Agent integration in its code). The `ai_flags` check below is
informational only (what is actually configured), not a pass/fail assertion of "must be
false" - a mixed enabled/disabled fleet is the intentional state, not a regression.

Smoke output contract (one result per line; other lines are ignored):
    PASS frontend:<name> <detail>
    FAIL read:<service> <detail>
    PASS ai-mode:<service> <detail>
`read:` lines are seeded GET checks and prove reads only. Older smoke files whose `crud:` lines
came from the read-only script are counted as `read:`; they are never CRUD proof. Older
`ai-disabled:` lines are from the pre-Phase-2 AI-off contract and are ignored outright - a
disabled AI route is not evidence that AI Mode works.

CRUD lifecycle evidence (docs/release-2/cloud/crud-lifecycle-evidence-<UTC>.json). The AWS teammate
fills this in after running a complete tagged create -> read -> update -> delete on each feature
against the reviewed ALB; nothing here performs that verification:
    {"format": "voyageai-crud-lifecycle/1",
     "verified_at": "2026-10-26T01:23:45Z",        # UTC, ISO 8601, with Z or an offset
     "verified_by": "<name>",
     "alb_url": "http://<alb dns name>",             # must equal the ALB URL in the selected run's stack-outputs
     "features": {"destination-service": {"tag": "r2-crud-<UTC>", "create": "PASS", "read": "PASS",
                                          "update": "PASS", "delete": "PASS", "notes": ""},
                  "accommodation-service": {...}, "activity-service": {...},
                  "budget-service": {...}, "itinerary-service": {...}}}
Evidence counts only for the reviewed deployment: its alb_url must match the stack output and its
verified_at must not be older than the selected smoke run. Otherwise, or when a feature, tag or
step is missing or not PASS/FAIL, the result is a GAP (an explicit FAIL step is a failure). The
human release decision is added by the orchestrator, not here.
"""
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from core.review import ReviewResult

MODE = "cloud-deployment-review"
KINDS = ("frontend", "read", "ai-mode")
EXPECTED_MIN = {"frontend": 6, "read": 5, "ai-mode": 5}  # home + 5 frontends; 5 backends
AI_FLAGS = ("MCP_ENABLED", "RAG_ENABLED", "MULTI_AGENT_ENABLED", "AI_ENABLED")
SMOKE_LINE = re.compile(r"^(PASS|FAIL)\s+(frontend|read|crud|ai-mode):(\S+)\s*(.*)$")
SMOKE_STAMP = re.compile(r"smoke-test-output-(\d{8}T\d{6}Z)")
LEGACY_NOTE = "(legacy crud: label, read-only check)"

LIFECYCLE_FORMAT = "voyageai-crud-lifecycle/1"
LIFECYCLE_GLOB = "crud-lifecycle-evidence*.json"
LIFECYCLE_FEATURES = ("destination-service", "accommodation-service", "activity-service",
                      "budget-service", "itinerary-service")
LIFECYCLE_STEPS = ("create", "read", "update", "delete")


def parse_smoke(text: str) -> dict:
    results = {kind: [] for kind in KINDS}
    for line in text.splitlines():
        match = SMOKE_LINE.match(line.strip())
        if match:
            status, kind, name, detail = match.groups()
            detail = detail.strip()
            if kind == "crud":  # older read-only smoke output: reads, never CRUD proof
                kind, detail = "read", f"{detail} {LEGACY_NOTE}".strip()
            results[kind].append((status, name, detail))
    return results


def _alb_url(outputs) -> str | None:
    if isinstance(outputs, dict):
        outputs = outputs.get("Outputs", [{"OutputKey": k, "OutputValue": v} for k, v in outputs.items()])
    for item in outputs or []:
        if isinstance(item, dict) and "url" in str(item.get("OutputKey", "")).lower():
            return item.get("OutputValue")
    return None


def _flag_values(template: str, flag: str) -> list[str]:
    # services.yml is a plain YAML mapping (`MCP_ENABLED: "false"` under each service's `env:`),
    # not a CloudFormation-style Name/Value list - match the mapping form directly.
    pattern = re.compile(rf"\b{flag}:\s*['\"]?([A-Za-z0-9]+)['\"]?")
    return [v.lower() for v in pattern.findall(template)]


def _norm_url(url) -> str:
    return str(url or "").strip().rstrip("/").lower()


def _utc(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(timezone.utc) if parsed.tzinfo else None  # no offset = ambiguous


def _run_id(smoke_file: Path) -> str | None:
    """`20261020T000000Z` or `20261020T000000Z_01`: shared by a run's smoke-test-output and
    stack-outputs files (scripts/deploy/aws/smoke-test.sh names both with the same RUN_ID)."""
    match = re.fullmatch(r"smoke-test-output-(\d{8}T\d{6}Z(?:_\d+)?)\.txt", smoke_file.name)
    return match.group(1) if match else None


def _smoke_run_time(smoke_file: Path | None) -> datetime | None:
    match = SMOKE_STAMP.search(smoke_file.name) if smoke_file else None
    if not match:
        return None
    return datetime.strptime(match.group(1), "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)


def _binding_problem(data: dict, alb: str | None, smoke_file: Path | None) -> str | None:
    """Why this evidence cannot be tied to the reviewed deployment, or None."""
    if data.get("format") != LIFECYCLE_FORMAT:
        return f"format is not '{LIFECYCLE_FORMAT}'"
    if not _norm_url(data.get("alb_url")):
        return "alb_url is missing"
    if not alb:
        return "no ALB URL in stack-outputs*.json to match it against"
    if _norm_url(data["alb_url"]) != _norm_url(alb):
        return f"alb_url {data['alb_url']} does not match the reviewed stack output {alb}"
    verified = _utc(data.get("verified_at"))
    if verified is None:
        return "verified_at is missing or not a UTC ISO 8601 timestamp (e.g. 2026-10-26T01:23:45Z)"
    smoke_time = _smoke_run_time(smoke_file)
    if smoke_time is None:
        return "no timestamped smoke-test-output*.txt to compare verified_at against"
    if verified < smoke_time:
        return f"verified_at {data['verified_at']} is older than the smoke run {smoke_time:%Y-%m-%dT%H:%M:%SZ}"
    return None


def _lifecycle_status(entry) -> tuple[str, str]:
    """(pass|fail|gap, detail) for one feature's lifecycle entry."""
    if not isinstance(entry, dict):
        return "gap", "feature not present in the evidence"
    steps = {step: str(entry.get(step, "")).strip().upper() for step in LIFECYCLE_STEPS}
    failed = [step for step, value in steps.items() if value == "FAIL"]
    if failed:
        return "fail", f"lifecycle step(s) failed: {', '.join(failed)}"
    missing = [step for step, value in steps.items() if value != "PASS"]
    if not str(entry.get("tag", "")).strip():
        missing.append("tag")
    if missing:
        return "gap", f"no explicit PASS/tag evidence for: {', '.join(missing)}"
    return "pass", f"tagged lifecycle create/read/update/delete passed (tag {entry['tag']})"


def _review_lifecycle(result: ReviewResult, cloud: Path, repo_root: Path, alb, smoke_file) -> dict:
    files = sorted(cloud.glob(LIFECYCLE_GLOB)) if cloud.exists() else []
    summary = {feature: "gap" for feature in LIFECYCLE_FEATURES}
    if not files:
        result.gap("crud_lifecycle", "cloud", f"no {LIFECYCLE_GLOB}; smoke 'read:' checks prove reads only. "
                   "Record a tagged create/read/update/delete per feature after live verification")
        return summary
    path = files[-1]
    result.sources.append(str(path.relative_to(repo_root)))
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        result.gap("crud_lifecycle", path.name, f"unreadable lifecycle evidence: {exc}")
        return summary
    problem = _binding_problem(data, alb, smoke_file) if isinstance(data, dict) else "not a JSON object"
    if problem:
        result.gap("crud_lifecycle", path.name, f"{problem}; evidence not bound to the reviewed deployment")
        return summary
    features = data.get("features")
    for feature in LIFECYCLE_FEATURES:
        status, detail = _lifecycle_status(features.get(feature) if isinstance(features, dict) else None)
        summary[feature] = status
        result.add("crud_lifecycle", feature, status, detail)
    return summary


def review(repo_root: Path) -> ReviewResult:
    result = ReviewResult(MODE, "Cloud Deployment Review")
    cloud = repo_root / "docs" / "release-2" / "cloud"

    report = cloud / "cloud-deployment-report.md"
    if report.exists():
        result.sources.append(str(report.relative_to(repo_root)))
        result.add("deployment_report", "cloud", "pass", "cloud-deployment-report.md present")
    else:
        result.gap("deployment_report", "cloud", "missing docs/release-2/cloud/cloud-deployment-report.md")

    # ---- smoke test -------------------------------------------------------
    smoke_files = sorted(cloud.glob("smoke-test-output*.txt")) if cloud.exists() else []
    smoke_file = smoke_files[-1] if smoke_files else None
    if not smoke_files:
        result.gap("smoke_output", "cloud", "no smoke-test-output*.txt; run scripts/deploy/aws/smoke-test.sh "
                   "and save its output")
        smoke = {kind: [] for kind in KINDS}
    else:
        result.sources.append(str(smoke_files[-1].relative_to(repo_root)))
        smoke = parse_smoke(smoke_files[-1].read_text(encoding="utf-8", errors="replace"))
    for kind in KINDS:
        rows = smoke[kind]
        if not rows:
            if smoke_files:
                result.gap(kind, "smoke", f"no '{kind}:' results in {smoke_files[-1].name}")
            continue
        failures = [f"{name} ({detail})" for status, name, detail in rows if status == "FAIL"]
        passed = [name for status, name, _ in rows if status == "PASS"]
        if failures:
            result.add(kind, "smoke", "fail", f"{len(failures)} failing: {'; '.join(failures)}")
        else:
            result.add(kind, "smoke", "pass", f"{len(passed)} passing: {', '.join(passed)}")
        if len(rows) < EXPECTED_MIN[kind]:
            result.add(f"{kind}_coverage", "smoke", "warn",
                       f"{len(rows)} '{kind}' result(s); expected at least {EXPECTED_MIN[kind]}")

    # ---- stack outputs ----------------------------------------------------
    alb, alb_gap = None, None
    stack_file = None
    if smoke_file is None:
        alb_gap = "no smoke run selected, so no stack-outputs file can be paired with it"
    else:
        run_id = _run_id(smoke_file)
        stack_file = cloud / f"stack-outputs-{run_id}.json" if run_id else None
        if stack_file is None:
            alb_gap = f"{smoke_file.name} carries no run id to pair a stack-outputs file with"
        elif not stack_file.is_file():
            alb_gap = f"no {stack_file.name} paired with the selected smoke run {smoke_file.name}"
        else:
            result.sources.append(str(stack_file.relative_to(repo_root)))
            try:
                alb = _alb_url(json.loads(stack_file.read_text(encoding="utf-8")))
            except ValueError as exc:
                result.add("stack_outputs", stack_file.name, "warn", f"invalid JSON: {exc}")
            if not alb:
                alb_gap = f"{stack_file.name} has no ALB URL output"
    if alb:
        result.add("alb_url", "cloud", "pass", f"ALB URL recorded: {alb} ({stack_file.name})")
    else:
        result.gap("alb_url", "cloud", alb_gap)

    # ---- full CRUD lifecycle (separate, explicit evidence) -----------------
    lifecycle = _review_lifecycle(result, cloud, repo_root, alb, smoke_file)

    # ---- static: AI flags actually configured in services.yml (informational) --------
    # Not a pass/fail assertion - see the module docstring. A missing flag is only a gap
    # if NO service sets it at all, since itinerary legitimately has no MULTI_AGENT_ENABLED.
    services = repo_root / "scripts" / "deploy" / "aws" / "services.yml"
    if services.exists():
        result.sources.append(str(services.relative_to(repo_root)))
        template = services.read_text(encoding="utf-8", errors="replace")
        for flag in AI_FLAGS:
            values = _flag_values(template, flag)
            if not values:
                result.gap("ai_flags", flag, f"{flag} not set anywhere in services.yml")
            else:
                counts = {v: values.count(v) for v in sorted(set(values))}
                result.add("ai_flags", flag, "pass", f"configured: {counts}")
    else:
        result.gap("ai_flags", "services.yml", "scripts/deploy/aws/services.yml not found; cannot confirm AI flags")

    result.metrics = {"smoke": {kind: len(rows) for kind, rows in smoke.items()}, "alb_url": alb,
                      "crud_lifecycle": lifecycle}
    result.recommendations = _recommendations(result)
    return result


def _recommendations(result: ReviewResult) -> list[str]:
    recs = []
    failing = {c.id for c in result.checks if c.status == "fail"}
    gaps = {c.id for c in result.checks if c.status == "gap"}
    if "read" in failing:
        recs.append("Implementation: a backend's seeded read failed in the cloud; check its database service "
                    "(Service Connect name:port), its seed data and CloudWatch logs before release.")
    if "crud_lifecycle" in failing:
        recs.append("Implementation: a feature's tagged create/read/update/delete lifecycle failed in the cloud; "
                    "fix the failing step and re-record the lifecycle evidence.")
    if "crud_lifecycle" in gaps:
        recs.append("Evidence: after live verification, the AWS teammate records each feature's tagged "
                    f"create/read/update/delete in docs/release-2/cloud/crud-lifecycle-evidence-<UTC>.json "
                    f"(format '{LIFECYCLE_FORMAT}' in cloud_collector.py); its alb_url must match the stack "
                    "output and verified_at must not predate the smoke run.")
    gaps = gaps - {"crud_lifecycle"}
    if "frontend" in failing:
        recs.append("Implementation: a frontend is unreachable via the ALB; check its target group health check and listener.")
    if "ai-mode" in failing:
        recs.append("Implementation: a backend's AI path didn't return a real result; check the AI host "
                    "(scripts/deploy/aws/ai-ec2-setup.sh / ai-ec2-deploy.sh) is running and reachable.")
    if gaps:
        recs.append("Evidence: run scripts/deploy/aws/smoke-test.sh against the live ECS Fargate deployment; it "
                    "writes the smoke output, stack outputs, and deployment report under docs/release-2/cloud/ "
                    "(missing: " + ", ".join(sorted(gaps)) + ").")
    return recs
