# Post-commit CI/CD report: Budget (Student 4), Release 2

## Final-SHA GitHub Actions run (added 2026-10-25 by Student 1)

**Result: PASS** (GitHub Actions conclusion `success`), verified with `gh run view` and `gh run download`.

| Field | Value |
|---|---|
| Run URL | https://github.com/Flynn-Jones/voyage-ai/actions/runs/40160985543 |
| Commit SHA | `48bb3cc6a76d2647d1ca00bedf52ee72f08c33e9` (`main`, the PR #28 merge; final code SHA) |
| Trigger | `push` / `main`, attempt 1, 2026-10-25 04:11:41–04:12:54 UTC |
| Jobs | `lint-security`, `unit-tests`, `build`, `endpoint-tests`, `verify-ai-disabled` all success |
| JUnit (unmodified CI artifacts) | [`junit-endpoint-tests.xml`](ci-run-40160985543-48bb3cc/junit-endpoint-tests.xml): 20 tests, 0 failures, errors or skips; [`junit-unit-tests.xml`](ci-run-40160985543-48bb3cc/junit-unit-tests.xml): 25 tests, 0 failures, errors or skips |

| Required endpoint test | Endpoint function | Result |
|---|---|---|
| `tests/test_endpoints.py::test_post_valid_expense_is_201_and_reads_back` | POST /expenses -> create_expense | PASS |
| `tests/test_endpoints.py::test_put_valid_update_persists` | PUT /expenses/<id> -> update_expense | PASS |

Source: [`ci-run-40160985543-48bb3cc/endpoint-tests.md`](ci-run-40160985543-48bb3cc/endpoint-tests.md), written by the workflow's "Record the two endpoint-function tests" step. The run, commit and JUnit described below are the first `student-4` run (2026-10-20, run 39367021651) and are kept as historical evidence.

- **Result: PASS** (all 5 jobs green on the first run of the new pipeline)
- Workflow: `.github/workflows/student-4.yml`
- Run: https://github.com/Flynn-Jones/voyage-ai/actions/runs/39367021651
- Trigger: `push` to `student-4/release-2-budget-reconciler`, commit `d9e7ed6` (before the rebase onto main; the same change is now `f940c2c`), 2026-10-20 03:57–03:58 UTC
- JUnit: [junit-endpoint-tests.xml](junit-endpoint-tests.xml) (CI artifact `junit-endpoint-tests`) and
  [junit-unit-tests.xml](junit-unit-tests.xml) (artifact `student-4-junit-unit-tests`)

## Pipeline

| Job | What it does | Result |
|---|---|---|
| lint-security | ruff with the S rules, detect-secrets, pip-audit on both Budget requirements files | success: "All checks passed!", "no secrets found", "No known vulnerabilities found" ×2 |
| unit-tests | Budget unit tests (Multi-Agent routes with the server mocked, AI switch, health, validation, seeding) + `budget_reconcile` workflow tests | success: 25 Budget unit tests, 0 failures; workflow tests passed |
| build | `docker compose -f docker-compose.yml -f docker-compose.ci.yml build` | success |
| endpoint-tests | Starts the containers with `docker-compose.ci.yml`, waits for `/health`, runs `pytest -m live --junitxml=junit-endpoint-tests.xml` | success: **20 tests, 0 failures, 0 errors, 0 skipped** |
| verify-ai-disabled | With MCP/RAG/Multi-Agent/AI-Mode off, every AI route must answer 403 "disabled" | success: 6/6 PASS |

Triggers: every push to `main` (no path filter, so the cloud-deployment gate always finds a student-4 run for
the commit it deploys), pushes to `student-4/**`, pull requests touching Budget, and manual dispatch.
The old file used `actions/checkout@v7`, which doesn't exist; it now uses `@v4`.

## Endpoint tests (live containers)

POST `/expenses`: a valid create returns 201 and reads back from budget-db; missing or invalid fields return 400
and nothing is stored. PUT `/expenses/<id>`: a valid update persists; an unknown id returns 404; invalid data
returns 400 and the record is unchanged. Every test cleans up its data.
The `ai_proposed` cases come from Ollama; see [ai-assisted-tests.md](ai-assisted-tests.md).

| Test | Result |
|---|---|
| `test_endpoints::test_post_valid_expense_is_201_and_reads_back` | passed |
| `test_endpoints::test_post_missing_required_field_is_400[trip_reference]` | passed |
| `test_endpoints::test_post_missing_required_field_is_400[expense]` | passed |
| `test_endpoints::test_post_missing_required_field_is_400[category]` | passed |
| `test_endpoints::test_post_missing_required_field_is_400[estimated_cost]` | passed |
| `test_endpoints::test_post_missing_required_field_is_400[destination_id]` | passed |
| `test_endpoints::test_post_invalid_field_is_400[overrides0-estimated_cost must be a number]` | passed |
| `test_endpoints::test_post_invalid_field_is_400[overrides1-estimated_cost must be a number]` | passed |
| `test_endpoints::test_post_invalid_field_is_400[overrides2-estimated_cost must be a number]` | passed |
| `test_endpoints::test_post_invalid_field_is_400[overrides3-actual_cost must be a number]` | passed |
| `test_endpoints::test_post_invalid_field_is_400[overrides4-status must be one of]` | passed |
| `test_endpoints::test_post_invalid_field_is_400[overrides5-category must be a non-empty string]` | passed |
| `test_endpoints::test_put_valid_update_persists` | passed |
| `test_endpoints::test_put_unknown_id_is_404` | passed |
| `test_endpoints::test_put_invalid_data_is_400_and_record_unchanged[body0-estimated_cost must be a number]` | passed |
| `test_endpoints::test_put_invalid_data_is_400_and_record_unchanged[body1-status must be one of]` | passed |
| `test_endpoints::test_put_invalid_data_is_400_and_record_unchanged[body2-no updatable fields]` | passed |
| `test_endpoints_ai_proposed::test_ai_proposed_post_edge_case[too_long_expense]` | passed |
| `test_endpoints_ai_proposed::test_ai_proposed_post_edge_case[null_status]` | passed |
| `test_endpoints_ai_proposed::test_ai_proposed_post_edge_case[invalid_category]` | passed |

## AI routes disabled under `docker-compose.ci.yml` (job log)

```text
PASS /budget/mcp/list-expenses -> 403 disabled
PASS /budget/rag/refresh -> 403 disabled
PASS /budget/rag/answer -> 403 disabled
PASS /budget/multi-agent/run -> 403 disabled
PASS /budget/ai-analyse -> 403 disabled
PASS /budget/ai-add-expense -> 403 disabled
```
