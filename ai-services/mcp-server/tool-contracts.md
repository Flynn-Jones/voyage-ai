# MCP Tool Contracts

One shared local MCP server (not containerised), the FastMCP instance in `server.py`.
Registered tool names equal the names below.

- **MCP endpoint (use this for new consumers):** Streamable HTTP at
  `http://host.docker.internal:7001/mcp` (`http://localhost:7001/mcp` on the host).
  Start: `python mcp_http_server.py` (env: `PORT`, `MCP_HOST`, plus `*_DB_URL` below).
  Probe: `python mcp_probe.py list` / `python mcp_probe.py call <tool> '<json>'`.
- **Compatibility shim:** `POST http://host.docker.internal:7001/<tool_name>` (Budget's
  existing client) dispatches through `mcp.call_tool`, so it still hits the FastMCP registry.
  Returns `{status, result}`; 404 unknown tool, 400 bad JSON, 502 tool error.
- `GET /health` lists the registered tools.
- stdio: MCP clients can launch `server.py` via `mcp-config.json`.
- Tool failures over `/mcp` are `isError=true` results with a message.

## list_destinations
- Purpose: read destinations from the Destination Database API (`DESTINATION_DB_URL`, default `http://localhost:6001`)
- Input: `city`, `country`, `travel_style` (all optional strings; trimmed, max 100 chars, control characters rejected; exact-match, case-sensitive filters)
- Output: `{ source: "destination-database", filters, count, destinations[] }`
- Errors (isError): invalid input, `destination-db unavailable`, `destination-db returned <code>`, `destination-db returned unexpected payload`
- Policy class: read-only, cross-feature data access via the Destination Database HTTP API only (never SQLite)

## list_expenses
- Purpose: read budget expenses from budget-db
- Input: `trip_reference` (optional string)
- Output: `{ trip_reference, count, expenses[] }` or `{ error }`
- Policy class: read-only, cross-feature data access via budget-db HTTP API only

## get_accommodation_by_destination
- Purpose: read accommodation reference records for a destination
- Input: `destination` (required string)
- Output: `{ destination, count, accommodations[] }` or `{ error }`
- Policy class: read-only, cross-feature data access via accommodation-db HTTP API only

## project_files
- Purpose: list files/folders in a repository directory
- Input: `directory_path` (optional string, defaults to repo root, must resolve inside the repo)
- Output: `{ directory, items[] }` or `{ error }`
- Policy class: read-only, sandboxed to the repository directory

## ci_report
- Purpose: read a feature's local CI evidence report.json
- Input: `feature` (optional string, defaults to "budget-service")
- Output: report JSON or `{ error, path, hint }`
- Policy class: read-only
