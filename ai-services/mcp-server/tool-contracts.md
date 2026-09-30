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
