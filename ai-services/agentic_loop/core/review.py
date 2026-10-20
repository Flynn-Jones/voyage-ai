"""Structured results and report files for the Release 2 review modes.

A review is a list of Checks. Missing evidence is a "gap", never an exception.
    PASS        no fail, no gap
    INCOMPLETE  gaps but no fail
    FAIL        at least one fail
Each run writes docs/release-2/agentic-loop/<mode>-<UTC>.md and
<mode>-validation-<UTC>.jsonl (one JSON object per check, then a summary line).
"""
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

STATUSES = ("pass", "warn", "gap", "fail")
ICONS = {"pass": "PASS", "warn": "WARN", "gap": "GAP", "fail": "FAIL"}


@dataclass
class Check:
    id: str
    subject: str
    status: str
    detail: str
    evidence: dict = field(default_factory=dict)

    def __post_init__(self):
        if self.status not in STATUSES:
            raise ValueError(f"status must be one of {STATUSES}")


@dataclass
class ReviewResult:
    mode: str
    title: str
    sources: list = field(default_factory=list)  # evidence files / URLs consulted
    checks: list = field(default_factory=list)
    metrics: dict = field(default_factory=dict)
    recommendations: list = field(default_factory=list)  # deterministic, derived from checks
    commentary: dict = field(default_factory=dict)  # optional LLM implementation/review text
    decision: dict | None = None  # human release decision (cloud mode)

    def add(self, check_id, subject, status, detail, **evidence) -> Check:
        check = Check(check_id, subject, status, detail, evidence)
        self.checks.append(check)
        return check

    def gap(self, check_id, subject, detail, **evidence) -> Check:
        return self.add(check_id, subject, "gap", detail, **evidence)

    def counts(self) -> dict:
        return {status: sum(1 for c in self.checks if c.status == status) for status in STATUSES}

    @property
    def verdict(self) -> str:
        counts = self.counts()
        if counts["fail"]:
            return "FAIL"
        if counts["gap"] or not self.checks:
            return "INCOMPLETE"
        return "PASS"

    def evidence_text(self) -> str:
        """Compact plain-text evidence for the console and the LLM prompts."""
        lines = [f"{self.title}: verdict {self.verdict} {self.counts()}"]
        lines += [f"- [{ICONS[c.status]}] {c.subject} :: {c.id} :: {c.detail}" for c in self.checks]
        if self.metrics:
            lines.append(f"metrics: {json.dumps(self.metrics, default=str)}")
        return "\n".join(lines)


def timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def render_markdown(result: ReviewResult, stamp: str) -> str:
    counts = result.counts()
    out = [
        f"# {result.title}",
        "",
        f"- Generated: {stamp} (UTC) by `ai-services/agentic_loop` mode `{result.mode}`",
        f"- **Verdict: {result.verdict}** (pass {counts['pass']}, warn {counts['warn']}, "
        f"gap {counts['gap']}, fail {counts['fail']})",
    ]
    if result.decision is not None:
        d = result.decision
        out += [
            "",
            "## Human release decision (required)",
            "",
            f"- **Decision: {d.get('decision', 'PENDING')}**",
            f"- Decided by: {d.get('decided_by') or '-'}",
            f"- Conditions: {d.get('conditions') or '-'}",
            f"- Recorded: {d.get('recorded_at') or '-'}",
        ]
    out += ["", "## Evidence consulted", ""]
    out += [f"- `{s}`" for s in result.sources] or ["- (none found)"]
    out += ["", "## Checks", "", "| Status | Subject | Check | Detail |", "|---|---|---|---|"]
    for c in result.checks:
        detail = c.detail.replace("|", "\\|").replace("\n", " ")
        out.append(f"| {ICONS[c.status]} | {c.subject} | `{c.id}` | {detail} |")
    if result.metrics:
        out += ["", "## Metrics", "", "```json", json.dumps(result.metrics, indent=2, default=str), "```"]
    out += ["", "## Recommendations", ""]
    out += [f"- {r}" for r in result.recommendations] or ["- None: every check passed."]
    if result.commentary:
        out += ["", "## LLM commentary (advisory; never changes the verdict)", ""]
        for key, text in result.commentary.items():
            out += [f"**{key}:** {text}", ""]
    return "\n".join(out).rstrip() + "\n"


def write_reports(result: ReviewResult, out_dir: Path, stamp: str | None = None) -> tuple[Path, Path]:
    stamp = stamp or timestamp()
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    md_path = out_dir / f"{result.mode}-{stamp}.md"
    jsonl_path = out_dir / f"{result.mode}-validation-{stamp}.jsonl"
    md_path.write_text(render_markdown(result, stamp), encoding="utf-8")
    with open(jsonl_path, "w", encoding="utf-8") as handle:
        for check in result.checks:
            handle.write(json.dumps({"type": "check", "mode": result.mode, **asdict(check)}, default=str) + "\n")
        handle.write(json.dumps({
            "type": "summary", "mode": result.mode, "generated": stamp, "verdict": result.verdict,
            "counts": result.counts(), "metrics": result.metrics, "sources": result.sources,
            "recommendations": result.recommendations, "decision": result.decision,
        }, default=str) + "\n")
    return md_path, jsonl_path
