"""testing-review: per-student Release 2 testing evidence.

OBSERVE  docs/release-2/student-N/ for N = 1..5:
           pre-commit-security-report.md   (+ raw pre-commit-<UTC>.txt from scripts/test/precommit-report.sh)
           post-commit-cicd-report.md
           *.xml JUnit results (any depth), if present
         the student's endpoint test source file (REQUIRED_ENDPOINT_TESTS)
CHECK    report present, scans passing, CI/CD report present and not failing, and the two
         required endpoint testcases (the ones .github/workflows/student-N.yml requires) are each
         defined in the source file AND have a passing JUnit <testcase> of that exact name.
         Unrelated passing tests do not count, and JUnit for a test no longer in the source is stale.
         Three outcomes are kept apart:
           endpoint_tests          fail  a required testcase ran and failed
           endpoint_test_source    fail  a required test is not implemented in the source file
           endpoint_test_evidence  gap   implemented, but no JUnit execution evidence (attach it)
Anything missing is a gap for that student, never an error.
"""
import re
import xml.etree.ElementTree as ET  # noqa: S405 - parses the team's own JUnit files from the repo, not untrusted input
from pathlib import Path

from core.review import ReviewResult

MODE = "testing-review"
STUDENTS = tuple(f"student-{n}" for n in range(1, 6))
# The two endpoint tests each student's workflow requires (REQUIRED in student-N.yml's "Record the
# two endpoint-function tests" step): student -> (source file, JUnit module, test names).
REQUIRED_ENDPOINT_TESTS = {
    "student-1": ("student-1/tests/test_destinations_api.py", "test_destinations_api",
                  ("test_seed_count", "test_crud_lifecycle")),
    "student-2": ("accommodation-service/tests/test_endpoints.py", "test_endpoints",
                  ("test_post_valid_accommodation_is_201_and_reads_back", "test_get_list_includes_created_record")),
    "student-3": ("activity-service/tests/test_backend_endpoints.py", "test_backend_endpoints",
                  ("test_add_activity_is_201_and_persists_in_the_database", "test_view_activities_filters_and_sorts")),
    "student-4": ("budget-service/tests/test_endpoints.py", "test_endpoints",
                  ("test_post_valid_expense_is_201_and_reads_back", "test_put_valid_update_persists")),
    "student-5": ("itinerary-service/ci/test_endpoints.py", "test_endpoints",
                  ("test_post_create_returns_201_and_persists", "test_put_update_returns_200_and_persists")),
}
PRECOMMIT_REPORT = "pre-commit-security-report.md"
CICD_REPORT = "post-commit-cicd-report.md"
RESULT_RE = re.compile(r"result\W{0,6}(pass|fail)", re.IGNORECASE)
RAW_STATUS_RE = re.compile(r"exit status:\s*\d+\s*\((PASS|FAIL)\)")


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


def junit_cases(paths):
    """Return ([(classname, name, state)], parse_errors) across JUnit XML files.
    state is "failed" (failure/error), "skipped" or "passed"."""
    cases = []
    errors = []
    for path in paths:
        try:
            root = ET.parse(path).getroot()  # noqa: S314 - repo-local CI output, see import note
        except ET.ParseError as exc:
            errors.append(f"{path.name}: {exc}")
            continue
        for case in root.iter("testcase"):
            if case.find("failure") is not None or case.find("error") is not None:
                state = "failed"
            elif case.find("skipped") is not None:
                state = "skipped"
            else:
                state = "passed"
            cases.append((case.get("classname", ""), case.get("name", ""), state))
    return cases, errors


def _junit_state(cases, module: str, test: str) -> str:
    """passed | failed | skipped | absent for one required test (all its parametrized cases)."""
    states = [state for classname, name, state in cases
              if module in classname.split(".") and (name == test or name.startswith(test + "["))]
    if "failed" in states:
        return "failed"
    if not states:
        return "absent"
    return "skipped" if "skipped" in states else "passed"


def _implemented(source: Path, test: str) -> bool:
    if not source.is_file():
        return False
    return re.search(rf"^\s*(?:async\s+)?def {re.escape(test)}\(", source.read_text(encoding="utf-8", errors="replace"),
                     re.MULTILINE) is not None


def review(repo_root: Path) -> ReviewResult:
    result = ReviewResult(MODE, "Testing Review")
    base = repo_root / "docs" / "release-2"
    summary = {}
    for student in STUDENTS:
        student_dir = base / student
        row = {"pre_commit": "missing", "cicd": "missing", "required_tests": None}
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

        # the two required endpoint tests: matched by testcase name, not by counting tests
        junit = sorted(student_dir.rglob("*.xml")) if student_dir.exists() else []
        cases, errors = junit_cases(junit)
        result.sources += [str(p.relative_to(repo_root)) for p in junit]
        if errors:
            result.add("junit_parse", student, "warn", "; ".join(errors))
        source_rel, module, tests = REQUIRED_ENDPOINT_TESTS[student]
        source = repo_root / source_rel
        outcome = {}
        stale = []  # JUnit results for a test whose definition is gone from the source
        for test in tests:
            state = _junit_state(cases, module, test)
            if not _implemented(source, test):  # checked first: JUnit XML alone may be stale
                outcome[test] = "not_implemented"
                if state != "absent":
                    stale.append(test)
            elif state in ("passed", "failed"):
                outcome[test] = state
            else:
                outcome[test] = "no_evidence" if state == "absent" else "skipped"
        row["required_tests"] = outcome
        _record_endpoint_tests(result, student, source_rel, module, outcome, cicd_text, stale)

    result.metrics = {"students": summary}
    result.recommendations = _recommendations(result)
    return result


def _record_endpoint_tests(result, student, source_rel, module, outcome, cicd_text, stale=()):
    def names(state):
        return [test for test, value in outcome.items() if value == state]

    failed, missing = names("failed"), names("not_implemented")
    unproven = names("no_evidence") + names("skipped")
    if failed:
        result.add("endpoint_tests", student, "fail",
                   f"required endpoint test(s) failed in JUnit: {', '.join(failed)}", tests=failed)
    if missing:
        stale_note = (f" (JUnit results for {', '.join(t for t in missing if t in stale)} are stale and ignored)"
                      if any(t in stale for t in missing) else "")
        result.add("endpoint_test_source", student, "fail",
                   f"required endpoint test(s) not implemented in {source_rel}: {', '.join(missing)}{stale_note}",
                   tests=missing)
    if unproven:
        mentioned = [test for test in unproven if test in cicd_text]
        note = (f"; {CICD_REPORT} names {', '.join(mentioned)} but a report is not execution evidence"
                if mentioned else "")
        result.gap("endpoint_test_evidence", student,
                   f"no passing JUnit testcase ({module}) for: {', '.join(unproven)}{note}", tests=unproven)
    if not (failed or missing or unproven):
        result.add("endpoint_tests", student, "pass",
                   f"JUnit: both required endpoint tests pass ({', '.join(outcome)})", tests=list(outcome))


def _recommendations(result: ReviewResult) -> list[str]:
    recs = []
    by_student = {}
    for check in result.checks:
        if check.status in ("gap", "fail"):
            by_student.setdefault(check.subject, []).append(check)
    for student, checks in sorted(by_student.items()):
        ids = {c.id for c in checks}

        def tests(check_id):
            return ", ".join(name for c in checks if c.id == check_id for name in c.evidence.get("tests", []))

        todo = []
        if "precommit_report" in ids or "precommit_scans" in ids:
            todo.append(f"run `scripts/test/precommit-report.sh {student}` and write {PRECOMMIT_REPORT}")
        if "cicd_report" in ids:
            todo.append(f"write {CICD_REPORT} from the GitHub Actions run")
        if "endpoint_test_source" in ids:
            todo.append(f"implement the missing required endpoint test(s): {tests('endpoint_test_source')}")
        if "endpoint_tests" in ids:
            todo.append(f"fix the failing required endpoint test(s): {tests('endpoint_tests')}")
        if "endpoint_test_evidence" in ids:
            todo.append(f"provide the JUnit XML from the {student} workflow run (testcase(s): "
                        f"{tests('endpoint_test_evidence')}) under docs/release-2/{student}/")
        if todo:
            recs.append(f"{student}: " + "; ".join(todo))
    return recs
