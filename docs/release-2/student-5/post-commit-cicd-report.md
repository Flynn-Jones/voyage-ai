# Student 5 Release 2 post-commit CI/CD endpoint report

## Final-SHA GitHub Actions run (added 2026-10-25 by Student 1)

**Result: PASS** (GitHub Actions conclusion `success`), verified with `gh run view` and `gh run download`.

| Field | Value |
|---|---|
| Run URL | https://github.com/Flynn-Jones/voyage-ai/actions/runs/40160985592 |
| Commit SHA | `48bb3cc6a76d2647d1ca00bedf52ee72f08c33e9` (`main`, the PR #28 merge; final code SHA) |
| Trigger | `push` / `main`, attempt 1, 2026-10-25 04:11:41–04:12:40 UTC |
| Jobs | single job `Student 5 Release 1 Validation` success. `report.json` records `commit_sha` = `48bb3cc…` |
| JUnit (unmodified CI artifacts) | [`junit-endpoint-tests.xml`](ci-run-40160985592-48bb3cc/junit-endpoint-tests.xml): 27 tests, 0 failures, errors or skips; [`junit-contract-tests.xml`](ci-run-40160985592-48bb3cc/junit-contract-tests.xml): 51 tests, 0 failures or errors, 1 skipped; [`junit-unit-tests.xml`](ci-run-40160985592-48bb3cc/junit-unit-tests.xml): 240 tests, 0 failures, errors or skips |

| Required endpoint test | Endpoint function | Result |
|---|---|---|
| `itinerary-service/ci/test_endpoints.py::test_post_create_returns_201_and_persists` | POST /api/itinerary -> create_item | PASS |
| `itinerary-service/ci/test_endpoints.py::test_put_update_returns_200_and_persists` | PUT /api/itinerary/<id> -> update_item | PASS |

Source: [`ci-run-40160985592-48bb3cc/endpoint-tests.md`](ci-run-40160985592-48bb3cc/endpoint-tests.md), written by the workflow's "Record the two endpoint-function tests" step. The status, run 39765017520 and commit `1fba88d` described below are the earlier run (2026-10-22) and are kept as historical evidence.

GitHub Actions status: **SUCCESS**. The GitHub-hosted `student-5` workflow completed successfully
for the exact tested commit below, including the live POST/PUT endpoint and disabled-service checks.

- Workflow: `.github/workflows/student-5.yml` (`itinerary-release-1`, retained job ID and check name).
- Actions run URL: [39765017520](https://github.com/Flynn-Jones/voyage-ai/actions/runs/39765017520).
- Tested commit SHA: `1fba88d8dc3407486648977b913790848415fef9` (workflow run SHA matched).
- Run date/result: **2026-10-22, SUCCESS**; completed at 10:42:47 UTC.
- Tested endpoints: `POST /api/itinerary` and `PUT /api/itinerary/<id>`.
- Published artifacts verified: `student-5-endpoint-tests` and `student-5-report`.

## Two endpoint functions

`itinerary-service/ci/test_endpoints.py` uses real HTTP to the running backend on port 5005 and
database API on port 6005. No Flask test client or mocked responses are used. An unreachable
service fails the suite rather than skipping. The existing offline test commands remain unchanged
in scope; the live suite runs explicitly after container health checks.

| Owning backend function | Success | Failure cases |
| --- | --- | --- |
| `routes.itinerary.create_item`, `POST /api/itinerary` | 201, positive item ID, all returned fields; subsequent public GET and direct DB API GET prove persistence | Every required field omitted in turn; invalid day, time order and cost return 400 without persistence |
| `routes.itinerary.update_item`, `PUT /api/itinerary/<id>` | 200, same ID, complete returned representation; changed day/times/cost/notes persisted through public and DB API GET | Invalid times/cost/day return 400 with unchanged record; PUT to a created-then-deleted ID returns 404 without recreation |

Every record has a unique `CI-ENDPOINT-<uuid>` trip reference. Fixture teardown finds only that exact
tag, deletes through the owning backend even after assertions fail, and verifies public/DB reads are
404 and no tagged records remain. Cleanup errors fail the suite. CI's final Compose teardown removes
the ephemeral CI volume even on failure. The existing CRUD smoke test also retains normal cleanup and
adds an EXIT trap for failures after obtaining its created ID.

## Disabled baseline

`itinerary-service/docker-compose.ci.yml` overrides `AI_ENABLED`, `MCP_ENABLED`, `RAG_ENABLED` and
`MULTI_AGENT_ENABLED` to literal `false`. CI also asserts the four actual backend-container environment
values. All ten optional routes must return **403 with a JSON error containing "disabled"**:

- POST `ai-review`, `mcp/itinerary`, `rag/answer`, `rag/retrieve`, `rag/refresh`.
- POST `multi-agent/run`; GET run and history; POST review and apply.

All routes above are under `/api/itinerary`. The original Release 1 disabled-route smoke checks remain,
extended for AI and Multi-Agent. No Ollama/MCP/RAG/Multi-Agent server is started in CI. This validates
baseline behavior locally/in CI; it does not deploy a cloud environment.

## Evidence/artifacts

Both uploads use `if: always()` so failures retain diagnostic output and fail the job normally.

- `student-5-endpoint-tests`: `junit-endpoint-tests.xml`, `endpoint-tests.txt`, `http-results.json`,
  plus offline/contract JUnit files and container logs on failure, under `reports/post-commit/`.
- `student-5-report`: existing reports/evidence, generated `report.json`, `report.md`, `run-view.md`,
  the new post-commit results and this report. Generated metadata includes actual Actions run URL,
  SHA, attempt and job status; it does not label failed validations as passed. The existing artifact
  root layout is preserved; a copy of this report is placed under `post-commit/`.

Runtime output under `itinerary-service/reports/post-commit/` is ignored by Git. Download actual CI
JUnit/artifacts after execution; keep only useful concise evidence in this documentation directory.

## Local reproduction

```text
docker compose -f itinerary-service/docker-compose.yml -f itinerary-service/docker-compose.ci.yml config --quiet
docker network inspect microservices-net
# Create microservices-net if it does not exist, then start and wait for all three health checks:
docker compose -f itinerary-service/docker-compose.yml -f itinerary-service/docker-compose.ci.yml up -d --build
python -m pytest -m live itinerary-service/ci -v --junitxml=itinerary-service/reports/post-commit/junit-endpoint-tests.xml
```

Optional URLs: `ITINERARY_BE_URL`, `ITINERARY_DB_URL`; HTTP evidence directory:
`ITINERARY_CI_REPORT_DIR`. Do not run CI's volume-removing teardown against a local demo dataset.
These commands reproduce the local CI checks without deploying a cloud environment.

## Local validation (2026-10-22)

| Check | Result |
| --- | --- |
| Full existing Student 5 suite with all four process flags false | **291 passed** (9.34s) |
| New endpoint suite against rebuilt real Compose containers with the CI override | **27 passed** (33.05s); zero failures/errors/skips |
| Original workflow CRUD smoke and extended disabled-route smoke commands | **PASS** against those same containers |
| Actual backend-container flag values | All four literal `false` during the live suite |
| Base, root and combined CI Compose validation | **PASS**; CI override also wins over all-true host flags |
| Workflow actionlint v1.7.7, YAML parsing, every inline shell step via `bash -n` | **PASS** |
| Configured Ruff/S, detect-secrets and pip-audit | **PASS** |
| `git diff --check` and new-file whitespace review | **PASS** |
| Report generator success/failure metadata; unavailable live service probe | **PASS**; unreachable service fails without skips and retains HTTP evidence |
| Cleanup/restoration | Exact original **12 records** preserved; local container switches restored to all true |

Local JUnit and HTTP/test output are in ignored `itinerary-service/reports/post-commit/`. They record
local execution, not GitHub Actions results. Raw tooling, caches and runtime output are not repository
evidence. The successful GitHub-hosted run and its published artifacts are recorded above.

## Separate workflow results

The separate [integration workflow](https://github.com/Flynn-Jones/voyage-ai/actions/runs/39765015676)
and [cloud workflow](https://github.com/Flynn-Jones/voyage-ai/actions/runs/39765014073) reported failures
with zero jobs. They were not part of the successful Student 5 endpoint-test result. No cloud
deployment work was performed for this validation.
