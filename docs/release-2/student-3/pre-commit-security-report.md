# Pre-commit security report: Activity (student-3)

- Scan: `scripts/test/precommit-report.sh student-3` → [`pre-commit-20261022T121139Z.txt`](pre-commit-20261022T121139Z.txt)
- Commit: `7939482` plus the working-tree changes described below.   Result: **PASS**. Ruff,
  detect-secrets and pip-audit all pass across the full scope.
- Scope: `activity-service/` added to the shared `files:` regex in `.pre-commit-config.yaml`, next
  to `accommodation-service/`, `ai-services/`, `budget-service/`, `itinerary-service/`, `student-1/`
  and `shared/`. It covers the frontend, backend and database tiers and the top-level `tests/`.
- Tools: pre-commit 4.7.0, ruff 0.17.1 (`ruff.toml`: classic defaults plus `S`), detect-secrets 1.5.0
  (`.secrets.baseline`), pip-audit 2.10.1.

| Hook | Result | Findings | Fixed | Accepted |
|---|---|---|---|---|
| ruff (S) | Passed | 6 | 1 | 5 |
| detect-secrets | Passed | 0 | 0 | 0 |
| pip-audit | Passed | 7 advisories in 3 packages | 7 | 0 |

## Hooks blocking insecure code

[`pre-commit-blocked-commit-demo.txt`](pre-commit-blocked-commit-demo.txt) records the configured
hooks run against a throwaway file planted under `activity-service/backend/`:

1. **Blocked.** The file held a fake AWS access key (AWS's documented example
   `AKIAIOSFODNN7EXAMPLE`) and a `subprocess.run(..., shell=True)` call. Ruff reported **S602** and
   detect-secrets reported an **AWS Access Key**. Both hooks failed with a non-zero exit, which
   refuses a `git commit`. The file was never staged or committed, and was deleted right after the
   run. `.secrets.baseline` was not changed.
2. **Allowed.** The real `activity-service/` files pass all three hooks.

The demo ran the hooks with `pre-commit run --files`. After `pre-commit install`, the same hooks run
on every `git commit`; the video shows that.

## Fixed

- `activity-service/backend/requirements.txt`, `frontend/requirements.txt`, `database/requirements.txt`
  pip-audit:
  - `flask` 3.0.3 → 3.1.3 (PYSEC-2026-2151)
  - `flask-cors` 4.0.1 → 6.0.5 (PYSEC-2024-71, PYSEC-2026-1383, PYSEC-2026-1384, PYSEC-2026-1385)
  - `requests` 2.32.3 → 2.34.2 (PYSEC-2026-1872, PYSEC-2026-2275)

  These match the versions the other services pin. All 178 offline tests and the 6 live endpoint
  tests pass on the new versions.
- `activity-service/tests/test_frontend_routes.py` F401: unused `make_fake_backend` import removed.

## Accepted (with justification)

- `activity-service/backend/app.py`, `database/app.py`, `frontend/app.py` S104 (bind to all
  interfaces, one each): `# noqa: S104 - binds inside its container; only the published port is reachable`.
  Each process runs in a container and must listen on `0.0.0.0` so the other tiers and the host
  can reach it. Docker networking controls exposure, not the bind address. Accommodation, Budget
  and the shared tier accept the same lines for the same reason.
- `activity-service/tests/report.py` S603 (`subprocess` call):
  `# noqa: S603 - fixed argv (this interpreter running pytest on tests/), no user input`. The
  command is `[sys.executable, "-m", "pytest", <tests dir>, --junit-xml=<temp file>]`, with no
  shell and nothing from the user.
- `activity-service/tests/report.py` S314 (`xml.etree` parse):
  `# noqa: S314 - JUnit XML pytest just wrote to our private temp dir`. The only XML parsed is the
  JUnit file pytest writes into a `tempfile.TemporaryDirectory()` a moment earlier, never
  external input.

## Same checks in CI

`.github/workflows/student-3.yml`'s `lint-security` job runs the same three tools at the same
versions on every push: `ruff check --config ruff.toml activity-service`,
`detect-secrets scan activity-service`, and `pip-audit -r` for each tier's requirements. A local
reproduction is in [`scenario-transcripts/lint-security.txt`](scenario-transcripts/lint-security.txt).
See [`post-commit-cicd-report.md`](post-commit-cicd-report.md).

## Follow-up

- pip-audit only checks what it resolves today. Re-run it after any dependency bump.
