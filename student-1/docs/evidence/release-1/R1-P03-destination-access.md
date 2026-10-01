# R1-P03 — Destination access to shared MCP + RAG (evidence)

Scope: `student-1/**` only. No shared MCP/RAG, Activity, root Compose or CI changes.

## Path
browser -> destination-frontend (:3001) -> destination-backend (:5001) -> shared MCP `:7001/mcp` (`tools/call list_destinations`) or shared RAG `:7002/answer`.

## Added
- Backend: `mcp_client.py`, `rag_client.py`; routes `POST /api/destinations/mcp-search`, `POST /api/destinations/rag-answer`; Dockerfile COPY line.
- Frontend: routes `/mcp-lookup`, `/ask`; templates `mcp.html`, `_mcp_result.html`, `ask.html`, `_rag_answer.html`; nav links.
- Compose (student-1 only): literal shared URLs for 7001/7002.
- RAG is consumed as-is (no retrieval/grounding/intent logic; P02 entity-only limitation not compensated).

## Env var names (values not recorded)
`MCP_SERVICE_URL`, `MCP_ENABLED`, `MCP_TIMEOUT_SECONDS`, `RAG_SERVICE_URL`, `RAG_ENABLED`, `RAG_TIMEOUT_SECONDS`; compose-level toggles `DESTINATION_MCP_ENABLED`, `DESTINATION_RAG_ENABLED`.

## Commands
- Offline: `cd student-1 && python3 -m pytest -q -m "not live"`
- Live: start shared services on the host (`python mcp_http_server.py`, `python rag_http_server.py`), then `docker compose -f student-1/docker-compose.yml up -d --build`.

## Automated test results
Current (after the latest malformed-MCP repair, which made numeric validation exception-safe for huge integers):
- Non-live Student 1 suite: `157 passed, 22 deselected`.
- Live stack not re-run after the latest repair; no full-suite total is claimed for the current code.

Earlier evidence (before the malformed-MCP repairs; not current counts):
- Live-only: `python -m pytest -q -m live` -> `22 passed, 122 deselected in 3.41s`.
- Non-live at that stage: `122 passed, 22 deselected`; full suite at that stage `144 passed in 2.60s`.
- Existing Student 1 regression suite was green at that stage.

## Post-review repair: destination-row validation (Codex blocker)
- Problem: `mcp_client.list_destinations` validated only the top-level envelope; a row with e.g. `average_daily_cost: "expensive"` returned `success` and the frontend template raised TypeError (HTTP 500).
- Fix (`backend/mcp_client.py`, `_valid_destination`): all eight Destination keys must be present (nullable = key present, value may be None). Types: int `destination_id`, str `city`/`country`, list-of-str `categories`; `description`/`travel_style` str or None; `average_daily_cost` finite int/float or None (float conversion wrapped in try/except; huge ints that overflow are rejected); `recommended_trip_length` int or None. Booleans rejected for numeric fields; NaN/Infinity rejected. The structuredContent `source` must equal `destination-database`. Any failure -> `MCPClientError("malformed")` -> existing 502 `malformed` envelope; no partial success, no coercion.
- Regression tests: `test_backend_mcp.py` (parameterised wrong-type rows; missing-key for each of the 8 fields; NaN/Infinity cost; wrong/missing source; integrated frontend -> backend -> mcp_client test with a row missing `average_daily_cost`, faking only the shared MCP HTTP call) and `test_frontend_mcp_rag.py::test_mcp_malformed_row_renders_safe_alert`.
- Results after repair: focused (`test_backend_mcp.py`, `test_frontend_mcp_rag.py`) `71 passed`; offline `155 passed, 22 deselected` (superseded by the huge-integer repair above: focused `73 passed`, offline `157 passed, 22 deselected`). Live stack not re-run.

## Manually verified live
- MCP UI, `country=Japan`: returned Tokyo, Kyoto and Osaka.
- MCP path worked: browser -> frontend -> backend -> shared MCP `:7001` -> `list_destinations` -> Destination DB.
- MCP unavailable: UI rendered the unavailable state correctly with the shared MCP service stopped.
- RAG supported question "Which destination is known for street food and nightlife?": grounded Osaka answer, High confidence, Destination citations.
- RAG unsupported question "What is the capital of Mars?": `insufficient_context`, no citations.
- RAG/Ollama failure: UI rendered an explicit infrastructure-failure state, not `insufficient_context`.
- Normal operation restored successfully afterward.

## Not verified
- Disabled toggles (`*_ENABLED`) were not reported as manually verified.
- Polish items (datalists, extra CSS, citation links) intentionally skipped.
