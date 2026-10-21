# Pre-commit security report: Accommodation (student-2)

- Scan: `scripts/test/precommit-report.sh student-2` → [`pre-commit-20261021T025239Z.txt`](pre-commit-20261021T025239Z.txt)
- Branch: `main`   Result: **PASS** — ruff, detect-secrets and pip-audit all pass across the full
  scope (`accommodation-service/`, `ai-services/`, `budget-service/`, `shared/`); see Follow-up
  for an interpreter-version gotcha this run exposed and worked around.
- Scope: `accommodation-service/` added to the shared `files:` regex in `.pre-commit-config.yaml`,
  alongside `ai-services/`, `budget-service/` and `shared/`. Covers the frontend, backend, database
  and the new top-level `tests/` tier.
- Tools: pre-commit 4.7.0, ruff 0.17.1 (`ruff.toml`: classic defaults + `S`), detect-secrets 1.5.0
  (`.secrets.baseline`), pip-audit 2.10.1.

| Hook | Result | Findings | Fixed | Accepted |
|---|---|---|---|---|
| ruff (S) | Passed | 12 | 9 | 3 |
| detect-secrets | Passed | 0 | 0 | 0 |
| pip-audit | Passed | 12 advisories in 2 packages (accommodation-service); 4 pre-accepted (rag-server, see `.pre-commit-config.yaml`) | 12 | 4 (pre-existing) |

## Hooks on a real commit

`pre-commit install` was run for this clone (`.git/hooks/pre-commit` now exists and fires on
every `git commit`, not just a manual `pre-commit run`) — pinned to a durable `pipx`-installed
Python 3.14 `pre-commit` so the interpreter gotcha in Follow-up can't recur for this user.
[`pre-commit-blocked-commit-demo.txt`](pre-commit-blocked-commit-demo.txt) records an actual
`git commit` against a planted canary file under `accommodation-service/backend/src/` (never
really committed):

1. **Blocked, via a real `git commit`.** A fake AWS access key and a `subprocess.run(...,
   shell=True)` call were rejected. Ruff reported S602, detect-secrets reported an AWS Access
   Key; both hooks exited non-zero, `git commit` exited without creating a commit (`HEAD` stayed
   at `df402f0` throughout), and the hook's own stash/restore cycle left every other unstaged
   change in this report untouched (verified: full backend test suite still passed afterwards).
   The canary was `git reset` and deleted immediately after, never staged again.
2. **Allowed, hooks ran clean.** The real `accommodation-service/` changes in this report (Ruff,
   detect-secrets and pip-audit all pass against every file described below).

## Fixed

- `accommodation-service/backend/requirements.txt`, `accommodation-service/frontend/requirements.txt`
  pip-audit:
  - `flask-cors` 4.0.1 → 6.0.0 (PYSEC-2024-71, PYSEC-2026-1383, PYSEC-2026-1384, PYSEC-2026-1385)
  - `requests` 2.32.3 → 2.34.2 (PYSEC-2026-1872, PYSEC-2026-2275)

  All Accommodation backend and frontend unit tests pass on the new versions.
- `accommodation-service/backend/src/app.py` E402 (6 findings; imports after the `sys.path`
  mutation): the `flask`/`flask_cors` imports now sit above the `sys.path` edit, and the local
  `routes.*` blueprint imports moved inside `create_app()` — the same pattern already used in
  `budget-service/budget-be/app.py`. No E402 findings remain.
- `accommodation-service/backend/app.py`, `accommodation-service/frontend/app.py`,
  `accommodation-service/backend/tests/test_ai_mode.py` F401 (3 findings): unused `pytest` import
  and unused `typing.Any` / `flask.jsonify` imports removed (`ruff --fix`).

## Accepted (with justification)

- `accommodation-service/backend/src/app.py`, `accommodation-service/database/src/app.py`,
  `accommodation-service/frontend/app.py` S104 (bind to all interfaces, one each):
  `# noqa: S104 - container bind`. Each process runs inside a container and must listen on
  `0.0.0.0` to be reachable from the other tiers and, in Docker, from the host; exposure is
  controlled by Docker networking, not the bind address. Same justification already accepted for
  the equivalent lines in Budget's and the shared tier's services.
- `accommodation-service/backend/app.py` F401 (`create_app` imported but unused):
  `# noqa: F401 - create_app is re-exported for tests to import`. The thin wrapper script imports
  `create_app` only so `backend/tests/*.py` can `from app import create_app`; removing it (as the
  auto-fix initially did) breaks every backend test module.

## Follow-up

- **Interpreter gotcha (worked around, not a repo bug):** the first attempt at this scan, run
  under this host's default Python 3.15, failed `pip-audit` on `ai-services/rag-server/requirements.txt`
  with `ModuleNotFoundError: No module named 'pkg_resources'` while building `pandas` from
  source — Python 3.15 is too new for pandas' pinned version to have a prebuilt wheel, and its
  `setup.py` build path needs `pkg_resources`. Confirmed this is purely an interpreter-version
  issue, not a repo regression: re-running the exact same scan with `pre-commit` itself installed
  under Python 3.14 (`/opt/homebrew/Cellar/python@3.14/.../bin/python3.14`) passed clean, matching
  Budget's (`student-4/pre-commit-20261020T121232Z.txt`) and the shared tier's own clean runs. CI's
  `lint-security` job already pins `actions/setup-python@v5` to `python-version: "3.11"`, well
  below the point where this would ever trigger — this is a local-sandbox-only gotcha, worth
  knowing if anyone else runs `scripts/test/precommit-report.sh` on a machine whose default
  `python3` is newer than 3.14.
- `requests`/`flask-cors` are now pinned above every currently known advisory, but pip-audit only
  checks what it resolves today — re-run after any future bump.
