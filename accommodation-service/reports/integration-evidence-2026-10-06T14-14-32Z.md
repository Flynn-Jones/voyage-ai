# Local Integration Evidence

- Generated: `2026-10-06T14-14-32Z` (UTC)
- Branch: `feat/accommodation-mcp`
- Commit: `9f0ee42`
- Containers running: 19

| ID | Scenario | Expected | Actual | Status |
| --- | --- | --- | --- | --- |
| IT-F3000 | Shared frontend | HTTP 200 | HTTP 200 | **PASS** |
| IT-F3001 | Destination frontend | HTTP 200 | HTTP 200 | **PASS** |
| IT-F3002 | Accommodation frontend | HTTP 200 | HTTP 200 | **PASS** |
| IT-F3003 | Activity frontend | HTTP 200 | HTTP 200 | **PASS** |
| IT-F3004 | Budget frontend | HTTP 200 | HTTP 200 | **PASS** |
| IT-F3005 | Itinerary frontend | HTTP 200 | HTTP 200 | **PASS** |
| IT-B5000 | Shared backend /health | HTTP 200 | HTTP 200 | **PASS** |
| IT-B5011 | Destination backend /health | HTTP 200 | HTTP 200 | **PASS** |
| IT-B5002 | Accommodation backend /health | HTTP 200 | HTTP 200 | **PASS** |
| IT-B5003 | Activity backend /health | HTTP 200 | HTTP 200 | **PASS** |
| IT-B5005 | Itinerary backend /health | HTTP 200 | HTTP 200 | **PASS** |
| IT-B5004 | Budget backend (no /health, uses /) | HTTP 200 | HTTP 200 | **PASS** |
| IT-D6000 | Shared database /health | HTTP 200 | HTTP 200 | **PASS** |
| IT-D6001 | Destination database /health | HTTP 200 | HTTP 200 | **PASS** |
| IT-D6002 | Accommodation database /health | HTTP 200 | HTTP 200 | **PASS** |
| IT-D6003 | Activity database /health | HTTP 200 | HTTP 200 | **PASS** |
| IT-D6005 | Itinerary database /health | HTTP 200 | HTTP 200 | **PASS** |
| IT-D6004 | Budget database (no /health, uses /expenses) | HTTP 200 | HTTP 200 | **PASS** |
| IT-R01 | Shared users | HTTP 200 | HTTP 200 | **PASS** |
| IT-R02 | Destinations | HTTP 200 | HTTP 200 | **PASS** |
| IT-R03 | Accommodations | HTTP 200 | HTTP 200 | **PASS** |
| IT-R04 | Accommodation record count | >0 | 20 records | **PASS** |
| IT-R05 | Activities | HTTP 200 | HTTP 200 | **PASS** |
| IT-R06 | Expenses | HTTP 200 | HTTP 200 | **PASS** |
| IT-R07 | Itinerary items | HTTP 200 | HTTP 200 | **PASS** |
| IT-S01 | MCP server health | HTTP 200 | HTTP 200 | **PASS** |
| IT-S02 | MCP tools registered | >0 | 6 tools | **PASS** |
| IT-S03 | RAG server health | HTTP 200 | HTTP 200 | **PASS** |
| IT-S04 | Compose excludes AI services | 0 | 0 found | **PASS** |
| IT-S05 | Container reaches host via host.docker.internal | 2 | 2/2 reachable | **PASS** |
| IT-X01 | Budget reads accommodation | HTTP 200 | HTTP 200 | **PASS** |
| IT-X02 | Accommodation reads budget | HTTP 200 | HTTP 200 | **PASS** |
| IT-X03 | Accommodation reads destinations | HTTP 200 | HTTP 200 | **PASS** |
| IT-M01 | MCP draft from plain text | HTTP 200 | HTTP 200 | **PASS** |
| IT-M02 | Draft extracted expected fields | name, dest id, price, amenities | Evidence Lodge | dest-osaka | 45.0 | Pool,WiFi | **PASS** |
| IT-M03 | MCP write rejects missing fields | HTTP 400 | HTTP 400 | **PASS** |
| IT-G01 | Corpus refresh | HTTP 200 | HTTP 200 | **PASS** |
| IT-G02 | Corpus populated | >0 | 63 chunks | **PASS** |
| IT-G03 | Grounded answer with records | HTTP 200 | HTTP 200 | **PASS** |
| IT-G04 | Answer carries citations and confidence | confidence + >=1 citation | High | 5 citations | 4 records | **PASS** |
| IT-G05 | Both RAG legs completed | single request | 16s elapsed | **PASS** |
| IT-C01 | CI override disables both modes | 2 | 2 flags false | **PASS** |

**Totals — passed: 42 · failed: 0 · skipped: 0**

Raw request/response log: `integration-evidence-2026-10-06T14-14-32Z.log`
