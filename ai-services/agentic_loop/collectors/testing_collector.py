"""testing-review: per-student Release 2 testing evidence.

OBSERVE  docs/release-2/student-N/ for N = 1..5:
           pre-commit-security-report.md   (+ raw pre-commit-<UTC>.txt from scripts/test/precommit-report.sh)
           post-commit-cicd-report.md
           *.xml JUnit results (any depth), if present
CHECK    report present, scans passing, CI/CD report present and not failing,
         at least MIN_ENDPOINT_TESTS passing tests (JUnit is authoritative; test
         names quoted in the CI/CD report are accepted as weaker evidence).
Anything missing is a gap for that student, never an error.
"""
import re
import xml.etree.ElementTree as ET  # noqa: S405 - parses the team's own JUnit files from the repo, not untrusted input
from pathlib import Path

from core.review import ReviewResult

MODE = "testing-review"
STUDENTS = tuple(f"student-{n}" for n in range(1, 6))
MIN_ENDPOINT_TESTS = 2
PRECOMMIT_REPORT = "pre-commit-security-report.md"
CICD_REPORT = "post-commit-cicd-report.md"
RESULT_RE = re.compile(r"result\W{0,6}(pass|fail)", re.IGNORECASE)
RAW_STATUS_RE = re.compile(r"exit status:\s*\d+\s*\((PASS|FAIL)\)")
TEST_NAME_RE = re.compile(r"\btest_[A-Za-z0-9_]+")


def _stated_result(text: str):
    match = RESULT_RE.search(text)
    return match.group(1).upper() if match else None


def _scan_status(student_dir: Path, report_text: str):
    """(PASS|FAIL|None, source). The newest raw scan output wins over the report's own claim."""
    raw = sorted(student_dir.glob("pre-commit-*.txt"))
    if raw:
        match = RAW_STATUS_RE.search(raw[-1].read_text(encoding="utf-8", errors="replace"))
        if match:
            return match.group(1), raw[-1].name
    if re.search(r"\.{3,}\s*Failed\b", report_text):
        return "FAIL", PRECOMMIT_REPORT
    return _stated_result(report_text), PRECOMMIT_REPORT


def junit_counts(paths):
    """Return (total, failed, skipped, parse_errors) across JUnit XML files."""
    total = failed = skipped = 0
    errors = []
    for path in paths:
        try:
            root = ET.parse(path).getroot()  # noqa: S314 - repo-local CI output, see import note
        except ET.ParseError as exc:
            errors.append(f"{path.name}: {exc}")
            continue
        for case in root.iter("testcase"):
            total += 1
            if case.find("failure") is not None or case.find("error") is not None:
                failed += 1
            elif case.find("skipped") is not None:
                skipped += 1
    return total, failed, skipped, errors


def review(repo_root: Path) -> ReviewResult:
    result = ReviewResult(MODE, "Testing Review")
    base = repo_root / "docs" / "release-2"
    summary = {}
    for student in STUDENTS:
        student_dir = base / student
        row = {"pre_commit": "missing", "cicd": "missing", "passing_tests": None, "evidence": None}
        summary[student] = row

        # pre-commit security report + scan status
        report = student_dir / PRECOMMIT_REPORT
        if report.exists():
            result.sources.append(str(report.relative_to(repo_root)))
            text = report.read_text(encoding="utf-8", errors="replace")
            status, source = _scan_status(student_dir, text)
            row["pre_commit"] = status or "unstated"
            if status == "PASS":
                result.add("precommit_scans", student, "pass", f"pre-commit scans pass ({source})")
            elif status == "FAIL":
                result.add("precommit_scans", student, "fail", f"pre-commit scans failing ({source})")
            else:
                result.gap("precommit_scans", student, f"{PRECOMMIT_REPORT} does not state a PASS/FAIL result; "
                           "attach scripts/test/precommit-report.sh output")
        else:
            result.gap("precommit_report", student, f"missing docs/release-2/{student}/{PRECOMMIT_REPORT}")

        # post-commit CI/CD report
        cicd = student_dir / CICD_REPORT
        cicd_text = ""
        if cicd.exists():
            result.sources.append(str(cicd.relative_to(repo_root)))
            cicd_text = cicd.read_text(encoding="utf-8", errors="replace")
            stated = _stated_result(cicd_text)
            row["cicd"] = stated or "present"
            if stated == "FAIL":
                result.add("cicd_report", student, "fail", f"{CICD_REPORT} reports a failing pipeline")
            else:
                result.add("cicd_report", student, "pass", f"{CICD_REPORT} present" + (f" ({stated})" if stated else ""))
        else:
            result.gap("cicd_report", student, f"missing docs/release-2/{student}/{CICD_REPORT}")

        # endpoint tests: JUnit first, report text second
        junit = sorted(student_dir.rglob("*.xml")) if student_dir.exists() else []
        total, failed, skipped, errors = junit_counts(junit)
        result.sources += [str(p.relative_to(repo_root)) for p in junit]
        if errors:
            result.add("junit_parse", student, "warn", "; ".join(errors))
        if total:
            passed = total - failed - skipped
            row.update(passing_tests=passed, evidence="junit")
            if failed:
                result.add("endpoint_tests", student, "fail", f"JUnit: {failed} failing of {total}")
            elif passed >= MIN_ENDPOINT_TESTS:
                result.add("endpoint_tests", student, "pass", f"JUnit: {passed} passing tests")
            else:
                result.add("endpoint_tests", student, "fail",
                           f"JUnit: only {passed} passing test(s); need at least {MIN_ENDPOINT_TESTS}")
        else:
            names = sorted(set(TEST_NAME_RE.findall(cicd_text)))
            if len(names) >= MIN_ENDPOINT_TESTS:
                row.update(passing_tests=len(names), evidence="report-text")
                result.add("endpoint_tests", student, "warn",
                           f"{len(names)} test names in {CICD_REPORT} ({', '.join(names[:4])}) but no JUnit XML; "
                           "attach pytest --junitxml output for verifiable evidence")
            else:
                result.gap("endpoint_tests", student, f"no JUnit XML and fewer than {MIN_ENDPOINT_TESTS} test names "
                           f"in {CICD_REPORT}")

    result.metrics = {"students": summary}
    result.recommendations = _recommendations(result)
    return result


def _recommendations(result: ReviewResult) -> list[str]:
    recs = []
    by_student = {}
    for check in result.checks:
        if check.status in ("gap", "fail"):
            by_student.setdefault(check.subject, []).append(check.id)
    for student, ids in sorted(by_student.items()):
        todo = []
        if "precommit_report" in ids or "precommit_scans" in ids:
            todo.append(f"run `scripts/test/precommit-report.sh {student}` and write {PRECOMMIT_REPORT}")
        if "cicd_report" in ids:
            todo.append(f"write {CICD_REPORT} from the GitHub Actions run")
        if "endpoint_tests" in ids:
            todo.append(f"add >= {MIN_ENDPOINT_TESTS} endpoint tests and commit `pytest --junitxml` output")
        recs.append(f"{student}: " + "; ".join(todo))
    if any(c.id == "endpoint_tests" and c.status == "warn" for c in result.checks):
        recs.append("Review: test counts taken from report text are unverified; commit JUnit XML to docs/release-2/<student>/.")
    return recs
