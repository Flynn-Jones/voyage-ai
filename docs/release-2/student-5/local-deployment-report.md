# Student 5 Release 2 live local validation

Date: **2026-10-22, Australia/Sydney**. Branch `student-5/itinerary-feature`, base checkpoint
`313b2538a9edaab19ea7585f7a96305b376f6ce8`; Multi-Agent implementation remains uncommitted.

**Result: PASS for the live functional/safeguard checks below.** No implementation bug was found and no
application source change was needed during this validation. Model-quality and integration limits are
recorded separately below; this is not a claim of cloud or post-commit CI validation.

## Actual startup and configuration

Followed the Itinerary README, shared server entrypoints and Student 2/4 local deployment examples:

1. Verified existing host Ollama and installed `qwen2.5:7b`; started Docker Desktop in the background.
2. Started the shared MCP, RAG and Multi-Agent Python entrypoints from their own directories using the
   project `.venv/Scripts/python.exe`. No AI server was containerised or replaced with a mock.
3. Started the existing standalone Itinerary Compose stack, preserving its named database volume.

```powershell
$env:AI_ENABLED = "true"
$env:MCP_ENABLED = "true"
$env:RAG_ENABLED = "true"
$env:MULTI_AGENT_ENABLED = "true"
docker compose -f itinerary-service/docker-compose.yml up -d --build
```

Host entrypoints: `ai-services/mcp-server/mcp_http_server.py`,
`ai-services/rag-server/rag_http_server.py`, `ai-services/multi-agent-server/server.py`.
All use the real local Ollama `qwen2.5:7b`. No model override Compose file was used.
The RAG process uses supported `RAG_DATA_DIR` pointing to ignored `.venv/live-release2/rag` to isolate its
generated index. Multi-Agent uses its standard ignored `runs.db` and `logs/audit.jsonl`. Runtime process
IDs/stdout/stderr and validation helpers/browser profiles stay ignored, outside the evidence set.

| Component | Version / configuration |
| --- | --- |
| Docker Engine | 29.6.2 |
| Itinerary backend/database | Python 3.11.17; Flask 3.1.3; backend requests 2.34.2 |
| Frontend | nginx 1.27.5; actual Nginx configuration test passed |
| Host Python / MCP / Chroma | 3.11.2 / 1.30.0 / 1.5.9 |
| Ollama / model | 0.40.7 / qwen2.5:7b |
| Multi-Agent backend URL / timeout | http://host.docker.internal:7004 / 240 seconds |
| Final flags | AI/MCP/RAG/MULTI_AGENT all true |

All seven required endpoints returned 200: frontend **3005**, backend **5005**, DB API **6005**,
MCP **7001**, RAG **7002**, Multi-Agent **7004**, Ollama **11434**. Shared Multi-Agent health reports
Ollama/MCP/RAG reachable and lists `itinerary_schedule_review`. Container-to-host Ollama connectivity
was independently checked inside `itinerary-be`.

Evidence: [live-health.json](live-health.json), [live-runtime-config.json](live-runtime-config.json).

## Real Release 0/1 regression

Both HTTP requests through the real frontend/Nginx boundary and the existing rendered-browser harness ran:

| Check | Actual result |
| --- | --- |
| Create/read/update/delete | 201/200/200/204; deleted item subsequently 404 |
| Day filtering | Correct day and updated record returned; rendered day control worked |
| AI review | Real 7b recommendation and `plan/act/observe/adapt`; advisory-only |
| MCP | TRIP-1001: six records; Day 2: three; exact trip/day scope; invalid day 400 |
| RAG refresh | 12 itinerary chunks; itinerary source ok; Chroma `vector_store_status=ready` |
| Grounded RAG | Real 7b answer: **"The estimated itinerary cost is 199.50."**; High confidence, six real citations, k=1 |
| Unsupported weather query | Insufficient, no citations, `answer_source=retrieval_guard` |

Evidence: [live-http-results.json](live-http-results.json),
[live-default-model-check.json](live-default-model-check.json),
[live-regression-browser-results.json](live-regression-browser-results.json).
The existing browser harness was reused with only evidence output paths and screenshot capture size
changed in an ignored runner; application requests/responses were not substituted.

## Real Multi-Agent browser runs

Seeded trips have no overlaps. Separate temporary `R2-LIVE-20261022-*` trips were created through the
owning backend: three overlapping Day 87 records, an unchanged Day 88 sentinel, and a foreign-trip
Day 87 sentinel. No seeded itinerary record was edited.

Each run used the actual browser -> Nginx -> owning backend -> shared Multi-Agent -> SDK MCP -> DB API
path. Every history begins `created, plan, work, review, human_review`; decided runs then record the
human decision and, when applied, the backend acknowledgement.

| Decision | Complete run ID | Final state | Changed records |
| --- | --- | --- | --- |
| Approve | `run-3bc2a8fe1842` | applied | 2 same-day time shifts |
| Partial accept | `run-0a70fe12b8bd` | applied | 1 selected shift; unselected record unchanged |
| Correct | `run-875d21878fe4` | applied | 2 shifts, including a human-corrected note |
| Reject | `run-479c24b29b70` | rejected | 0 |

**Source distinctions:** all four Planners accepted real 7b output (`source=llm`). All four Workers used
the real registered MCP SDK transport (`transport=mcp-sdk`, `get_itinerary`, success). Reviewer model
calls were real, but their replies failed the workflow's exact evidence-assessment validator on both
attempts. All four therefore used the shared framework's logged deterministic Reviewer fallback.
The final findings/risk were grounded, not silently claimed as accepted model output.

Full run inputs, Planner/Worker/Reviewer output, decisions, before/after records and ordered histories:
[live-workflow-transcript.json](live-workflow-transcript.json).
Selected actual server audit lines: [live-audit-excerpts.jsonl](live-audit-excerpts.jsonl).

## Apply and ownership verification

For every scenario, the trip records were checked unchanged after the agents ran and again after the
human decision. Only the explicit Apply action changed records. Approved/corrected fields were verified
through the real DB API; the rendered ordinary itinerary list showed the new times and corrected note.
Foreign trip and Day 88 sentinel records remained unchanged.

Read-only SQL inspection **inside the database container** captured exactly three per-run receipts,
matching the approve/partial/correct runs and their updated IDs. Rejected output had no application.
The database API access log shows application POSTs from the owning backend container's IP, after human
review. Worker audit entries contain only read-only `get_itinerary` calls. Together with the unchanged
pre-Apply snapshots and plugin boundary, this verifies application occurred through the owning backend/
database API, not through a feature-database writer inside the Multi-Agent Server.

Evidence: [live-application-receipts.json](live-application-receipts.json),
[live-database-boundary.txt](live-database-boundary.txt).

## Live safeguards and recovery

| Check | Actual result |
| --- | --- |
| Rejected run Apply | 409; unchanged records |
| Same applied run Apply again | 409 for all three applied runs; no duplicate changes |
| Second human decision | 409 on every run |
| Invalid/missing trip or day | 400 |
| Unknown run / unknown workflow | 404 / 404 |
| Foreign workflow Apply | 404 for real `example_readonly` run `run-8d2d97e7ee7d`; that test run was then rejected |
| Multi-Agent disabled | Recreated backend/frontend with supported `MULTI_AGENT_ENABLED=false`; all five routes returned 403; full CRUD round trip still passed |
| Multi-Agent unavailable | Recreated backend/frontend with enabled=true and service URL pointed to closed host port 65534; actual connection failure returned 502; CRUD still passed |
| Restoration | Finally recreated backend/frontend with all flags true and URL restored to port 7004 |

The unavailable test used an actual closed network endpoint, not a mocked HTTP response, and left the
shared server running for other callers. No volumes were deleted or dataset resets performed.
Evidence: [live-safeguard-results.json](live-safeguard-results.json).

## Browser evidence and data restoration

Real headless Edge exercised all four Multi-Agent decisions and applications. The existing browser
regression harness also exercised CRUD/filter/AI/MCP/RAG. Both recorded **zero JavaScript exceptions**.

- [live-browser-results.json](live-browser-results.json)
- [live-browser-review.png](live-browser-review.png)
- [live-browser-corrected-applied.png](live-browser-corrected-applied.png)
- [live-browser-rag-grounded.png](live-browser-rag-grounded.png)
- [live-browser-rag-insufficient.png](live-browser-rag-insufficient.png)

Every temporary itinerary record was deleted through the owning backend. The final 12 records exactly
equal the pre-validation baseline across all stored fields; only TRIP-1001/1002/1003 remain. RAG was
refreshed after cleanup and again contains 12 itinerary chunks. Shared history/audit and durable receipts
remain as runtime evidence; browser profiles, caches, models, raw logs and databases are not added to Git.
Evidence: [live-data-restoration.json](live-data-restoration.json).

## Final automated checks

- Full Student 5 suite: **291 passed** after live validation.
- Full shared Multi-Agent suite: **152 passed** after live validation.
- Configured Ruff/S, detect-secrets and pip-audit: **PASS**, 70 scoped source/test files.
- Compose configuration and actual frontend `nginx -t`: **PASS**.
- `git diff --check`: **PASS**.

Exact test output: [live-automated-validation.txt](live-automated-validation.txt).
Final security output: [pre-commit-20261022T100837Z.txt](pre-commit-20261022T100837Z.txt).
These automated tests complement the actual-service runs above; mocked unit tests are not presented as
live model/service evidence. No commit or push was performed.

## Remaining limits before commit/demo

- **Reviewer model acceptance was not demonstrated:** all four used the designed deterministic fallback.
  Model/prompt tuning is a quality follow-up; safe workflow behavior and fallback provenance were verified.
- The first cold 7b probe timed out at 65 seconds. Once loaded, actual AI review succeeded in 21.39 seconds
  and all subsequent application runs completed. Warm the model before a timed demonstration.
- Only Student 5 containers were started. Shared RAG reported other feature DBs unavailable and the existing
  missing `docs/ARCHITECTURE.md`/`budget-service/SUMMARY.md`; its Itinerary source and vector index were healthy.
- Legacy day-only CRUD filtering/AI review still spans trips, as documented before Release 2. New
  Multi-Agent requests and application remain explicitly trip/day scoped.
- This remains a trusted local app with no new user authentication. Cloud deployment and post-commit
  GitHub Actions were not exercised. Services are left running with all Student 5 AI modes enabled.
