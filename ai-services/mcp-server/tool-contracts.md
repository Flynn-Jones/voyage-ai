# MCP Tool Contracts

Shared local MCP server (not containerised). All tools are read-only except
`create_accommodation`, which writes to accommodation-db. Containerised backends call it at
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
- Output: `{ destination, count, matched_by, accommodations[] }` or `{ error }`
- Matching: filters on `destination_city` first; falls back to a keyword (`q`) search when the
  city yields nothing, so a district or property name still resolves. `matched_by` reports which
  path produced the result (`destination_city` or `keyword`).
- Policy class: read-only, cross-feature data access via accommodation-db HTTP API only

## search_accommodations
- Purpose: search accommodation records across any combination of filters
- Input (all optional): `destination` (city name; falls back to keyword), `accommodation_type`
  (hotel/hostel/ryokan/apartment/guesthouse), `min_price`, `max_price`, `min_rating`,
  `amenities` (comma-separated, e.g. `WiFi,Pool`), `sort_by`
  (`price_asc`/`price_desc`/`rating_desc`/`name_asc`/`created_at_desc`), `limit` (1-100, default 20)
- Output: `{ destination, filters_applied, matched_by, count, accommodations[] }` or `{ error }`
- `matched_by`: `destination_city`, `keyword`, or `filters_only` when no destination was given
- Policy class: read-only, cross-feature data access via accommodation-db HTTP API only

## create_accommodation
- Purpose: create a new accommodation record (the only write tool on this server)
- Input: `name` (required), `destination_id` (required, e.g. `dest-tokyo`), `price_per_night`
  (required, >= 0); optional `destination_city`, `accommodation_type`
  (hotel/hostel/ryokan/apartment/guesthouse), `rating` (0-5), `location`, `description`,
  `amenities` (list or comma-separated string, matched by name against existing amenities)
- Output: `{ created, id, ignored_amenities? }` or `{ error, detail? }`
- `ignored_amenities` lists amenity names accommodation-db did not recognise and therefore
  dropped from the record — the database matches by name and silently ignores unknown ones.
- Policy class: **write**, scoped to accommodation-db only. Required fields and value ranges
  are validated here before the database is touched.

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
