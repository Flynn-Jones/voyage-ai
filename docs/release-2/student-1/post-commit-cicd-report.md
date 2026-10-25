# Post-commit CI/CD report: Destination (Student 1), Release 2

- **Result: SUCCESS** for the tested software commit `e223429db551d11541c8dba981fc2da5b81ca917` (`main`). This is the tested software SHA; it is not claimed to be the final repository SHA (see `docs/release-2/release-evidence-index.md`). Any later evidence or workflow commit needs its own `student-1` run.
- Workflow: `.github/workflows/student-1.yml` (triggers: pushes to `main`, `feat/destination-service` and `student-1/**`, unfiltered by path; `workflow_dispatch`; and `workflow_call` from `cloud-deployment.yml`)

## GitHub Actions run

| Field | Value |
|---|---|
| Run URL | https://github.com/Flynn-Jones/voyage-ai/actions/runs/40154654944 |
| Commit SHA | `e223429db551d11541c8dba981fc2da5b81ca917` (matches `commit_sha` in the downloaded `report.json`) |
| Trigger / branch | `push` / `main`, generated 2026-10-25T02:31:14Z |
| Conclusion | success |
| Artifacts (all unexpired when checked) | `student-1-live-test-results` (`live-tests.xml`, `endpoint-tests.md`), `student-1-offline-test-results` (`offline-tests.xml`), `student-1-report` (`report.json`, `report.md`, `run-view.md`, `live/`) |
| JUnit totals read from the artifacts | live: 35 tests, 0 failures or errors, 3 skipped (the AI-compare live tests, skipped because CI runs with every AI flag `false`); offline: 276 tests, 0 failures or errors |

The raw JUnit files are not committed; the artifacts above are the authoritative copy. The dependency on the five other student workflows is enforced by `cloud-deployment.yml`, not by this file.

## The two endpoint functions tested

The existing live-stack job already runs `test_destinations_api.py` against the Student 1 backend; no second test step was added. A new step, **Record the two endpoint-function tests from the live JUnit**, reads that JUnit, writes `endpoint-tests.md` and fails the job if either test did not pass. The `evidence-pack` job appends it to `report.md`.

| Test | Endpoint function | What it asserts |
|---|---|---|
| `test_destinations_api.py::test_seed_count` | `GET /api/destinations` → `list_destinations` | 200 and at least 10 seeded records |
| `test_destinations_api.py::test_crud_lifecycle` | `POST /api/destinations` → `create_destination` | 201 create, then GET, PUT, DELETE and a 404 on the deleted record |

## Local run of the same tests (not CI)

Stack: `student-1/docker-compose.yml` built and started under a throwaway project name, with the CI profile (`DESTINATION_AI_ENABLED`, `DESTINATION_MCP_ENABLED`, `DESTINATION_RAG_ENABLED`, `DESTINATION_MULTI_AGENT_ENABLED` all `false`). Command: `pytest student-1/tests -m live -v`.

- **35 tests: 32 passed, 3 skipped, 0 failed.** The 3 skipped are the AI-compare live tests, which skip when `DESTINATION_AI_ENABLED=false`.
- `test_seed_count`: PASSED. `test_crud_lifecycle`: PASSED.
- The same script the workflow uses to build `endpoint-tests.md` was run on this JUnit and exited 0:

| Test | Endpoint function | Result |
|---|---|---|
| `test_destinations_api.py::test_seed_count` | GET /api/destinations -> list_destinations | PASS |
| `test_destinations_api.py::test_crud_lifecycle` | POST /api/destinations -> create_destination (then GET/PUT/DELETE/404) | PASS |

Also passing in the same run, the disabled-mode tests for the Release 2 switches: `ai-compare` returns 503 `disabled`; every `/api/destinations/multi-agent/*` route returns 503 `disabled`; `/multi-agent` shows the disabled message and no form; `/health` reports `features` all `false`; the MCP and RAG disabled tests still pass.

AI-enabled profile (same stack, flags unset, host Ollama running): **22 passed, 13 skipped** (the skips are the flag-gated disabled-mode tests), including the 3 live AI-compare tests (Tokyo/Kyoto comparison, three consecutive calls, unknown city 404).

The raw JUnit is not committed because it embeds local paths and the hostname; the CI artifact for run 40154654944 (table above) is the authoritative copy.

## Other checks

| Check | Result |
|---|---|
| Student 1 offline tests (`pytest student-1/tests -m "not live"`) on the upgraded dependencies | 276 passed |
| Shared Multi-Agent Server tests (`python -m pytest`, includes 35 for `destination_recommend`) | 165 passed |
| `docker compose config --quiet` for `student-1/docker-compose.yml` and the root file | both valid; root still maps `destination-backend` to `5011:5001` |
| CI jq assertion (AI, MCP, RAG, Multi-Agent all `"false"`; `MULTI_AGENT_SERVICE_URL` ends `:7004`) against the real rendered Compose, run as `bash -e` | passes with the CI env; fails if any one flag is true |
| Student 1 stack with Multi-Agent Server stopped (flag on) | `/multi-agent` and the backend answer "unavailable" (HTTP 503); create, read, update and delete still work |
