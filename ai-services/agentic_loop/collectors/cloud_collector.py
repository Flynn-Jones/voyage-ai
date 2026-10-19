"""cloud-deployment-review: evidence that the AWS deployment works with AI disabled.

OBSERVE  docs/release-2/cloud/
           cloud-deployment-report.md
           smoke-test-output*.txt   (scripts/deploy/aws/smoke-test.sh; newest file wins)
           stack-outputs*.json      (aws cloudformation describe-stacks Outputs)
         scripts/deploy/aws/services.yml (static: AI flags set to false)
CHECK    every frontend answers, CRUD round-trips pass on every backend, AI routes
         report disabled, the ALB URL is recorded, and no AI flag is enabled.

Smoke output contract (one result per line; other lines are ignored):
    PASS frontend:<name> <detail>
    FAIL crud:<service> <detail>
    PASS ai-disabled:<service> <detail>
The human release decision is added by the orchestrator, not here.
"""
import json
import re
from pathlib import Path

from core.review import ReviewResult

MODE = "cloud-deployment-review"
KINDS = ("frontend", "crud", "ai-disabled")
EXPECTED_MIN = {"frontend": 6, "crud": 5, "ai-disabled": 5}  # home + 5 frontends; 5 backends
AI_FLAGS = ("MCP_ENABLED", "RAG_ENABLED", "MULTI_AGENT_ENABLED", "AI_ENABLED")
SMOKE_LINE = re.compile(r"^(PASS|FAIL)\s+(frontend|crud|ai-disabled):(\S+)\s*(.*)$")


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
    pattern = re.compile(rf"Name:\s*['\"]?{flag}['\"]?\s*\n\s*Value:\s*['\"]?([A-Za-z0-9]+)", re.MULTILINE)
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

    # ---- static: AI disabled in the deployed task definitions -------------
    services = repo_root / "scripts" / "deploy" / "aws" / "services.yml"
    if services.exists():
        result.sources.append(str(services.relative_to(repo_root)))
        template = services.read_text(encoding="utf-8", errors="replace")
        for flag in AI_FLAGS:
            values = _flag_values(template, flag)
            if not values:
                result.add("ai_flags", flag, "fail", f"{flag} not set in services.yml (backends would use their default)")
            elif any(v != "false" for v in values):
                result.add("ai_flags", flag, "fail", f"{flag} has non-false value(s): {sorted(set(values))}")
            else:
                result.add("ai_flags", flag, "pass", f"false in {len(values)} container definition(s)")
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
    if "ai-disabled" in failing or "ai_flags" in failing:
        recs.append("Review: AI must be off in the cloud; set MCP/RAG/MULTI_AGENT/AI_ENABLED=false for every backend "
                    "and confirm the AI routes answer 'disabled'.")
    if gaps:
        recs.append("Evidence: run scripts/deploy/aws/deploy.sh and smoke-test.sh, then save the smoke output and "
                    "stack outputs under docs/release-2/cloud/ (missing: " + ", ".join(sorted(gaps)) + ").")
    return recs
