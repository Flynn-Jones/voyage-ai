#!/usr/bin/env python3
"""Runs activity-service's full test suite (database-service, backend,
frontend) and appends a dated, labeled results section to
activity-service/docs/test-results.md — never overwritten, so this file
becomes a running pre/post-testing evidence trail across runs.

Each layer runs in its OWN subprocess (via `python -m pytest <dir>`), not one
combined in-process run: database-service, backend, and frontend each have a
top-level `app` module, and importing more than one into the same Python
process lets the module cache silently serve the wrong `app` to a later
layer's tests (confirmed — running them combined broke 19 otherwise-passing
backend tests). Subprocess isolation sidesteps that entirely.

Usage (from activity-service/):
    python tests/report.py --label "Pre-testing - before edit/delete rollout"
"""
import argparse
import datetime
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

ACTIVITY_SERVICE_ROOT = Path(__file__).resolve().parent.parent
REPORT_PATH = ACTIVITY_SERVICE_ROOT / "docs" / "test-results.md"

LAYERS = [
    ("database-service", ACTIVITY_SERVICE_ROOT / "database-service" / "tests"),
    ("backend", ACTIVITY_SERVICE_ROOT / "backend" / "tests"),
    ("frontend", ACTIVITY_SERVICE_ROOT / "frontend" / "tests"),
]


def run_layer(tests_dir, junit_path):
    return subprocess.run(
        [sys.executable, "-m", "pytest", str(tests_dir), f"--junit-xml={junit_path}", "-q"],
        capture_output=True, text=True,
    )


def parse_junit(junit_path, layer):
    if not junit_path.exists():
        return []
    rows = []
    for testcase in ET.parse(junit_path).getroot().iter("testcase"):
        failure = testcase.find("failure")
        error = testcase.find("error")
        skipped = testcase.find("skipped")
        if failure is not None:
            status, detail = "FAIL", (failure.get("message") or "").splitlines()[0][:150]
        elif error is not None:
            status, detail = "ERROR", (error.get("message") or "").splitlines()[0][:150]
        elif skipped is not None:
            status, detail = "SKIP", "skipped"
        else:
            status, detail = "PASS", "ok"
        rows.append({"layer": layer, "name": testcase.get("name"), "status": status, "detail": detail})
    return rows


def build_section(label, all_rows, layer_summaries, run_errors):
    timestamp = datetime.datetime.now().isoformat(timespec="seconds")
    total = len(all_rows)
    passed = sum(1 for r in all_rows if r["status"] == "PASS")

    lines = [f"\n## {label} — {timestamp}\n"]
    if run_errors:
        lines.append("**Layers that failed to run at all (not test failures — the run itself broke):**")
        for name, message in run_errors:
            lines.append(f"- `{name}`: {message}")
        lines.append("")

    lines.append("| Layer | Test | Expected | Actual | Result |")
    lines.append("|---|---|---|---|---|")
    for row in all_rows:
        icon = "✅" if row["status"] == "PASS" else "❌"
        lines.append(f"| {row['layer']} | {row['name']} | PASS | {row['detail']} | {icon} {row['status']} |")
    lines.append("")
    lines.append(f"**Summary: {passed}/{total} passed** ({', '.join(layer_summaries)})")
    lines.append("")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Run activity-service's test suite and record results.")
    parser.add_argument("--label", required=True, help='e.g. "Pre-testing - before edit/delete rollout"')
    args = parser.parse_args()

    all_rows = []
    layer_summaries = []
    run_errors = []

    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        for name, tests_dir in LAYERS:
            if not tests_dir.exists() or not any(tests_dir.iterdir()):
                layer_summaries.append(f"{name}: no tests found")
                continue

            junit_path = tmp_dir / f"{name}.xml"
            result = run_layer(tests_dir, junit_path)
            rows = parse_junit(junit_path, name)

            if not rows and result.returncode not in (0, 1):
                # 0 = all passed, 1 = some tests failed (both are a real run);
                # anything else means pytest itself didn't run (import error,
                # bad args, etc.) rather than the tests failing normally.
                run_errors.append((name, (result.stderr or result.stdout).strip().splitlines()[-1][:200]))
                layer_summaries.append(f"{name}: FAILED TO RUN")
                continue

            all_rows.extend(rows)
            passed = sum(1 for r in rows if r["status"] == "PASS")
            layer_summaries.append(f"{name}: {passed}/{len(rows)} passed")

    section = build_section(args.label, all_rows, layer_summaries, run_errors)

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    is_new = not REPORT_PATH.exists()
    with open(REPORT_PATH, "a", encoding="utf-8") as f:
        if is_new:
            f.write("# activity-service Test Results\n\n")
            f.write(
                "Appended by `tests/report.py` on each run — sections are never "
                "overwritten, so this file is a running pre/post-testing evidence "
                "trail across runs, per the assignment's requirement.\n"
            )
        f.write(section)

    total = len(all_rows)
    passed_total = sum(1 for r in all_rows if r["status"] == "PASS")
    print(f"Wrote results to {REPORT_PATH} ({passed_total}/{total} passed; {', '.join(layer_summaries)})")
    return 0 if passed_total == total and not run_errors else 1


if __name__ == "__main__":
    sys.exit(main())
