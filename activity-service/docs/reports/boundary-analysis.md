# MCP Boundary Analysis — activity-service

What activity-service's MCP integration exposes, and what it deliberately does not.
This describes the code as built on 2026-09-26. Evidence is in [run-report.md](run-report.md).

## What is exposed

Five tools, all **read-only**. The same five are registered on every transport:
stdio (`mcp-server/server.py`), HTTP (`mcp-server/mcp_http_server.py`), and the Flask
bridge (`backend/routes/mcp_mode.py`, `TOOL_ARGUMENTS`).

| Tool | Reads (database-service, port 6003) | Input | Access |
|---|---|---|---|
| `list_activities` | `GET /activities` | none | read |
| `get_activity` | `GET /activities/<id>` | `activity_id` (positive int) | read |
| `get_activity_assignments` | `GET /activities/<id>/assignments` | `activity_id` (positive int) | read |
| `list_assignments` | `GET /assignments` | none | read |
| `get_assignment` | `GET /assignments/<id>` | `assignment_id` (positive int) | read |

These are the same database-service endpoints that the backend's `services/db_client.py`
already uses for `/api/view_activities` and related routes. MCP does not add a parallel data path.

## What is deliberately not exposed

| Not exposed | Why |
|---|---|
| **Add / edit / delete activity** | Decided in the Plan phase. The group reference (`ai-services/mcp-server`) is read-only throughout, and an LLM-driven MCP client should not be able to change or delete data without supervision. Activity writes also go through `backend/services/activity_store.py`, which writes directly to SQLite inside a Docker volume that a host-side MCP server can't reach. |
| **Raw SQL or arbitrary queries** | The tools only call fixed database-service GET paths. An ID is formatted into the path only after it has been parsed as a positive integer. |
| **Filesystem, CI reports, other features' data** | The shared server's `project_files`, `ci_report` and budget/accommodation tools are left out on purpose. This server's scope is activity data only. |
| **The LLM itself** | MCP clients get data, not model access. The `/ask` route uses the LLM only to choose a tool from the fixed registry. |

Tests enforce this boundary:
- `test_http_wrapper_registers_exactly_the_read_only_tools`
- `test_stdio_server_registers_exactly_the_read_only_tools`
- `test_bridge_has_no_write_routes`: `/api/activity/mcp/delete-activity` returns 404.

A live `POST /api/activity/mcp/delete-activity` also returned **404**, and the HTTP
wrapper answers `POST /delete_activity` with `404 unknown tool`.

## Input boundary

- **ID arguments.** Only positive integers, or strings that are exactly a positive integer
  (`"4"`), are accepted. `None`, `""`, `"abc"`, `0`, `-1`, `"1.5"`, `2.5`, `True` and
  `"4; DROP TABLE"` are all rejected before any database request. That's 27 parametrised
  tests, each asserting that no database call was made.
- **Checked twice.** IDs are validated at the bridge (400, never forwarded) and again
  inside `tools.py`, for stdio clients that skip the bridge.
- **Extra arguments are dropped.** The bridge forwards only each tool's declared
  argument (`test_bridge_success_forwards_validated_integer_arguments`).

## LLM selection boundary (`/api/activity/mcp/ask`)

The model's output is treated as untrusted. A tool is called only when all of these hold:
1. The output parses as a JSON object. One surrounding markdown fence is tolerated; nothing else is.
2. `tool` is one of the five registered names.
3. Every required argument is a valid positive integer.

In every other case the response is `status: "no_tool_called"`, it includes the reason and
the raw model output, and nothing is forwarded. That covers unparseable text, `"none"`,
unknown tools such as `delete_activity` or `run_sql`, and missing or invented IDs.
Eight parametrised tests cover this.

## Kill switches

- `MCP_ENABLED=false` in the backend environment makes all six `/api/activity/mcp/*` routes return 403.
- A per-request `X-MCP-Mode: off` header does the same.

Both match budget-service's gating. Tested, and checked live (403).

## Residual risks

- **Anyone who can reach the host can read everything.** `mcp_http_server.py` binds
  `0.0.0.0:7003` without authentication, the same as the shared reference server. Everything
  exposed is read-only activity data, but anyone who can reach that host port can read all of it.
- **Assignments can point at deleted activities.** The database doesn't enforce the foreign
  key on seeded data, so `list_assignments` can return an assignment whose activity no longer
  exists. The tools report what the database holds and don't hide it.
