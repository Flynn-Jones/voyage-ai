# Post-commit CI/CD report: Destination (Student 1), Release 2

- **Result: PASS** (GitHub Actions conclusion `success`) for the final code commit `48bb3cc6a76d2647d1ca00bedf52ee72f08c33e9` on `main` (the PR #28 merge; same tree as `8b9e357`). Verified with `gh run view` / `gh run download` on 2026-10-25.
- Workflow: `.github/workflows/student-1.yml` (triggers: pushes to `main`, `feat/destination-service` and `student-1/**`, unfiltered by path; `workflow_dispatch`; and `workflow_call` from `cloud-deployment.yml`)

## GitHub Actions run (final code SHA)

| Field | Value |
|---|---|
| Run URL | https://github.com/Flynn-Jones/voyage-ai/actions/runs/40160985545 |
| Commit SHA | `48bb3cc6a76d2647d1ca00bedf52ee72f08c33e9` (matches `commit_sha` in the run's `report.json`) |
| Trigger / branch | `push` / `main`, attempt 1, 2026-10-25 04:11:41–04:13:02 UTC |
| Conclusion | success. Jobs `offline-tests`, `compose-validate`, `build-and-smoke`, `evidence-pack` all success |
| JUnit (unmodified CI artifacts copied into this folder; untracked until the evidence commit) | [`ci-run-40160985545-48bb3cc/live-tests.xml`](ci-run-40160985545-48bb3cc/live-tests.xml): 35 tests, 0 failures or errors, 3 skipped (the AI-compare live tests; CI runs with every AI flag `false`). [`offline-tests.xml`](ci-run-40160985545-48bb3cc/offline-tests.xml): 276 tests, 0 failures, errors or skips. [`endpoint-tests.md`](ci-run-40160985545-48bb3cc/endpoint-tests.md): both endpoint functions PASS |

The dependency on the five other student workflows is enforced by `cloud-deployment.yml`, not by this file.

### Historical runs

- [40154654944](https://github.com/Flynn-Jones/voyage-ai/actions/runs/40154654944): `push` / `main` on `e223429db551d11541c8dba981fc2da5b81ca917`, 2026-10-25, success. Same totals: live 35 (3 skipped), offline 276. Its JUnit was not committed.

## The two endpoint functions tested

The existing live-stack job already runs `test_destinations_api.py` against the Student 1 backend; no second test step was added. A new step, **Record the two endpoint-function tests from the live JUnit**, reads that JUnit, writes `endpoint-tests.md` and fails the job if either test did not pass. The `evidence-pack` job appends it to `report.md`.

| Test | Endpoint function | What it asserts |
|---|---|---|
| `test_destinations_api.py::test_seed_count` | `GET /api/destinations` → `list_destinations` | 200 and at least 10 seeded records |
| `test_destinations_api.py::test_crud_lifecycle` | `POST /api/destinations` → `create_destination` | 201 create, then GET, PUT, DELETE and a 404 on the deleted record |

## Local run of the same tests (not CI, historical)

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

The local raw JUnit is not committed because it embeds local paths and the hostname. The CI JUnit for run 40160985545 (table above) is the authoritative copy. The GitHub artifact is the original; `ci-run-40160985545-48bb3cc/` holds a byte-identical copy that becomes part of the repository with the evidence commit.

## Other checks

| Check | Result |
|---|---|
| Student 1 offline tests (`pytest student-1/tests -m "not live"`) on the upgraded dependencies | 276 passed |
| Shared Multi-Agent Server tests (`python -m pytest`, includes 35 for `destination_recommend`) | 165 passed |
| `docker compose config --quiet` for `student-1/docker-compose.yml` and the root file | both valid; root still maps `destination-backend` to `5011:5001` |
| CI jq assertion (AI, MCP, RAG, Multi-Agent all `"false"`; `MULTI_AGENT_SERVICE_URL` ends `:7004`) against the real rendered Compose, run as `bash -e` | passes with the CI env; fails if any one flag is true |
| Student 1 stack with Multi-Agent Server stopped (flag on) | `/multi-agent` and the backend answer "unavailable" (HTTP 503); create, read, update and delete still work |
