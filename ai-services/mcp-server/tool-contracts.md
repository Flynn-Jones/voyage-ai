# MCP Tool Contracts

Shared local MCP server (not containerised). Containerised backends call it at
`http://host.docker.internal:7001/<tool_name>`; MCP clients can instead launch
`server.py` over stdio via `mcp-config.json`.

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
# Student 5 extension: get_itinerary

- HTTP: `POST /get_itinerary`; stdio MCP name: **get_itinerary** (explicitly named).
- Input: required `trip_reference` (non-empty string, max 120 characters), optional positive integer `day`.
- Output inside the existing HTTP `{status, result}` envelope: `trip_reference`, `day`, `count`, `items`, `source`, `read_only`.
- Reads `GET /itinerary-items` from `ITINERARY_DB_URL` (default `http://localhost:6005`), then filters exact trip/day. No SQLite access or write tools.
- Unknown trip/day returns an empty successful result. Invalid input or unavailable/invalid upstream returns a tool error (HTTP wrapper uses 502).
- Existing legacy stdio names ending in `_tool` are preserved for compatibility; the new tool uses the same name in both transports.
