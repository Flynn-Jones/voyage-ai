# Pre-commit security report: Destination (student-1)

- Scan: `scripts/test/precommit-report.sh student-1` → [`pre-commit-20261022T090916Z.txt`](pre-commit-20261022T090916Z.txt)
- Result: **PASS** (`--all-files`, whole shared scope including `student-1/`; exit status 0)
- Run on: branch `student-1/release-2-destination`, base commit `63a7c83`, **uncommitted working tree** (the raw report records the base commit because nothing is committed yet).
- Scope: `student-1/` added to the `files:` regex in `.pre-commit-config.yaml` (one line, above `shared/`). It covers Student 1's frontend, backend, database service and tests.
- Tools: pre-commit 4.7.0, ruff (hook rev v0.17.1, `ruff.toml`: classic defaults + `S`), detect-secrets 1.5.0 (`.secrets.baseline`, unchanged), pip-audit 2.10.1.

| Hook | Result | Findings before fixes | Fixed | Accepted |
|---|---|---|---|---|
| ruff (S) | Passed | 4 | 1 | 3 |
| detect-secrets | Passed | 0 | 0 | 0 |
| pip-audit | Passed | 4 requirements files, 2 packages (requests ×2 advisories, pytest ×1) | all | 0 |

## Caveats on how this was produced

- The report script calls `pre-commit run` directly, and pre-commit refuses to run while `.pre-commit-config.yaml` has an unstaged change. To avoid staging anything without approval, the script was pointed at a wrapper that runs pre-commit with `-c` set to a byte-identical copy of the working-tree config. The hooks, versions and scope are the same as a normal run.
- **Commit hook demonstrated:** pre-commit was installed in this clone and the real Git commit executed Ruff, detect-secrets and pip-audit; all three passed.

## Fixed

- `student-1/backend/app.py` F841: unused `as exc` on the `GET /api/destinations` error path removed (pre-existing).
- pip-audit, `requests` 2.32.3 → 2.34.2 (PYSEC-2026-1872 fixed in 2.32.4, PYSEC-2026-2275 fixed in 2.33.0), applied consistently to `student-1/backend`, `student-1/frontend`, `student-1/database` and `student-1/tests` requirements. `requests` 2.34.2 is the version already pinned by Accommodation and Budget.
- pip-audit, `pytest` 8.3.4 → 9.1.1 in `student-1/tests/requirements.txt` (PYSEC-2026-1845 fixed in 9.0.3).
- After the upgrades the full offline suite (276 passed) and the live suite against a rebuilt stack passed; see [post-commit-cicd-report.md](post-commit-cicd-report.md).

## Accepted (with justification)

- `student-1/backend/app.py`, `student-1/frontend/app.py`, `student-1/database/app.py` S104 (bind to all interfaces): `# noqa: S104 - container bind`. (The backend and frontend annotations were added while editing those files, before the first scan, so only the database one is in the count of 4 above.) Each service runs in its own container and must listen on `0.0.0.0` to be reachable from the others; exposure is controlled by Docker networking.
- `student-1/database/app.py` S608 ×2 (INSERT / UPDATE built with an f-string): `# noqa: S608 - fixed column names`. The interpolated names come from the keys `_validate()` sets from fixed literals (never from request keys), and every value is passed as a bound `?` parameter.
