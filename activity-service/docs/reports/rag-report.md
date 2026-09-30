# RAG Report — activity-service (Lab 08)

These are actual results from 2026-09-27, 15:00–15:25 AEST. Nothing here is projected: every number and output is copied from a real run.

The retrieval metrics, validation and improvement are in three companion files:

- [`rag-server/retrieval-metrics.md`](../../rag-server/retrieval-metrics.md): the latest evaluation, written by `rag_eval.py`.
- [`rag-validation-report.md`](rag-validation-report.md): the dual-agent review (OBSERVE / IMPLEMENTATION / REVIEW).
- [`rag-improvement-report.md`](rag-improvement-report.md): the IMPROVE step, with before/after metrics.

## What was built

The pattern is REFRESH → RETRIEVE → ANSWER → VALIDATE → REVIEW → IMPROVE, over activity-service's own data.

| Component | File | Notes |
|---|---|---|
| Pipeline | `rag-server/rag_pipeline.py` | `refresh_corpus`, `retrieve_context`, `answer_question`. |
| MCP server | `rag-server/rag_server.py` | FastMCP over stdio, "Activity Manager RAG MCP", launched by `mcp-config.json`. |
| HTTP server | `rag-server/rag_http_server.py` | Port 6013 on the host (not containerised): `GET /health`, `POST /refresh`, `/retrieve`, `/answer`. |
| Evaluation | `rag-server/rag_eval.py` | P@5 / R@5 over 5 benchmark queries, written to `retrieval-metrics.md`. |
| Contracts | `rag-server/tool-contracts.md` | Inputs, outputs, errors and policy class for each tool, plus the confidence rule. |
| Backend | `backend/services/rag_api.py`, `backend/routes/rag_mode.py` | `POST /api/activity/rag/{refresh,retrieve,answer}`. Returns 403 when disabled, 400 on bad input, 503 when the RAG server is unreachable, and 502 on a RAG tool error. |
| Frontend | `frontend/templates/rag_mode.html`, `tabs/rag.html`, `tabs/_rag_call.html` | "RAG Mode" tab. See below. |
| Prompts | `prompts/lab8/implementation/…`, `prompts/lab8/review/…` | The implementation, review and reasoning prompts. |
| Review loop | `ai-services/agentic_loop/` | New menu option **3 – Activity RAG**. See the validation report. |
| Tests | `tests/test_rag_pipeline.py` (57), plus `frontend/tests/test_rag_mode.py` (9) and `test_mcp_mode.py` (10) | The pipeline tests use a temporary SQLite, docs folder, corpus, audit file and Chroma directory. |

The "RAG Mode" tab has:

- an ON/OFF toggle, stored in `localStorage` and sent as `X-RAG-Mode`;
- a Refresh Corpus button;
- a Retrieve form and an Answer form;
- result cards showing the answer, a confidence badge, citations (chunk ID, source and tier), and the raw JSON in a `<details>` element.

**The corpus has 111 chunks:**

- **28 tier_1**, from the activities database:
  - 10 `activity_<id>` records;
  - 10 `assignment_<id>` records;
  - `activity_count` and `activity_category_counts`;
  - 6 `activity_category_<slug>` summaries.
- **82 tier_2:** ~80-word chunks of `docs/**/*.md`.
- **1 tier_3:** the repository file index.

## Deviations from the lab / the brief

- **There is no `destination` column.** The `activities` schema is `activity_id, activity_name, activity_type, activity_cost, duration`. Records use `category=` (from `activity_type`). The deterministic answers cover counts and categories rather than "activities in <destination>".
- **Ports.** The brief put the backend on 6003, but it is on **5003**; 6003 is database-service. RAG_PORT is **6013**, which is free in both compose files. It is configurable via `RAG_PORT` (server) and `RAG_SERVICE_URL` (backend).
- **Route paths.** The routes are `/api/activity/rag/*`, not `/rag/*`, to match this backend's `/api/activity/mcp/*` prefix.
- **Status codes.** The brief's 503 is used for "RAG server unreachable". A RAG-side tool error (for example, Ollama being down) is a separate 502, and the RAG server's own 400 passes through.
- **tier_1 source order.** The SQLite file is used only if it actually holds activities. The committed `database-service/activity-db.sqlite` is empty, while the real data sits in Docker's volume, so the pipeline falls through to database-service's HTTP API. Otherwise it would index "0 activities" as tier-1 truth.
- **Ranking.** Chunks are sorted by distance, with tier only as a tie-breaker. The lab sorts by tier first, which lets a weak tier-1 hit outrank a strong tier-2 hit (tested in `test_ranking_uses_tier_only_as_tie_breaker`).
- **Hash embedding.** It uses signed feature hashing over all 256 dimensions. The lab's version only ever writes dimensions 0–31, because it adds a 32-byte digest at `i % 256`.
- **The MCP banner goes to stderr.** `mcp-server/server.py` (Lab 07) prints it to stdout, which is the stdio protocol stream.
- **`rag-*.md` reports are excluded from the corpus.** Otherwise the pipeline's own metrics tables would become evidence for the answers they measure.
- **Backend service/route split.** `services/rag_api.py` and `routes/rag_mode.py` are separate files, as the brief lists them (and as budget-service does). `routes/mcp_mode.py` keeps both in one module.
- **Shared panel CSS.** The MCP page's panel CSS moved unchanged into `frontend/templates/tabs/_tool_panel_styles.html`, so the RAG page reuses it instead of copying ~150 lines. MCP mode renders the same. `static/css/styles.css` was not touched.
- **Agentic loop.** `ai-services/agentic_loop` already had a RAG mode (option 2) for the shared `ai-services/rag-server`. Activity RAG was added as **option 3** with its own collector, and option 2 is unchanged. `ModeConfig` gained a `prompts_dir` field (default `"prompts"`) so this mode can read `activity-service/prompts/lab8/`.
- **Compose.** Both compose files (`activity-service/docker-compose.yml` and the root `docker-compose.yml`) gained `RAG_SERVICE_URL` and `RAG_ENABLED` on the backend. `extra_hosts: host.docker.internal:host-gateway` was already present, and there is no rag-server container.
- **Embedding default** changed from `hash` to `ollama` in the IMPROVE step, with an automatic hash fallback. See the improvement report.

## Environment

- Python 3.14.7, in a venv at `rag-server/.venv` (gitignored): chromadb **1.5.9**, mcp, requests 2.32.3, flask, flask-cors and pytest.
- Ollama on the host (`localhost:11434`) with `qwen2.5:0.5b`, `llama3.1:8b` and `nomic-embed-text`. `nomic-embed-text` was pulled during the IMPROVE step.
- Docker: the **root** compose's `activity-database`, `activity-backend` and `activity-frontend` (frontend on host port **3003**).
  - The standalone `activity-service/docker-compose.yml` stack was started first, but its fresh volume has no activities, so it was stopped.
  - The root stack's `activity-db-data` volume holds the real 10 activities and 10 assignments.

## Runbook (exact commands)

```bash
# one-off setup
cd activity-service/rag-server
python -m venv .venv && .venv/bin/pip install -r requirements.txt pytest flask flask-cors
ollama pull nomic-embed-text          # embeddings (EMBEDDING_MODE=ollama, the default)

# Terminal 1: RAG HTTP server on the host
cd activity-service/rag-server && .venv/bin/python rag_http_server.py
curl http://localhost:6013/health

# Terminal 2: containers (from the repo root, whose volume holds the data)
docker network inspect microservices-net >/dev/null 2>&1 || docker network create microservices-net
docker compose up --build -d activity-database activity-backend activity-frontend
docker ps

# backend endpoints (port 5003)
curl -X POST localhost:5003/api/activity/rag/refresh
curl -X POST localhost:5003/api/activity/rag/retrieve -H 'Content-Type: application/json' \
     -d '{"query":"Harbour Kayaking Tour price","k":5}'
curl -X POST localhost:5003/api/activity/rag/answer -H 'Content-Type: application/json' \
     -d '{"query":"When is the Guided Sunrise Mountain Hike scheduled?"}'
curl -X POST localhost:5003/api/activity/rag/answer -H 'X-RAG-Mode: off' -H 'Content-Type: application/json' -d '{"query":"x"}'
curl -X POST localhost:5003/api/activity/rag/answer -H 'Content-Type: application/json' -d '{}'

# browser: http://localhost:3003/rag-mode (root stack) or :3004 (standalone stack)

# evaluation, tests, review
cd activity-service/rag-server && .venv/bin/python rag_eval.py
cd activity-service && rag-server/.venv/bin/python -m pytest tests/
(cd activity-service/backend && ../rag-server/.venv/bin/python -m pytest)
(cd activity-service/frontend && ../rag-server/.venv/bin/python -m pytest)
printf '3\n0\n' | AGENTIC_IMPLEMENTATION_MODEL=qwen2.5:0.5b AGENTIC_REVIEW_MODEL=llama3.1:8b \
  activity-service/rag-server/.venv/bin/python ai-services/agentic_loop/app_main.py
```

The frontend and backend test suites run as separate pytest invocations because both have a top-level `app` module, as documented in `frontend/tests/test_frontend.py`.

## Recorded outputs

### Health and refresh (after IMPROVE)

```
$ curl http://localhost:6013/health
{"status": "ok", "service": "activity-rag-server", "tools": ["refresh", "retrieve", "answer"], "embedding_mode": "ollama", "model": "qwen2.5:0.5b"}

$ curl -X POST localhost:5003/api/activity/rag/refresh
{'status': 'success', 'chunk_count': 111, 'tier_counts': {'tier_1': 28, 'tier_2': 82, 'tier_3': 1}, 'embedding_mode': 'ollama', 'vector_store_status': 'ready'}
```

### Retrieve, through the backend (after IMPROVE)

```
$ retrieve "Harbour Kayaking Tour price", k=5
vector ollama [(1, 'activity_1', 'tier_1', 0.197), (2, 'assignment_1', 'tier_1', 0.317), (3, 'activity_6', 'tier_1', 0.327),
               (4, 'activity_category_adventure', 'tier_1', 0.392), (5, 'activity_2', 'tier_1', 0.398)]
```

### Answer, through the backend (after IMPROVE)

| Question | Answer | Source | Confidence | Citations |
|---|---|---|---|---|
| What does the Harbour Kayaking Tour cost? | The Harbour Kayaking Tour costs 45.00. | llm | High | activity_1, activity_6, assignment_1, activity_category_adventure, activity_2 |
| When is the Guided Sunrise Mountain Hike scheduled? | The Guided Sunrise Mountain Hike is scheduled for 2026-10-04 05:30. ✅ matches `assignment_10` | llm | High | assignment_10, activity_10 |
| How many activities are there? | There are 10 activities in total. ✅ matches the DB | deterministic | High | activity_count |
| What is the capital of France? | Insufficient evidence | deterministic (relevance guard, no model call) | Unknown | (none) |
| How can MCP mode be disabled? | "…by changing the `MCP_ENABLED` and `X-MCP-Mode` headers in the backend container's `backend/routes/mcp_mode.py` file…" ⚠️ partly wrong | llm | Medium | 5 integration/run-report chunks |

The last row is a known weakness: docs retrieval finds the wrong chunks. It is recorded as the residual risk in the improvement report.

### Gating and validation (backend, live)

```
$ curl -X POST localhost:5003/api/activity/rag/answer -H 'X-RAG-Mode: off' ...
{"error": "RAG mode is disabled.", "status": "error"}   HTTP 403
$ curl -X POST localhost:5003/api/activity/rag/answer -d '{}' ...
{"error": "query is required", "status": "error"}       HTTP 400
```

### Frontend (through the `activity-frontend` container, :3003)

```
$ GET /rag-mode                                 → HTTP 200; page contains the tab link, "Refresh Corpus", #rag-toggle
$ POST /rag-mode/call kind=answer "Which Food activities are there?" rag_mode=on
    Answer (deterministic) · Confidence: High
    There are 2 Food activities: Street Food Night Walk (activity_id=3), Cooking Class: Local Dishes (activity_id=5).
    Citations: activity_3, activity_5 (database-service · tier_1)
$ POST /rag-mode/call kind=answer "When is the Live Jazz Club Evening scheduled?" rag_mode=on
    Answer (llm) · Confidence: High
    The Live Jazz Club Evening is scheduled for 2026-10-07 20:00.
    Citations: assignment_9, activity_9, activity_category_nightlife
$ POST /rag-mode/call kind=refresh rag_mode=off → card shows "HTTP 403 · RAG mode is disabled."
$ GET /mcp-mode (regression)                    → HTTP 200
```

### MCP stdio session (`rag_server.py`, official `mcp` client)

```
server: Activity Manager RAG MCP
tools: ['refresh_corpus', 'retrieve_context', 'answer_question']
retrieve_context: success vector ollama [('activity_3', 0.25), ('assignment_3', 0.305), ('activity_category_food', 0.346)]
empty query: {"status": "error", "error": "query is required and must be a non-empty string", "error_type": "invalid_input", "query": ""}
```

### Audit log

`rag-server/rag-audit.jsonl` had 72 lines after these runs, each a valid JSON record with `request_id`, `trace_id`, `tool_name`, `tool_input`, a summarised `tool_output` (chunk IDs and distances, never chunk text), `timestamp`, `duration_ms`, `validation_status` and `outcome`. Example:

```json
{"tool_name": "retrieve_context", "tool_input": {"query": "Street Food Night Walk", "k": 3, "caller": "mcp_client"},
 "tool_output": {"status": "success", "retrieval_mode": "vector", "chunk_ids": ["activity_3", "assignment_3", "activity_category_food"],
 "distances": [0.2495, 0.3048, 0.3464]}, ...}
```

### Tests (final run)

```
activity-service$ python -m pytest tests/test_rag_pipeline.py   → 57 passed
activity-service$ python -m pytest tests/                       → 129 passed  (57 new + 72 existing MCP tests)
backend$ python -m pytest                                       → 30 passed
database-service$ python -m pytest                              → 12 passed
frontend$ python -m pytest                                      → 30 passed  (11 existing + 9 RAG tab + 10 MCP tab tests)
```

Before any change the baseline was 72 + 30 + 12 + 11 = 125 passing. All of those still pass.

## Evidence log

| Phase | Check | Expected | Actual | Pass/Fail |
|---|---|---|---|---|
| REFRESH | Corpus built | status: success, chunk_count > 0 | `success`, 111 chunks (28 / 82 / 1), vector store `ready`, embedding `ollama` | ✅ Pass |
| RETRIEVE | Top-k results | ≤5 results with chunk_id, source_id, tier, distance, text | 5 results, all fields present, ranked by distance (e.g. `activity_1` 0.197 first for "Harbour Kayaking Tour price") | ✅ Pass |
| ANSWER | Grounded answer | answer + citations + confidence | For example, "…scheduled for 2026-10-04 05:30." citing `assignment_10`, High; off-topic question → `Insufficient evidence`, Unknown | ✅ Pass (docs-question weakness noted) |
| VALIDATE | pytest | all tests pass | 57 pipeline + 9 RAG tab + 10 MCP tab tests; all 201 across the four suites pass | ✅ Pass |
| VALIDATE | rag_eval.py | P@5 / R@5 for ≥4 queries | 5 queries; mean P@5 0.24, R@5 0.70 (ollama), up from 0.16 / 0.55 (hash) | ✅ Pass |
| VALIDATE | HTTP + backend + UI | health ok, /rag/* succeed, RAG tab works, OFF blocks calls | Health ok; `/api/activity/rag/*` return 200, 403 when off, 400 on missing query; the tab renders and its cards work through the frontend container | ⚠️ Partial: the tab was driven over HTTP, not in a real browser, so the toggle's JavaScript and `localStorage` persistence were not exercised live |
| REVIEW | Dual-agent output | OBSERVE / IMPLEMENTATION / REVIEW recorded | Recorded before and after IMPROVE (qwen2.5:0.5b → llama3.1:8b) | ✅ Pass |
| IMPROVE | Before/after | improvement report with metrics | Hash → Ollama embeddings, plus per-space thresholds; hallucinated schedule fixed; metrics table in the report | ✅ Pass |

## Not verified

- **A real browser session on the RAG tab.** The toggle script and `localStorage` persistence were not exercised live. The server side of OFF (header → 403) is verified live and by tests.
- **`RAG_ENABLED=false` on a running container.** It is covered by `test_backend_rag_routes_403_when_disabled_by_env`, but was not tried live.
- **The standalone `activity-service/docker-compose.yml` stack with data.** It came up and served empty lists, and was then replaced by the root stack for the data-bearing runs.
