# Pre-commit security report: Itinerary Manager (student-5)

- Result: **PASS** for the Student 5 foundation working tree on 2026-10-22.
- Branch: `student-5/itinerary-feature`; base commit: `bebf735071c00b3e61ecbc07ffeacbf23a2326c0`.
- Changes are uncommitted. No commit, push, or blocked-commit demonstration was attempted.
- Raw configured-hook output: [pre-commit-20261022T090853Z.txt](pre-commit-20261022T090853Z.txt).
- Scope: all 59 tracked and new non-ignored `itinerary-service/` files, supplied explicitly to
  `pre-commit run --files`. This is a Student 5 scan, not a claim that every shared/other-student file passed.
- Tools: Python 3.11.2, pre-commit 4.7.0, Ruff 0.17.1, detect-secrets 1.5.0, pip-audit 2.10.1.

## Tooling inspected

The shared `.pre-commit-config.yaml` runs Ruff's classic lint plus `S` security rules from `ruff.toml`,
detect-secrets against `.secrets.baseline`, and `scripts/precommit/pip_audit_requirements.py` against each
requirements file (including resolved transitive dependencies). `scripts/test/precommit-report.sh` saves
a full shared-scope scan. This run used the same configured hooks with explicit Student 5 files and saved
the raw output in the same report format, including the authoritative exit status.

Added only `itinerary-service/|` to the shared scope. The rules, baseline and existing shared dependency
exceptions are unchanged. The baseline had zero findings before and after the scan. No new vulnerability
exceptions or secrets-baseline entries were added.

Reviewed Student 2/4 security reports and their AI/Multi-Agent configuration, plus Student 3's Activity
Multi-Agent client and CI workflow. Student 3 has no `docs/release-2/student-3/` report directory at this
commit. The shared feature clients use a 240-second Multi-Agent timeout; Student 5 follows that convention.

| Check | Final result | Initial findings | Fixed | Accepted with justification |
| --- | --- | --- | --- | --- |
| Ruff lint + S | Passed | 58 | 2 | 56 |
| detect-secrets | Passed | 0 | 0 | 0 |
| pip-audit | Passed | 2 unique advisories in 2 packages | 2 | 0 |

## Fixed

- `itinerary-service/reports/final-runtime/validate_browser.py`: S113, added a 10-second timeout to the
  local Edge discovery request. This bounds a validation harness request without changing feature behavior.
- `itinerary-service/reports/final-runtime/validate_http.py`: F841, removed unused timing state and its
  now-unused import. Validation assertions and recorded results are unchanged.
- `itinerary-service/itinerary-be/requirements.txt`: requests 2.32.5 -> 2.34.2, resolving
  `PYSEC-2026-2275` (scanner minimum fix version 2.33.0).
- `itinerary-service/itinerary-db/requirements.txt`: pytest 8.3.5 -> 9.0.3, resolving
  `PYSEC-2026-1845`. pytest was already a dependency here; this task does not restructure the container.

The initial auditor printed each advisory twice; the table counts distinct package/advisory pairs.
Regression tests ran against the updated requirements in the project virtual environment.

## Accepted with justification

These are explicit, rule-specific annotations; no global rules or scan paths were weakened.

- **2 S104 findings:** backend and database `app.py` bind to `0.0.0.0` so their containers can receive
  requests from the other tiers. Both have inline `# noqa: S104` reasons and retain `debug=False`.
  Network exposure is controlled by Docker/cloud networking, not by binding to loopback inside a container.
- **10 E402 findings:** imports in `test_ai_review.py`, `test_backend_api.py`,
  `test_cross_service_enrichment.py`, `test_database_api.py`, and `tests/test_shared_release1.py` occur after
  service-specific module-path setup. Database tests also evict the backend's identically named modules.
  Moving these imports can break service isolation in the combined suite. Each flagged import has its own
  inline E402 explanation; production imports are unaffected.
- **42 S101 findings:** the optional `validation/live_release1.py` and the two
  `reports/final-runtime/validate_*.py` files are test/evidence harnesses. Their assertions check observed
  results; they are not authorization or production input validation. Each file has a rule-specific
  `# ruff: noqa: S101` explanation. Run these validation scripts without Python's `-O` option.
- **2 S603 findings:** `validation/live_release1.py` starts the fixed Python executable with repository
  scripts or a literal validation program, using argument lists and no shell. The arguments are not user
  requests. Both flagged calls have inline S603 explanations; subprocess behavior is unchanged.

## Foundation behavior and verification

- `AI_ENABLED` defaults to true. When false, the AI review route returns HTTP 403
  `{"error": "AI Mode is disabled"}` before JSON/input validation, database access, or Ollama calls.
- AI-enabled output remains `plan`, `act`, `observe`, `adapt`, with advisory-only recommendations.
- Existing `MCP_ENABLED`/`RAG_ENABLED`, URLs, timeouts and per-request gating are preserved.
- `MULTI_AGENT_ENABLED=false`, `MULTI_AGENT_SERVICE_URL` and `MULTI_AGENT_TIMEOUT_SECONDS=240` are
  configuration only; there are no new Multi-Agent routes, workflow, frontend controls or network calls.
- Invalid/non-positive Multi-Agent timeouts fall back to 240 seconds. Host URL defaults to
  `http://localhost:7004`; Compose uses `http://host.docker.internal:7004`.
- Root Compose uses `ITINERARY_` overrides; standalone Compose uses unprefixed overrides. Both support
  all four flags set to false. AI/Multi-Agent configuration is read at app creation; recreate the backend
  to apply environment changes.

Full Student 5 test command (with `MCP_ENABLED=false`, `RAG_ENABLED=false`, bytecode/cache writes disabled):

```text
.venv/Scripts/python.exe -B -m pytest itinerary-service/itinerary-be/tests itinerary-service/itinerary-db/tests itinerary-service/tests -q -p no:cacheprovider
209 passed in 1.91s
```

This includes the previous 170 tests plus 39 new cases covering AI disabled before validation/dependencies,
enabled/default behavior, Multi-Agent defaults/overrides/invalid timeouts, and CRUD/health independence when
all AI modes are disabled. Existing AI test fixtures explicitly enable the feature they exercise.

Both Compose files passed rendered-JSON checks for defaults and overridden cloud-disabled settings,
including a custom Multi-Agent URL and timeout. Docker emitted the existing user-config access warning;
configuration rendering still exited successfully. Ruff and `git diff --check` passed.

## Reproduce the configured Student 5 scan (PowerShell)

```powershell
$student5Files = @(git ls-files -co --exclude-standard -- itinerary-service)
.\.venv\Scripts\python.exe -m pre_commit run --color never --files $student5Files
```

The sandbox run used an ignored `.venv/pre-commit-cache` and a process-scoped Git safe-directory exception;
neither changes tracked application configuration. Tool/dependency downloads and advisory queries required
network access. The shared Bash report helper remains available for a later full-repository scan.

## Limits and next work

No Git hook was installed and no GitHub Actions, live Docker deployment, browser, real-model, or cloud
validation was performed by this foundation task. Passing local unit/security scans is not a post-commit
CI result. Multi-Agent workflow/lifecycle integration, frontend human review, live endpoint CI/evidence,
and cloud navigation/deployment remain separate Student 5 tasks.
