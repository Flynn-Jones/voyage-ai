"""cloud-deployment-review: evidence that the AWS deployment works.

OBSERVE  docs/release-2/cloud/
           cloud-deployment-report.md
           smoke-test-output*.txt   (scripts/deploy/aws/smoke-test.sh; newest file wins)
           stack-outputs*.json      (scripts/deploy/aws/smoke-test.sh: ALB/cluster/service ARNs
                                     via plain `aws elbv2`/`aws ecs` calls - no CloudFormation stack)
         scripts/deploy/aws/services.yml (AI flags - see note below)
CHECK    every frontend answers, CRUD round-trips pass on every backend, each backend's
         AI path behaves as its own services.yml entry intends, and the ALB URL is
         recorded.

Phase 2 (docs/release-2/cloud/phase-2-ai-mode-plan.md) wired AI Mode live for the 5
feature backends against a dedicated EC2 "AI host" - MCP/RAG/AI_ENABLED are "true" for
destination/accommodation/activity/budget, and MULTI_AGENT_ENABLED additionally for all
but itinerary (no Multi-Agent integration in its code). The `ai_flags` check below is
informational only (what's actually configured), not a pass/fail assertion of "must be
false" - a mixed enabled/disabled fleet is now the intentional state, not a regression.

Smoke output contract (one result per line; other lines are ignored):
    PASS frontend:<name> <detail>
    FAIL crud:<service> <detail>
    PASS ai-mode:<service> <detail>
The human release decision is added by the orchestrator, not here.
"""
import json
import re
from pathlib import Path

from core.review import ReviewResult

MODE = "cloud-deployment-review"
KINDS = ("frontend", "crud", "ai-mode")
EXPECTED_MIN = {"frontend": 6, "crud": 5, "ai-mode": 5}  # home + 5 frontends; 5 backends
AI_FLAGS = ("MCP_ENABLED", "RAG_ENABLED", "MULTI_AGENT_ENABLED", "AI_ENABLED")
SMOKE_LINE = re.compile(r"^(PASS|FAIL)\s+(frontend|crud|ai-mode):(\S+)\s*(.*)$")


def parse_smoke(text: str) -> dict:
    results = {kind: [] for kind in KINDS}
    for line in text.splitlines():
        match = SMOKE_LINE.match(line.strip())
        if match:
            status, kind, name, detail = match.groups()
            results[kind].append((status, name, detail.strip()))
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
    output_files = sorted(cloud.glob("stack-outputs*.json")) if cloud.exists() else []
    alb = None
    for path in output_files:
        result.sources.append(str(path.relative_to(repo_root)))
        try:
            alb = alb or _alb_url(json.loads(path.read_text(encoding="utf-8")))
        except ValueError as exc:
            result.add("stack_outputs", path.name, "warn", f"invalid JSON: {exc}")
    if alb:
        result.add("alb_url", "cloud", "pass", f"ALB URL recorded: {alb}")
    else:
        result.gap("alb_url", "cloud", "no stack-outputs*.json with an ALB URL output")

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

    result.metrics = {"smoke": {kind: len(rows) for kind, rows in smoke.items()}, "alb_url": alb}
    result.recommendations = _recommendations(result)
    return result


def _recommendations(result: ReviewResult) -> list[str]:
    recs = []
    failing = {c.id for c in result.checks if c.status == "fail"}
    gaps = {c.id for c in result.checks if c.status == "gap"}
    if "crud" in failing:
        recs.append("Implementation: a backend CRUD round-trip failed in the cloud; check its database service "
                    "(Service Connect name:port) and CloudWatch logs before release.")
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
