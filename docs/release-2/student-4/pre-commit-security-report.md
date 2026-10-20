# Pre-commit security report: Budget (student-4)

- Scan: `scripts/test/precommit-report.sh student-4` → [`pre-commit-20261020T121232Z.txt`](pre-commit-20261020T121232Z.txt)
- Commit: `c4d7827` (branch `student-4/release-2-budget-reconciler`)   Result: **PASS**
- Scope: `budget-service/` added to the shared `files:` regex in `.pre-commit-config.yaml` (commit `696911f`),
  alongside `ai-services/` and `shared/`. Covers budget-fe, budget-be, budget-db and the Budget tests.
- Tools: pre-commit 4.7.0, ruff 0.17.1 (`ruff.toml`: classic defaults + `S`), detect-secrets 1.5.0
  (`.secrets.baseline`, empty), pip-audit 2.10.1.

| Hook | Result | Findings | Fixed | Accepted |
|---|---|---|---|---|
| ruff (S) | Passed | 4 | 1 | 3 |
| detect-secrets | Passed | 0 | 0 | 0 |
| pip-audit | Passed | 7 advisories in 3 packages | 7 | 0 |

## Hooks on a real commit

[`pre-commit-commit-run.txt`](pre-commit-commit-run.txt) records the installed git hook running on `git commit`:

1. **Blocked.** A planted canary in `budget-service/budget-be/services/` (fake AWS key, hardcoded password,
   `subprocess.run(..., shell=True)`) was rejected. Ruff reported S105 and S602, detect-secrets reported an
   AWS Access Key and a Secret Keyword, git exited 1 and HEAD was unchanged. The canary was removed and never
   committed; the fake values are redacted in the log.
2. **Allowed.** The scope change itself (`696911f`).
3. **Allowed, hooks ran.** A new `budget-service/README.md` (`c4d7827`): detect-secrets Passed.

## Fixed

- `budget-service/budget-be/requirements.txt` pip-audit (`0a08f6c`):
  - flask 3.0.3 → 3.1.3 (PYSEC-2026-2151)
  - flask-cors 4.0.1 → 6.0.5 (PYSEC-2024-71, PYSEC-2026-1383, PYSEC-2026-1384, PYSEC-2026-1385)
  - requests 2.32.3 → 2.34.2 (PYSEC-2026-1872, PYSEC-2026-2275)

  All Budget unit and live endpoint tests pass on the new versions.
- `budget-service/budget-be/app.py` E402 (imports after code): blueprints are now imported inside
  `create_app()` instead of being suppressed (`714c830`).
- `budget-service/budget-be/app.py:49`, `budget-service/budget-db/app.py:164` (S201 risk): `debug=True` replaced
  with `debug=os.environ.get("FLASK_DEBUG") == "1"`, so the Werkzeug debugger (remote code execution) is off unless
  explicitly enabled (`d2d61dd`). Containers run under gunicorn, which never enables it.

## Accepted (with justification)

- `budget-service/budget-be/app.py:48` S104 (bind to all interfaces): `# noqa: S104 - container bind`. The
  backend runs inside a container and must listen on `0.0.0.0` to be reachable from the frontend's nginx proxy
  and from the other containers. Exposure is controlled by Docker networking and, in the cloud, by the ALB and
  task security groups.
- `budget-service/budget-db/app.py:163` S104: same reason, for budget-db.
- `budget-service/budget-db/app.py:135` S608 (SQL built with an f-string):
  `# noqa: S608 - whitelisted column names, bound values`. Only column names from a fixed whitelist are
  interpolated into `SET` (`UPDATABLE_FIELDS`; any other key in the request is ignored). Every value is
  passed as a bound `?` parameter, and invalid values return 400 before the query is built (`7f99199`).

## Follow-up

- `openai` and `python-dotenv` in `budget-be/requirements.txt` are unpinned. pip-audit checks the version it
  resolves today, so pin them to keep the audit reproducible.
