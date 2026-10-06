# R1-P06 — Final main reconciliation and validation

## 1. Starting known-good point
- `student-1-release-1` @ `603b37f` (R1-P01..P05 complete; MCP, RAG, Destination access, Compose/CI and agentic loop validated).
- Restore point: local tag `r1-p05-verified` -> `603b37f`.
- `origin/main` had moved from merge-base `c078287` to `7617616` (PR #12, Accommodation MCP/RAG).

## 2. Main reconciliation
- Latest `origin/main` (`7617616`) merged on throwaway branch `student-1-r1-main-sync`; merge commit `0d71166`. `student-1-release-1` was not moved.
- Four shared conflicts, all in files hardened in P01-P05:
  - `ai-services/mcp-server/server.py`
  - `ai-services/mcp-server/mcp_http_server.py`
  - `ai-services/mcp-server/tool-contracts.md`
  - `ai-services/rag-server/rag_pipeline.py`
- Resolution principle: preserve the canonical P01-P05 FastMCP + RAG architecture; take main's changes only where they add Accommodation capability.
  - `server.py`: kept the FastMCP configuration; registered `search_accommodations` and `create_accommodation` on the canonical shared MCP. Wrapper parameters are nullable (`X | None`) because the Accommodation backend sends explicit `null` filter values.
  - `mcp_http_server.py`: kept the registry-dispatching shim unchanged.
  - `rag_pipeline.py`: kept the relevance-first ranking and tested prompt; main's unused tier-reserve constant is not carried over.
  - `tool-contracts.md`: kept the `list_destinations` contract; added the Accommodation sections (`create_accommodation` is the single documented write tool).
- `tools.py` took main's Accommodation tool implementations by automatic merge (no conflict).
- Tests: the two tools were added to `REQUIRED_TOOLS` and two shim tests were added (explicit-null search; create validates before any HTTP call). The first draft of the search test failed because its fixture returned a bare list (Destination's response shape) instead of accommodation-db's `{"data": [...]}`; this was a test-fixture error, not a merge defect, and was corrected.
- Shared MCP now registers 7 tools: `list_expenses`, `get_accommodation_by_destination`, `search_accommodations`, `create_accommodation`, `project_files`, `ci_report`, `list_destinations`.

## 3. Frozen components confirmed unchanged vs `603b37f`
`git diff --stat 603b37f 0d71166` is empty for:
- `ai-services/rag-server/`
- `ai-services/agentic_loop/`
- `student-1/`
- `ai-services/mcp-server/mcp_http_server.py`
- `.github/workflows/student-1.yml`

## 4. Final validation results
| Check | Result |
|---|---|
| MCP suite | 20 passed (18 existing + 2 added) |
| RAG suite | 41 passed |
| Agentic-loop suite | 71 passed |
| Student 1 full suite (standalone stack + shared MCP/RAG up) | 221 passed, 4 skipped (the 4 disabled-mode tests, by design) |
| `agentic_loop --mode all --no-llm` | OVERALL PASS (MCP, RAG supported, RAG unsupported; exit 0) |
| MCP `/health` and `mcp_probe list` | 7 tools |
| MCP `list_destinations` country=Japan | Tokyo, Kyoto, Osaka; source `destination-database` |
| Root stack (`docker compose up -d --build`) | 18 containers up; health 200 on :3001, :5011, :6001, :5002, :6002 |
| Accommodation via shared MCP :7001 | `search_accommodations` and `get_accommodation_by_destination` return 4 Tokyo stays (`matched_by: destination_city`); Accommodation backend MCP call succeeds |
| Accommodation via shared RAG :7002 | grounded answer, 5 citations, Medium confidence |
| `qwen2.5:7b` | installed (`ollama pull`); root Destination AI compare verified (HTTP 200, ~12-14 s) |

The MCP-down negative control is not repeated here; it is recorded in `R1-P05-agentic-validation.md`.

## 5. Root Destination persisted-data investigation
- Symptom: on the root stack the supported RAG query answered "Tokyo ... Medium" (2 citations) instead of Osaka/High, because root's Osaka record was `destination_id=19` with description "Japan" and categories `food, culture, karate`.
- Cause: persisted state in the old root Docker volume, not repository code.
  - `student-1/database/init_db.py` was already correct (Osaka id 3, street-food/nightlife description) and seeds only an empty table.
  - Root and standalone both build `./student-1/database`; only the volume differs.
  - The degraded values appear in no tracked file on any branch or tag.
  - The volume's autoincrement high-water mark was 19 with 10 rows, i.e. records had been created and deleted by hand after seeding, including seeded Osaka (id 3).
- Remediation: only the root Destination database (service and its own volume) was recreated; the standalone and teammate volumes were untouched. The pre-reseed state was exported first.
- Clean seed result: 10 rows, ids 1-10, Osaka id 3 with the canonical description.
- No code change was required.

## 6. Reproducibility validation after clean reseed (root stack)
| Check | Result |
|---|---|
| Shared RAG refresh | success, 59 chunks; `destination_db`, `budget_db`, `accommodation_db` all ok |
| Supported `/ask` ("Which destination is known for street food and nightlife?") | Osaka, High confidence, citations `destination_3`, `destination_5`, `destination_1` |
| Unsupported `/ask` ("What is the capital of Mars?") | Insufficient confidence, no citations, no fabricated answer |
| MCP Japan (probe and `/mcp-lookup`) | Tokyo, Kyoto, Osaka (ids 1, 2, 3) |
| Root browser walk | list, detail (Tokyo, Osaka), search, `/mcp-lookup` and `/ask` all 200; backend :5011 returns 10 destinations |
| AI compare (Tokyo vs Osaka) | HTTP 200 |

Note: a reseed starts from an empty volume, so Destination records created or deleted by hand afterwards will change RAG answers again. Confirm the root list still shows the 10 seeded rows before recording.

## 7. Known remaining group limitation
- Activity still uses its own non-canonical MCP (:7003) and RAG (:6013) servers instead of the shared :7001 / :7002.
- Its MCP tools (`list_activities`, `get_activity`, `get_activity_assignments`, `list_assignments`, `get_assignment`) are registered only on its own server, and its RAG client/corpus targets its own server.
- Not modified during P06. It remains an explicit group-level integration limitation unless the Activity owner changes it.
