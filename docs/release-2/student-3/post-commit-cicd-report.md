# Post-commit CI/CD report: Activity (student-3)

**Status note:** the steps below were run locally, using the same commands as
`.github/workflows/student-3.yml`'s jobs, against real Docker containers. They have not yet run
as a GitHub Actions workflow; that needs these changes pushed. Every number and transcript here
is real output from this machine.

## Pipeline

`.github/workflows/student-3.yml` was expanded from build plus `/health` checks to the same job
graph as student-1.yml, with a pre-commit security job added:

```
security-scans ─┐
offline-tests ──┼─> build-and-smoke ─> evidence-pack
compose-validate┘
```

The stack runs with the shared MCP, RAG and Multi-Agent routes switched off
(`ACTIVITY_*_ENABLED=false`), as in the cloud.

| Job | What it checks | Result (reproduced locally) |
|---|---|---|
| `security-scans` | the repo's own pre-commit hooks (ruff S rules, detect-secrets, pip-audit) over `activity-service/`, and that `activity-service/` is in the pre-commit scope | **PASS**: see [`pre-commit-security-report.md`](pre-commit-security-report.md) and [`scenario-transcripts/lint-security.txt`](scenario-transcripts/lint-security.txt) |
| `offline-tests` | every offline test in `activity-service/tests/`: backend, database, frontend, shared MCP/RAG bridges | **PASS**: 178 passed |
| `compose-validate` | the compose file renders, declares exactly frontend/backend/database, and has MCP, RAG and Multi-Agent set to `false` | **PASS** (and correctly fails when a flag is on) |
| `build-and-smoke` | builds and starts the 3 containers, waits for health, runs `pytest -m live`, **fails unless both endpoint functions pass**, checks MCP/RAG/Multi-Agent return 403, smoke-checks public routes | **PASS**: 6/6 live tests, both endpoint functions PASS, 3/3 routes 403 |
| `evidence-pack` | `report.json`, `report.md` (with the endpoint-test table) and the run URL, uploaded as the `student-3-report` artifact | runs after `build-and-smoke` |

The endpoint-function gate reads the live JUnit, writes this table to `reports/endpoint-tests.md`,
and exits non-zero if either test is missing or not passed. It was checked against the real JUnit
(both PASS, exit 0) and against a copy with one test marked failed (exit 1):

| Test | Endpoint function | Result |
|---|---|---|
| `test_backend_endpoints.py::test_add_activity_is_201_and_persists_in_the_database` | POST /api/add_activity -> add_activity (201, read back from the database) | PASS |
| `test_backend_endpoints.py::test_view_activities_filters_and_sorts` | GET /api/view_activities -> view_activities (filters and sort) | PASS |

## Unit tests (offline, no containers)

```
activity-service$ python -m pytest --junitxml=junit-unit-tests.xml
178 passed, 6 deselected in 4.06s
```

The 6 deselected tests are the live endpoint tests below; `pytest.ini` skips them unless
`-m live` is given. JUnit XML: [`junit-unit-tests.xml`](junit-unit-tests.xml).

## Endpoint tests (live containers)

Real containers (`docker compose up -d --build` in `activity-service/`), health-checked on
`:6003`, `:5003` and `:3003`, then:

```
activity-service$ python -m pytest -m live --junitxml=junit-endpoint-tests.xml -v
tests/test_backend_endpoints.py::test_add_activity_is_201_and_persists_in_the_database PASSED
tests/test_backend_endpoints.py::test_add_activity_rejects_invalid_input_with_400[payload0-missing required field(s): activity_name] PASSED
tests/test_backend_endpoints.py::test_add_activity_rejects_invalid_input_with_400[payload1-activity_cost must be a number] PASSED
tests/test_backend_endpoints.py::test_view_activities_lists_a_created_record PASSED
tests/test_backend_endpoints.py::test_view_activities_filters_and_sorts PASSED
tests/test_backend_endpoints.py::test_view_activities_rejects_a_bad_filter_with_400 PASSED
====================== 6 passed, 178 deselected in 0.56s =======================
```

The two required backend endpoint functions, in `backend/routes/activity_routes.py`, are:

- **`add_activity`** (`POST /api/add_activity`): a valid record returns 201 and is read back from
  the database tier directly (`:6003`), including its scheduled time, to prove it was saved.
  Missing fields and a non-numeric cost return 400.
- **`view_activities`** (`GET /api/view_activities`): a created record appears in the list. The
  type, cost, duration and date filters and the cost sort return exactly the expected records. A
  bad filter returns 400.

Both run against the live backend on `:5003`, not mocks. Each test deletes the records it
creates. JUnit XML: [`junit-endpoint-tests.xml`](junit-endpoint-tests.xml). Full transcript:
[`scenario-transcripts/endpoint-tests.txt`](scenario-transcripts/endpoint-tests.txt).

## Shared-AI routes disabled (verify-ai-disabled)

```
PASS /api/activity/mcp/list-activities -> 403 {"error": "MCP mode is disabled.", "status": "error"}
PASS /api/activity/rag/answer -> 403 {"error": "RAG mode is disabled.", "status": "error"}
PASS /api/activity/multi-agent/workflow -> 403 {"error": "Multi-Agent mode is disabled.", "status": "error"}
```

AI Mode (Ollama summaries and chat) has no on/off switch, so it isn't part of this check.

## How to run it yourself

```bash
# pre-commit (security scans)
pip install pre-commit && pre-commit install
pre-commit run --files $(git ls-files activity-service)

# post-commit pipeline, locally
cd activity-service
pip install -r tests/requirements.txt
python -m pytest                                    # unit-tests
ACTIVITY_MCP_ENABLED=false ACTIVITY_RAG_ENABLED=false ACTIVITY_MULTI_AGENT_ENABLED=false \
  docker compose up -d --build                      # build + start
python -m pytest -m live -v                         # endpoint-tests
docker compose down
```
