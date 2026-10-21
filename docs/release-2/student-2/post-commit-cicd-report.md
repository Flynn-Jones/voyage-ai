# Post-commit CI/CD report: Accommodation (student-2)

**Status note:** the steps below were run locally, command-for-command identical to
`.github/workflows/student-2.yml`'s jobs, against real Docker containers — they have not yet run
as an actual GitHub Actions workflow (that needs this branch pushed and a run triggered; see
`contribution-log.md` for what's still outstanding). Every number and transcript here is real
output from this host, not fabricated.

**Independently verified by the shared agentic_loop's `testing-review` mode** (reads this report
+ the JUnit XML below from disk, not hand-checked): student-2 passes every check — see
[`../agentic-loop/testing-review-20261021T025747Z.md`](../agentic-loop/testing-review-20261021T025747Z.md).
The mode's overall verdict is INCOMPLETE only because students 1/3/5 have no `docs/release-2/student-N/`
yet — not a student-2 finding.

## Pipeline

`.github/workflows/student-2.yml` — rewritten from a 3-job smoke test to Budget's 5-job shape:

| Job | What it does | Result (reproduced locally) |
|---|---|---|
| `lint-security` | ruff (S rules), detect-secrets, pip-audit against `accommodation-service/` | **PASS** — see [`pre-commit-security-report.md`](pre-commit-security-report.md) |
| `unit-tests` | mocked backend routes (`backend/tests/`) + `accommodation_shortlist` workflow tests | **PASS** — 64 + 23 = 87 tests |
| `build` | `docker compose -f docker-compose.yml -f docker-compose.ci.yml build` | **PASS** — all 3 images (frontend/backend/database) build clean |
| `endpoint-tests` | live `pytest -m live` against real containers | **PASS** — 6/6 |
| `verify-ai-disabled` | MCP, RAG, AI-Mode, Multi-Agent all → 403 under `docker-compose.ci.yml` | **PASS** — 4/4 |

## Unit tests (mocked MCP/MAS/LLM)

```
accommodation-service/backend$ python -m pytest --junitxml=junit-unit-tests.xml -q
................................................................         [100%]
64 passed in 0.63s
```

```
ai-services/multi-agent-server$ python -m pytest tests/test_accommodation_shortlist.py --junitxml=junit-mas-accommodation-shortlist.xml -q
.......................                                                  [100%]
23 passed in 0.17s
```

JUnit XML: [`junit-unit-tests.xml`](junit-unit-tests.xml),
[`junit-mas-accommodation-shortlist.xml`](junit-mas-accommodation-shortlist.xml).

## Endpoint tests (live containers)

Real containers (`docker compose -f docker-compose.yml -f docker-compose.ci.yml up -d --build`),
health-checked on `:6002`, `:5002`, `:3002`, then:

```
accommodation-service$ pytest -m live --junitxml=junit-endpoint-tests.xml -v
tests/test_endpoints.py::test_post_valid_accommodation_is_201_and_reads_back PASSED
tests/test_endpoints.py::test_post_missing_required_field_is_422[name] PASSED
tests/test_endpoints.py::test_post_missing_required_field_is_422[destination_id] PASSED
tests/test_endpoints.py::test_post_missing_required_field_is_422[price_per_night] PASSED
tests/test_endpoints.py::test_get_list_includes_created_record PASSED
tests/test_endpoints.py::test_get_list_filters_by_max_price PASSED
============================== 6 passed in 0.33s ===============================
```

The two required "endpoint functions" are **`create_accommodation`** (`POST /accommodations`,
read back directly from accommodation-db to prove persistence) and **`list_accommodations`**
(`GET /accommodations`, including a price-filter check) — both exercised against the live backend
on `:5002`, not mocked. JUnit XML: [`junit-endpoint-tests.xml`](junit-endpoint-tests.xml).

## AI routes disabled under docker-compose.ci.yml (job log)

```
=== MCP ===
403 {"error": "MCP mode is disabled.", "status": "error"}
=== RAG ===
403 {"error": "RAG mode is disabled.", "status": "error"}
=== AI-mode ===
403 {"error": "AI-Mode disabled", "status": "error"}
=== Multi-Agent ===
403 {"error": "Multi-Agent mode is disabled (cloud default).", "status": "error"}
```

Full transcript: [`scenario-transcripts/verify-ai-disabled.txt`](scenario-transcripts/verify-ai-disabled.txt).
