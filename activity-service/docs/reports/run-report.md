# MCP Run Report — activity-service

Actual results from 2026-09-26, around 20:44 AEST. Nothing here is projected or hypothetical.

## Environment

- Python 3.14.7, in a clean venv containing `backend/requirements.txt` (flask 3.0.3,
  flask-cors 4.0.1, requests 2.32.3), `mcp-server/requirements.txt` (`mcp` resolved to
  **1.30.0**), and pytest.
- **Docker wasn't available in this shell.** For the live checks, each service ran as a local
  process on its normal port instead:
  - database-service on :6003, against a **scratch copy** of `database-service/activity-db.sqlite`;
  - `mcp_http_server.py` on :7003;
  - backend on :5003;
  - frontend on :3004.
- The committed SQLite file has no activity rows, so the scratch copy was seeded with 3 activities
  (IDs 4–6, because the file's AUTOINCREMENT counter was already at 3) and 1 assignment. **The
  repository's SQLite file was not modified.**
- **Ollama is not installed on this machine**, so `/api/activity/mcp/ask` could not be exercised
  against a real model.

## 1. Unit tests: `tests/test_mcp_tools.py`

Command, from `activity-service/`:

```
python -m pytest tests/test_mcp_tools.py -v
============================== 72 passed in 0.34s ==============================
```

| Group | Tests | Result |
|---|---|---|
| tools.py: success (all 5 tools, plus a numeric-string ID) | 6 | ✅ 6/6 |
| tools.py: not found or empty (empty lists, three 404s, an activity with no assignments) | 6 | ✅ 6/6 |
| tools.py: invalid IDs rejected with no database call (3 tools × 9 bad values) | 27 | ✅ 27/27 |
| tools.py: database unreachable (×5 tools) or returning 500 | 6 | ✅ 6/6 |
| Transports: HTTP registry, HTTP dispatch, stdio registry and schemas | 3 | ✅ 3/3 |
| Bridge: success, list, 404, 400 ×4, 403 by header, 403 by env, 502, no write routes | 11 | ✅ 11/11 |
| `/ask`: valid selection, fenced JSON, 8 bad outputs, missing message, model down, prompt lists all tools | 13 | ✅ 13/13 |
| **Total** | **72** | **✅ 72/72** |

**Do the tests catch real bugs?** Two faults were injected, one at a time, and restored afterwards:

| Injected fault | Result |
|---|---|
| `tools.py` accepts zero and negative IDs | **6 failed**, 66 passed |
| `/ask` forwards any tool the model names, even if it isn't registered | **2 failed**, 70 passed |
| Restored | 72 passed |

**Running without the `mcp` SDK.** `test_stdio_server_registers_exactly_the_read_only_tools` skips
(`could not import 'mcp'`), and every other test only needs flask and requests. Under the system
Python, which also lacks `flask_cors`, the bridge tests error on import. The existing
`backend/tests` suite fails the same way there (7 passed, 23 errors), so it's an environment gap
and not a code fault. Install the requirements listed above first.

## 2. Regression: existing activity-service suites

Each layer ran in its own process, as `tests/report.py` requires.

| Layer | Before MCP work | After MCP work |
|---|---|---|
| database-service/tests | 12 passed | ✅ 12 passed |
| backend/tests | 30 passed | ✅ 30 passed |
| frontend/tests | 11 passed | ✅ 11 passed |
| tests/test_mcp_tools.py | (new) | ✅ 72 passed |

**Regression found and fixed during Act cycle 3.** The first version of the `headers=` change to
`frontend/app.py`'s `backend_request` always passed `headers` to `requests.request`. The existing
test fake doesn't accept that argument, so frontend/tests went from 11 passed to **3 passed,
8 failed**. The parameter is now passed only when a caller supplies one, and the suite is back to 11/11.

## 3. Live: real MCP stdio session (`server.py`)

An official `mcp` SDK client (`ClientSession` over `stdio_client`) launched `server.py`:

```
TOOL list_activities | List every travel activity with its id, name, type, cost, and duration. Read-only. | {} None
TOOL get_activity | Get one travel activity by its numeric activity_id. Read-only. | {"activity_id": {"type": "integer"}} ['activity_id']
TOOL get_activity_assignments | Get the scheduled time assignment(s) for one activity by its numeric activity_id. Read-only. | ... ['activity_id']
TOOL list_assignments | List every activity time assignment (assignment_id, activity_id, assignment_time). Read-only. | {} None
TOOL get_assignment | Get one time assignment by its numeric assignment_id. Read-only. | ... ['assignment_id']
CALL list_activities {} -> { "count": 3, "activities": [ { "activity_cost": 45.0, "activity_id": 4, "activity_name": "Harbour Kayaking", ...
CALL get_activity {'activity_id': 2} -> { "error": "activity not found", "activity_id": 2 }
CALL get_activity_assignments {'activity_id': 1} -> { "error": "activity not found", "activity_id": 1 }
CALL get_assignment {'assignment_id': 999} -> { "error": "assignment not found", "assignment_id": 999 }
```

## 4. Live: HTTP wrapper (:7003)

| Request | Status | Body (abridged) |
|---|---|---|
| `GET /health` | 200 | `tools: [list_activities, get_activity, get_activity_assignments, list_assignments, get_assignment]` |
| `POST /get_activity {"activity_id":4}` | 200 | `{"activity": {"activity_id": 4, "activity_name": "Harbour Kayaking", ...}}` |
| `POST /get_activity_assignments {"activity_id":4}` | 200 | `count: 1, assignment_time: "2026-10-02 09:00"` |
| `POST /get_activity_assignments {"activity_id":5}` | 200 | `count: 0, assignments: []` |
| `POST /get_activity {"activity_id":99999}` | 502 | `result.error: "activity not found"` (reference convention) |
| `POST /get_activity {"activity_id":"abc"}` | 502 | `result.error: "activity_id must be a positive integer"` |
| `POST /delete_activity` | 404 | `unknown tool: delete_activity` |

## 5. Live: backend bridge (:5003 → :7003 → :6003)

| Route | Input | Status |
|---|---|---|
| list-activities | `{}` | 200 |
| activity | `{"activity_id":4}` | 200 |
| activity | `{"activity_id":999}` | 404 |
| activity | `{"activity_id":0}` | 400 |
| activity-assignments | `{"activity_id":5}` | 200 (empty list) |
| assignment | `{"assignment_id":4}` | 200 |
| delete-activity | `{"activity_id":4}` | 404 (no such route) |
| list-activities | `X-MCP-Mode: off` | 403 |
| ask | `"When is activity 4 scheduled?"` | 502: `AI tool selection unavailable: ... localhost:11434 ... Connection refused` |

The first version of the bridge returned **502** for a missing record, which is the budget-service
convention. It was changed during Act cycle 2 to return **404**, matching `/api/view_activity`, and
re-verified above.

## 6. Live: frontend tab (:3004)

- `GET /mcp-mode` returns 200, rendered from `mcp_mode.html`, which includes `tabs/mcp.html`. The page renders the three-tab strip with MCP Mode active, and 5 tools in the selector.
- Re-checked after `base.html` was reverted and `mcp_mode.html` was split out, with the full stack running and htmx loaded:
  - the nav bar has no MCP link;
  - an htmx `POST /mcp-mode/call` with `get_activity 4` returns HTTP 200 with the activity;
  - frontend 11/11, backend 30/30 and MCP 72/72 tests pass;
  - a live headless Chrome screenshot shows the pending indicator collapsed between requests.
- `POST /mcp-mode/call`:

| Input | Card shown |
|---|---|
| `get_activity`, `4` | HTTP 200, the forwarded arguments `{"activity_id": 4}`, the full activity JSON |
| `get_activity`, `999` | HTTP 404, "activity not found" |
| `get_activity`, `abc` | HTTP 400, "activity_id is required and must be a positive integer" |
| unknown tool `delete_activity` | "no response" (never sent), "Unknown tool" |
| Ask | "no tool called", with the AI-unavailable error |

- A headless Chrome screenshot of the tab with three real call cards (success, 404, list) confirmed
  the red/white theme, the tab strip, and the call-log layout.

## 7. Live: containerised stack (Podman), 2026-09-26 ~20:54 AEST

Docker isn't installed on this machine, so the unmodified `activity-service/docker-compose.yml`
was run with Podman 5.8.7 and `podman-compose` 1.6.0. The external `microservices-net` network
was created first. `mcp_http_server.py` ran on the host as described in the integration report.
Two test activities (one with an assignment) were added through `POST /api/add_activity`, then
deleted through `DELETE /api/delete_activity/<id>` afterwards. The database was confirmed empty again.

**Each hop:**

| Hop | Result |
|---|---|
| Host → database container (`localhost:6003`) | ✅ reachable |
| Host MCP server (`:7003/health`) | ✅ 5 tools listed |
| Backend container → host MCP server (`host.docker.internal:7003`) | ✅ 200. No firewall change was needed under Podman. |
| Prompts mount inside the backend container | ❌ **`Permission denied`** on the first run → fixed, ✅ readable |
| Backend container bridge (`:5003/api/activity/mcp/*`) | ✅ list 200, get 200, missing record 404, `"abc"` 400, empty assignments 200, header off 403, `delete-activity` 404 |
| Frontend container → backend container | ❌ fails. This problem already existed before the MCP work; see below. |
| Frontend `/mcp-mode/call` (with a scratch override) | ✅ `get_activity 1` 200, `get_activity_assignments 1` 200 (count 1), `list_activities` 200 (count 2), `get_activity 999` 404, `get_assignment abc` 400 |
| MCP stdio client → `server.py` → database container | ✅ list, get, assignments, and not-found all correct |
| `/ask` | 502: `host.docker.internal:11434` refused, because Ollama isn't installed |

**Fixed in this run: the prompts mount on SELinux hosts.** On Fedora (SELinux enforcing), the
backend container couldn't read the `./prompts:/prompts:ro` bind mount, which would have broken
`/ask` in any real deployment. The local-process tests in sections 5–6 couldn't catch this. The
mount is now `./prompts:/prompts:ro,z`, which works under Docker and Podman and has no effect on
hosts without SELinux. After the fix, both prompt files were readable inside the container.

**Found, then fixed at your request: the standalone frontend couldn't reach the backend.**
`frontend/app.py` defaults `BACKEND_SERVICE_URL` to `http://activity-backend:5003`. That name is
correct in the root `docker-compose.yml`, but in `activity-service/docker-compose.yml` the
service is called `backend` and no override is set. Every frontend page fails as a result,
including the existing Activities page, which shows `Failed to resolve 'activity-backend'`. The
frontend row above was tested with a scratch compose override adding
`BACKEND_SERVICE_URL=http://backend:5003`, which isn't committed. **Fixed (after the 21:09 re-run):** added
`BACKEND_SERVICE_URL=http://backend:5003` to the frontend's `environment` in
`activity-service/docker-compose.yml`. Re-tested with the real file and no override:
- `/`, `/activities`, `/activities/5`, `/ai-mode` and `/mcp-mode` all return 200, with no resolve errors;
- the MCP tab returned `list_activities` 200, `get_activity 5` 200, `get_activity_assignments 5` 200 (count 1), `get_activity 999` 404, and `get_assignment abc` 400.

The test activity was deleted afterwards.

**Note for the root stack.** The root `docker-compose.yml` was out of scope and wasn't changed.
Its `activity-backend` service has no `MCP_SERVICE_URL` or prompts mount. Under the root stack,
the MCP routes would therefore try `localhost:7003` inside the container and fail with 502, and
`/ask` couldn't load its prompt.

### Re-run, 2026-09-26 ~21:10 AEST (no code changes since the first run)

- **Unit suites:** database-service 12/12, backend 30/30, frontend 11/11, MCP 72/72.
- **Stack:** rebuilt with `podman-compose up -d --build`, with the MCP server on the host.
- **Seeded IDs:** the new activities were IDs **3 and 4** and the assignment was **2**, because
  the first run deleted IDs 1 and 2 and SQLite `AUTOINCREMENT` doesn't reuse IDs. Lookups of 1
  and 2 correctly returned 404.
- **Backend container → host MCP server:** 200. Both prompt files are readable in the backend
  container (the `:ro,z` fix held).
- **Bridge:**
  - `activity 3`: 200
  - `activity-assignments 3`: 200, count 1
  - `activity-assignments 4`: 200, count 0
  - `assignment 2`: 200
  - `"abc"`: 400
  - `delete-activity`: 404
  - header off: 403
- **Frontend as committed:** still fails with `Failed to resolve 'activity-backend'`. The fix
  hasn't been applied.
- **Frontend with the scratch override:**
  - `list_activities`: 200, count 2
  - `get_activity 3`: 200
  - `get_activity_assignments 3`: 200
  - `get_assignment 2`: 200
  - deleted ID 1: 404
  - `"abc"`: 400
  - Ask: 502 (no Ollama)
- **stdio client:** 5 tools listed, and all calls correct.
- **Cleanup:** test data deleted afterwards and the database confirmed empty.

## 8. Live: real LLM tool selection, 2026-09-26 (before 21:31 AEST)

Ollama 0.34.4 was installed, with `llama3.1:8b`, `qwen2.5:0.5b` and `deepseek-r1:8b` pulled.

- **Selection accuracy:** `llama3.1:8b` got 16/16 correct with 0 wrong calls. `qwen2.5:0.5b` got
  9/16 correct with 6 wrong (read-only) calls. Full table in [tool-review.md](tool-review.md).
- **End-to-end `/ask`:** the path was backend on the host (port 5013, `OLLAMA_INTENT_MODEL=llama3.1:8b`)
  → host MCP server → database container, with 1 test activity (ID 6) added and later deleted.

  | Request | Status | Tool / result |
  |---|---|---|
  | "When is activity 6 scheduled?" | 200 | `get_activity_assignments {activity_id: 6}` → the real assignment `2026-10-02 09:00` |
  | "Show me all the activities" | 200 | `list_activities` → count 1 |
  | "Tell me about activity 999" | 200 `no_tool_called` | the model said `"none"` (see the tool-review quirks) |
  | "Delete activity 6" | 200 `no_tool_called` | refused; activity 6 still returned 200 afterwards |

- **Containers can't reach Ollama.** Ollama listens only on `127.0.0.1:11434`. From the backend
  container, `http://host.docker.internal:11434/api/tags` gave a **ConnectionError**. This is a host
  setting, not a code change, and it affects AI Mode's chat and summaries in containers as well as `/ask`.
  The fix is `OLLAMA_HOST=0.0.0.0` for the Ollama service (needs sudo; not applied here).
- **The default model is missing.** Neither compose file sets `OLLAMA_INTENT_MODEL`, so the backend
  asks for `qwen2.5:7b`, which isn't pulled. Even once containers can reach Ollama, `/ask` and AI
  Mode's intent step would fail until that model is pulled or the variable is set.

## 9. Live: `/ask` inside the containers with a real model, 2026-09-26 (last model request logged by Ollama at 21:31:33 AEST)

Ollama was still bound to `127.0.0.1`, because the `OLLAMA_HOST` change needs sudo and hadn't
been applied. To exercise the container path anyway, a **test-only** TCP forwarder
(`0.0.0.0:11435` → `127.0.0.1:11434`) ran from a scratch directory and was stopped afterwards.
The stack started with `OLLAMA_URL=http://host.docker.internal:11435`, using the compose file's
existing `${OLLAMA_URL}` passthrough, plus a scratch override setting
`OLLAMA_INTENT_MODEL=llama3.1:8b`. **Nothing in the repo changed for this.** Two test activities
(IDs 7 and 8, one with an assignment) were added and deleted afterwards.

Path: frontend container `/mcp-mode/call` → backend container `/api/activity/mcp/ask` → Ollama
(`llama3.1:8b`) → host MCP server → database container.

| Ask request | Card |
|---|---|
| "When is activity 7 scheduled?" | `get_activity_assignments` HTTP 200: `2026-10-02 09:00`, count 1 |
| "Show me all the activities" | `list_activities` HTTP 200: both activities, count 2 |
| "How much does activity 8 cost?" | `get_activity` HTTP 200: Aquarium Visit |
| "Show every scheduled time" | `list_assignments` HTTP 200: count 1 |
| "Delete activity 7" | no tool called: "no registered tool matches this request" (activity 7 still returned 200 afterwards) |
| "What's the weather in Tokyo?" | no tool called |

The "Run a tool" form (`get_activity 8`) returned HTTP 200. AI Mode's chat also answered through
the same Ollama path.

**To make this permanent without the forwarder:**
- set `OLLAMA_HOST=0.0.0.0` on the Ollama service;
- pull `qwen2.5:7b`, or set `OLLAMA_INTENT_MODEL=llama3.1:8b` for the backend.

## 10. Live: `/ask` inside the containers, direct to Ollama, 2026-09-26 ~21:37 AEST

`OLLAMA_HOST=0.0.0.0` was applied to the Ollama service
(`/etc/systemd/system/ollama.service.d/override.conf`). Checked at 21:36:14: Ollama listens on
`*:11434`, and the backend container reaches `http://host.docker.internal:11434` with 200. The
test forwarder from section 9 was stopped. The stack used the compose file's own `OLLAMA_URL`
with no override.

| Setup | Ask ("Show me all the activities", etc.) |
|---|---|
| Committed config only (`OLLAMA_INTENT_MODEL` unset, so `qwen2.5:7b`) | ❌ 502 `AI tool selection unavailable: Ollama returned 404`. The model isn't pulled. |
| Plus a scratch override `OLLAMA_INTENT_MODEL=llama3.1:8b` | ✅ "When is activity 9 scheduled?" → `get_activity_assignments` 200 (`2026-10-02 09:00`). "Show me all the activities" → `list_activities` 200. "Delete activity 9" → no tool called, and activity 9 still returned 200. |

**The one remaining step** for `/ask`, and for AI Mode's intent step, to work from the committed
config: pull `qwen2.5:7b`, or set `OLLAMA_INTENT_MODEL` in `activity-service/docker-compose.yml`.

### Re-run, 2026-09-26 21:50 AEST (after the AI Mode tab link was added)

- **Unit suites:** database-service 12/12, backend 30/30, frontend 11/11, MCP 72/72.
- **Stack:** the running containers, with Ollama direct on `*:11434` and a scratch override setting
  `OLLAMA_INTENT_MODEL=llama3.1:8b`.
- **Pages:** `/`, `/activities`, `/activities/9`, `/ai-mode` and `/mcp-mode` all return 200 with no
  resolve errors. The AI Mode page has its MCP Mode tab, and the MCP page has its tab strip.
- **Bridge:**
  - `list-activities` / `activity 9` / `activity-assignments 9` / `list-assignments`: 200
  - `activity 999` / `assignment 999`: 404
  - `"abc"`: 400
  - `delete-activity`: 404
  - header off: 403
- **Run a tool:** `get_activity 9` 200, `get_activity_assignments 9` 200 (`2026-10-02 09:00`),
  `get_activity 999` 404.
- **Ask:**
  - "When is activity 9 scheduled?" → `get_activity_assignments` 200
  - "Show me all the activities" → `list_activities` 200
  - "How much does activity 9 cost?" → `get_activity` 200
  - "Delete activity 9" → no tool called
  - "What's the weather in Tokyo?" → no tool called

  Activity 9 still returned 200 afterwards.

## Not verified

- **`/ask` with the committed default model.** `qwen2.5:7b` isn't pulled, so `/ask` inside the
  containers has only been verified with `OLLAMA_INTENT_MODEL=llama3.1:8b` (section 10).
- **`qwen2.5:7b`.** It isn't pulled, so the actual default `OLLAMA_INTENT_MODEL` wasn't evaluated.
- **Docker Engine specifically.** The compose deployment was verified under Podman (section 7),
  not Docker.
