# R1-P01 — Shared MCP service stabilised (evidence)

Scope: `ai-services/mcp-server/` only. No Student 1 application code changed.

## What changed
- `server.py`: single FastMCP server; tools registered under contract names; Streamable HTTP settings (`stateless_http`, `json_response`, DNS-rebinding allow-list incl. `host.docker.internal`).
- `mcp_http_server.py`: now serves real MCP at `:7001/mcp`; `/health`; `POST /<tool>` compatibility shim routed through `mcp.call_tool`.
- `tools.py`: new read-only `list_destinations` (Destination Database API over HTTP).
- `mcp_probe.py`: official-SDK client probe. `requirements.txt` pins `mcp==1.30.0`.

## Commands (env var names only)
- Start: `python mcp_http_server.py` (env: `PORT`, `MCP_HOST`, `DESTINATION_DB_URL`)
- Enumerate: `python mcp_probe.py list`
- Invoke: `python mcp_probe.py call list_destinations '{"country":"Japan"}'`
- Tests: `python -m pytest -q` (18 passed)

## Live results
- Registered tools: list_expenses, get_accommodation_by_destination, project_files, ci_report, list_destinations.
- SDK probe `call list_destinations {"country":"Japan"}`: `isError=false`, `structuredContent.count=3` (Tokyo, Kyoto, Osaka), matching `GET :6001/destinations?country=Japan`.
- Invalid input (101-char city): `isError=true`, rejected before any HTTP call.
- Destination DB stopped: `isError=true`, "destination-db unavailable", probe exit 1.
- Caveat: Docker was not running, so the unmodified `student-1/database/app.py` was run directly (not in Compose) with `DB_FILE` set to a scratch path. Re-run against `destination-database` in Compose before the demo.
- Budget shim: `POST /list_expenses` kept its `{status, result}` shape (502 error because budget-db was not running locally); not verified against live Budget data.
