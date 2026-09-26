# MCP Integration Report — activity-service

How MCP Mode fits together, for teammates and markers. You shouldn't need to read the code to follow it.

## What MCP Mode is

MCP Mode is a **third tab** (Manual Mode | AI Mode | **MCP Mode**) alongside the existing AI Mode
chat. The AI Mode chat is unchanged.
- **AI Mode** answers in prose.
- **MCP Mode** exposes activity data as standard Model Context Protocol tools. For every call it
  shows which tool ran, the arguments it received, the HTTP status, and the raw result.

It mirrors the group's shared MCP setup (`ai-services/mcp-server/`) and budget-service's MCP Mode.
The lab's `enrolment-service` reference wasn't available on this machine, so the group's
implementation was used as the reference instead (agreed in the Plan phase).

## Components

```
                    ┌───────────────── host (not containerised) ─────────────────┐
 Claude Desktop /   │  mcp-server/server.py  ──┐                                 │
 VS Code (stdio) ──►│  (FastMCP, stdio)        │                                 │
                    │                          ├──► mcp-server/tools.py ──HTTP──►│ database-service :6003
                    │  mcp-server/             │    (5 read-only tools)          │ (GET /activities, ...)
                    │  mcp_http_server.py ─────┘                                 │
                    │  (HTTP :7003, POST /<tool>)                                │
                    └──────────▲─────────────────────────────────────────────────┘
                               │ host.docker.internal:7003
 Browser ──► frontend :3004 ──► backend :5003  backend/routes/mcp_mode.py
             /mcp-mode         /api/activity/mcp/<tool>   (validate → forward)
             /mcp-mode/call    /api/activity/mcp/ask      (LLM picks tool → validate → forward)
                                        │
                                        └──► Ollama (tool selection only, /ask)
```

| Piece | File | Job |
|---|---|---|
| Tools | `mcp-server/tools.py` | Five plain functions. Each calls the same database-service endpoints as `backend/services/db_client.py`, returns a dict, and never raises. |
| stdio server | `mcp-server/server.py` | Registers the tools with FastMCP (official `mcp` SDK, `>=1.2,<2`) for MCP clients. Configured in `mcp-config.json`. |
| HTTP server | `mcp-server/mcp_http_server.py` | The same tools over HTTP (`POST /<tool>`, `GET /health`), because a Docker container can't reach a stdio process. |
| Bridge | `backend/routes/mcp_mode.py` | One route per tool plus `/ask`. Gated by `MCP_ENABLED` and `X-MCP-Mode`. Validates IDs, then forwards. |
| Page | `frontend/templates/mcp_mode.html` | The dedicated MCP Mode page, the counterpart of `ai_mode.html`. Extends `base.html` and holds the back link, the three-tab strip, the page styles, and the script that enables or disables the ID field for each tool. |
| Panel | `frontend/templates/tabs/mcp.html` + `tabs/_mcp_call.html` | Included by `mcp_mode.html`. Contains the tool table, the "Run a tool" and "Ask" htmx forms, and the call log. Each call's card (`_mcp_call.html`) is added at the top of the log, newest first. |
| Prompts | `prompts/mcp/implementation/tool_selection_prompt.txt` | Used at runtime by `/ask`. |
| | `prompts/mcp/review/integration_review_prompt.txt` | A checklist for reviewing this integration against evidence. |

## Request flow: "Run a tool"

1. The user picks `get_activity`, enters `4` and clicks Run. htmx posts to the frontend's
   `/mcp-mode/call`.
2. The frontend maps the tool to its backend route and posts `{"activity_id": "4"}` to
   `/api/activity/mcp/activity` with the header `X-MCP-Mode: on`.
3. The bridge checks that MCP is enabled (otherwise 403). It parses `"4"` into `4`; anything that
   isn't a positive integer gets a 400 and is never forwarded. It drops unknown keys, then posts
   `{"activity_id": 4}` to `MCP_SERVICE_URL/get_activity`.
4. The HTTP server calls `tools.get_activity(4)`, which calls `GET :6003/activities/4`.
5. The response comes back as `{"status", "result", "tool", "arguments"}`, with HTTP 200 on
   success, 404 for a missing record, and 502 if the MCP server or database is down.
6. The frontend always returns 200 to htmx, so the card is swapped in even on errors. The card
   shows the backend's real status code, the arguments that were actually forwarded, and the result.

## Request flow: "Ask"

1. The bridge sends `tool_selection_prompt.txt` plus `User request: <message>` to Ollama. It uses
   `OLLAMA_INTENT_MODEL` at temperature 0 through AI Mode's existing `ai_client._generate`, which
   is reused and not modified.
2. `parse_tool_selection` accepts only a JSON object naming a registered tool with valid integer
   arguments.
3. If the selection is valid, the tool is called as in the "Run a tool" flow. Otherwise the
   response is `status: "no_tool_called"` with the reason. Either way, the raw model output is
   shown under "Raw model tool selection" on the card.

This reuses the lesson from AI Mode's `extract_intent`: when the model's output can't be read,
the fallback is explicit and visible, never a guess.

## Configuration

| Variable | Where | Default |
|---|---|---|
| `MCP_SERVICE_URL` | backend (`docker-compose.yml`) | `http://host.docker.internal:7003` |
| `MCP_ENABLED` | backend | `true` |
| `MCP_TIMEOUT_SECONDS` | backend | `30` |
| `ACTIVITY_DB_URL` | mcp-server | `http://localhost:6003` |
| `PORT` | `mcp_http_server.py` | `7003` |

The prompts reach the backend container through a read-only mount (`./prompts:/prompts:ro,z`),
because the backend's build context is `./backend`. The `z` option lets the container read the mount
on SELinux hosts such as Fedora; without it, the read fails with "Permission denied" (see run-report section 7).

## Running it

```bash
# 1. activity-service containers
docker compose up -d                       # from activity-service/

# 2. MCP server on the host
cd mcp-server && pip install -r requirements.txt
python mcp_http_server.py                  # HTTP bridge for the backend (port 7003)
python server.py                           # or: stdio, for MCP clients (via mcp-config.json)

# 3. open http://localhost:3004/mcp-mode
```

## Changes outside the new files

All of these were approved in the Plan phase. None of them changes existing behaviour.

- **`backend/app.py`:** registers the `mcp_mode` blueprint (2 lines).
- **`frontend/app.py`:**
  - adds the `/mcp-mode` and `/mcp-mode/call` routes and the `MCP_TOOLS` display list;
  - `backend_request` gains an optional `headers=` parameter, passed through only when given,
    so existing requests are identical and the existing frontend tests still pass.
- **`frontend/static/css/styles.css`:** adds an unchanged copy of the `.act-tabstrip` rules.
- **`frontend/templates/ai_mode.html`:** one line adding an "MCP Mode" link to its tab strip,
  added at your request after the initial build. Nothing else in the AI Mode page was changed.
- **`docker-compose.yml`:** adds `MCP_SERVICE_URL` and `MCP_ENABLED`, plus the prompts mount.
  It also sets `BACKEND_SERVICE_URL=http://backend:5003` on the frontend. This fixes an
  existing bug where the standalone stack's frontend looked up the root stack's `activity-backend` name.
- **Root `docker-compose.yml` (`activity-backend`):** after the switch to running the root stack, and at your
  request, it gained `MCP_SERVICE_URL`, `MCP_ENABLED`, `OLLAMA_INTENT_MODEL=llama3.1:8b` and the
  `./activity-service/prompts:/prompts:ro,z` mount. Under the root stack the MCP page is at
  `http://localhost:3003/mcp-mode`. Checked live: `list_activities` 200, `get_activity 999` 404,
  Ask → `list_activities` 200, and "Delete activity 1" → no tool called.

## Where this departs from the reference

| Reference (budget-service / shared server) | Here | Why |
|---|---|---|
| HTTP client in a separate `services/mcp_api.py` | Inside `routes/mcp_mode.py` | Keeps within the agreed file scope. |
| Any tool error returns 502 | Missing record returns **404**; other failures return 502 | Matches activity-service's existing `/api/view_activity` convention. |
| Only a non-empty check on arguments | Integer validation at the bridge (400) and in the tools | Invalid input is the caller's error, not a server failure. |
| stdio names `*_tool`, no descriptions | `name=` plus docstrings | Consistent names and usable descriptions for LLM clients. |
| Browser calls the backend directly with `fetch` | htmx through the frontend | Matches how activity-service's frontend already works. |
| Response is only `{status, result}` | Also echoes `tool` and `arguments` | The tab shows what was actually forwarded. |
| No runtime LLM tool selection | `/ask` added | Option C2, chosen in the Plan phase. |

## Known limitations

- **No nav bar link.** `base.html`'s nav was left unchanged at your request. MCP Mode is reached
  from the tab strip on the AI Mode page and on its own page, or at `/mcp-mode` directly.
- **Duplicated chat styles.** The `.act-chat__*` rules this tab reuses are defined inline in
  `ai_mode.html` and not in `styles.css`, so `mcp_mode.html` repeats the subset it needs.
- **`/ask` untested against a real model.** See [tool-review.md](tool-review.md).
- **Not in the report script.** `tests/report.py` doesn't yet include `tests/test_mcp_tools.py`
  as a layer. It was outside this task's file scope.
