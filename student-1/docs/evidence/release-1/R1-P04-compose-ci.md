# R1-P04 — Release 1 Compose, environment, timeout and CI integration (evidence)

Scope: Student 1 standalone Compose, the Destination services in the root Compose, `student-1/**`, and `.github/workflows/student-1.yml`. No shared MCP/RAG, Activity or teammate changes. Nothing committed; no remote CI run.

## Architecture established
browser -> destination-frontend (:3001) -> destination-backend -> `host.docker.internal:7001` (shared MCP) / `host.docker.internal:7002` (shared RAG). MCP, RAG, Ollama and the agentic loop remain host-local; none is a Compose service. Host access uses the existing `extra_hosts: host.docker.internal:host-gateway` pattern (already present in both Compose files).
CI runs Student 1 with both integrations disabled.

## Files changed
- `student-1/docker-compose.yml` — `destination-frontend` receives the two hop timeouts. `destination-backend` unchanged (already canonical).
- `docker-compose.yml` (root) — `destination-backend` gains the canonical MCP/RAG settings; `destination-frontend` gains the two hop timeouts. 13 added lines, no deletions; service names, the 5011:5001 mapping, `OLLAMA_*`, `extra_hosts` and all teammate services (including Activity 7003/6013) untouched.
- `student-1/frontend/app.py` — `_read_timeout()` helper; MCP/RAG hop read timeouts now env-driven. Connect timeout (3), defaults (15 / 130) and Release 0 timeouts unchanged. Non-numeric, non-finite, zero or negative values fall back to the default.
- `student-1/.env.example` — Release 1 section (names and defaults only).
- `.github/workflows/student-1.yml` — workflow-level `DESTINATION_MCP_ENABLED=false` / `DESTINATION_RAG_ENABLED=false`; rendered-Compose `jq` assertion step; in-container inspection step; explanatory comments. `push.branches` and `paths` unchanged; `workflow_dispatch` intact; no existing step removed.
- `student-1/tests/test_backend_mcp.py`, `test_backend_rag.py` — `*_ENABLED` env-parsing tests (fresh module load).
- `student-1/tests/test_frontend_mcp_rag.py` — hop-timeout default/override/fallback/pass-through tests.
- `student-1/tests/test_shared_disabled_live.py` (new) — live disabled-mode tests; each runs only when its `DESTINATION_*_ENABLED` is `false`.

## Environment variable names
- Backend container (unchanged names): `MCP_SERVICE_URL`, `MCP_ENABLED`, `MCP_TIMEOUT_SECONDS`, `RAG_SERVICE_URL`, `RAG_ENABLED`, `RAG_TIMEOUT_SECONDS`.
- Compose toggles: `DESTINATION_MCP_ENABLED`, `DESTINATION_RAG_ENABLED`.
- Frontend container (new): `MCP_REQUEST_TIMEOUT_SECONDS` (default 15), `RAG_REQUEST_TIMEOUT_SECONDS` (default 130), passed through by both Compose files via `${VAR:-default}`.

## Results (all actually run; dates 2026-10-01, local macOS + Docker Desktop, Compose v5.3.1)

### Focused tests
`pytest tests/test_backend_mcp.py tests/test_backend_rag.py tests/test_frontend_mcp_rag.py` -> `138 passed`.

### Full non-live suite
`cd student-1 && python3 -m pytest -q -m "not live"` -> `199 passed, 26 deselected` (was 157 / 22; +42 offline, +4 live-marked).

### Standalone Compose
- `docker compose -f student-1/docker-compose.yml config --quiet` -> OK; services: `destination-backend`, `destination-database`, `destination-frontend`.
- Defaults render: backend 7001/7002 URLs, `MCP_ENABLED`/`RAG_ENABLED` `"true"`, timeouts 10/120; frontend 15/130; `extra_hosts` host-gateway.
- With `DESTINATION_MCP_ENABLED=false DESTINATION_RAG_ENABLED=false`: URLs still 7001/7002, both flags `"false"`, frontend 15/130.
- `MCP_REQUEST_TIMEOUT_SECONDS=20 RAG_REQUEST_TIMEOUT_SECONDS=200` render as `"20"` / `"200"`.
- The CI `jq -e` assertion exits 0 in disabled mode and non-zero with defaults (so it genuinely discriminates).

### Root Compose
- `git diff --stat -- docker-compose.yml`: 13 insertions, 0 deletions, Destination backend/frontend only.
- `docker compose -f docker-compose.yml config --quiet` -> OK.
- Destination backend renders `host.docker.internal:7001` / `:7002`; no `localhost`/`127.0.0.1` in its MCP/RAG settings; Destination frontend renders 15/130; published port still `5011->5001`.
- No MCP/RAG/Ollama/agentic service in the root service list.

### Disabled-mode standalone container run (mirrors CI)
Build + `up -d` with both flags false. Health 200 on :6001, :5001, :3001. In-container: backend `MCP_ENABLED=False`, `RAG_ENABLED=False`, URLs end `:7001` / `:7002`; frontend `(3, 15.0)` / `(3, 130.0)`.
`pytest -m live` (same env) -> `26 passed, 0 skipped, 0 failed` (22 existing + 4 new disabled-mode tests; Ollama was running locally so the AI-compare live tests ran — on CI they self-skip).

### Enabled-mode standalone container run
Host MCP (:7001) and RAG (:7002, `OLLAMA_MODEL=llama3.1:8b`) started from their existing local venvs; Ollama on :11434. Stack recreated with the toggles unset. Health 200 on all three. In-container: both enabled, canonical URLs, container -> `host.docker.internal` `/health` 200 for MCP and RAG.
- `pytest -m live` -> `22 passed, 4 skipped` (skips = the 4 disabled-mode tests, as designed).
- Full suite (`pytest`) -> `221 passed, 4 skipped`.
- Live smoke: MCP `country=Japan` -> Tokyo, Kyoto, Osaka (frontend and backend). RAG "Which destination is known for street food and nightlife?" -> `success`, High, Osaka, citations `destination_1/3/5`. RAG "What is the capital of Mars?" -> `insufficient_context`, no citations.
- MCP stopped temporarily: backend 503 `unavailable` with the safe message, frontend rendered the unavailable alert, Release 0 routes still 200; MCP restored and verified.

### Root-stack Destination smoke
Standalone stack stopped first (`down`, no `-v`). Started only `destination-frontend`, `destination-backend`, `destination-database` from the root file. Health 200 on :3001, :5011, :6001. In-container: enabled, canonical URLs, frontend 15/130; MCP and RAG both reachable. MCP (Japan) and RAG (street food) smoke through `localhost:3001` succeeded; `localhost:5011` MCP search `success`, `/api/destinations` returned 10 rows. Those three services were then stopped with `docker compose stop` (no `down -v`).
The live pytest suite hard-codes backend `:5001`, so it was not run against the root stack (known limitation).

## Final enabled-mode re-validation (fresh run after all P04 changes)
Setup: standalone `student-1/docker-compose.yml` (not the root stack); `DESTINATION_MCP_ENABLED`, `DESTINATION_RAG_ENABLED`, `MCP_REQUEST_TIMEOUT_SECONDS`, `RAG_REQUEST_TIMEOUT_SECONDS` unset in the shell (verified with `env`); stack rebuilt and recreated with `up -d --build --force-recreate`. Shared MCP (:7001) and RAG (:7002, `OLLAMA_MODEL=llama3.1:8b`) started directly on the host; Ollama host-local on :11434 (HTTP 200). No AI service is a Compose service. Health 200 on :6001, :5001, :3001.
Observed inside the running containers:
- destination-backend: `MCP_ENABLED=True`, `MCP_SERVICE_URL=http://host.docker.internal:7001`, `MCP_TIMEOUT_SECONDS=10.0`; `RAG_ENABLED=True`, `RAG_SERVICE_URL=http://host.docker.internal:7002`, `RAG_TIMEOUT_SECONDS=120.0`.
- destination-frontend: `MCP_REQUEST_TIMEOUT=(3, 15.0)`, `RAG_REQUEST_TIMEOUT=(3, 130.0)`.

Results:
- `cd student-1 && python3 -m pytest -q -m live` -> `22 passed, 4 skipped, 199 deselected`. The 4 skips are `test_shared_disabled_live.py`, which runs only when `DESTINATION_*_ENABLED` is `false`.
- `python3 -m pytest -q` (complete suite) -> `221 passed, 4 skipped`.
- No failures; no code or config changed during this re-validation.

## CI
Not run. The workflow is prepared for `workflow_dispatch`; remote execution (push + `gh workflow run student-1.yml --ref student-1-release-1`) needs approval and has not been performed. No CI result is claimed.

## Notes
- Containers exit 137 on `stop` (Flask is PID 1 and ignores SIGTERM until the grace period elapses); this is pre-existing and unrelated.
- Not verified: the GitHub-hosted runner behaviour of the new workflow steps (only the underlying commands, `jq` expression and YAML were verified locally).
