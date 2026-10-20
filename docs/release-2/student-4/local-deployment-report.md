# Local deployment report: Budget (Student 4), Release 2

Run on 2026-10-20 (Windows 11, Docker Desktop 29.7.2, Python 3.11) from branch
`student-4/release-2-budget-reconciler`.

## Start commands (one terminal each)

| # | Terminal | Command | Port |
|---|---|---|---|
| 1 | Ollama | `ollama serve` (then `ollama pull qwen2.5:7b` once) | 11434 |
| 2 | Shared MCP | `cd ai-services/mcp-server && python mcp_http_server.py` | 7001 |
| 3 | Shared RAG | `cd ai-services/rag-server && python rag_http_server.py` | 7002 |
| 4 | Multi-Agent Server | `cd ai-services/multi-agent-server && python server.py` | 7004 |
| 5 | Containers | `docker compose up -d --build` (root; or `cd budget-service && docker compose up -d --build`) | 3004 / 5004 / 6004 (+ other features' DBs 6001–6005) |

Terminals 2–4 can be replaced by `./up-all.sh`. Reset Budget's data to the seed rows with
`docker exec -e BUDGET_DB_RESEED=1 <budget-db container> python init_db.py`.

## Health output

See [scenario-transcripts/health.txt](scenario-transcripts/health.txt). Summary: Ollama lists `qwen2.5:7b`; MCP,
RAG and Multi-Agent report ok (the Multi-Agent Server sees Ollama, MCP and RAG and lists `budget_reconcile`);
budget-be `/health`, budget-db `/health` and the proxied `:3004/api/health` all return 200.

## Test results

| Suite | Where | Result |
|---|---|---|
| `budget_reconcile` workflow (mocked) | `ai-services/multi-agent-server/tests/test_budget_reconcile.py` | 26 tests; the whole server suite is **71 passed** |
| Budget unit tests (routes with the server mocked, AI switch, health, validation, seeding) | `budget-service/tests/test_multi_agent_mode.py`, `test_cloud_readiness.py` | **25 passed** |
| Live endpoint tests (POST/PUT /expenses) + 3 AI-proposed | `budget-service/tests/test_endpoints*.py` | **20 passed** (standalone and root compose) |
| `verify-ai-disabled` (CI job, run locally) | `docker-compose.ci.yml` | **6/6 AI routes → 403** |
| Pre-commit hooks (ruff S, detect-secrets, pip-audit) over Budget | #18 config with Budget in scope | **all passed** after the fixes |

## Scenario results (all 10 run live)

| # | Scenario | Expected | Result | Evidence |
|---|---|---|---|---|
| 1 | TRIP-1001, cap 1300, then partial approval | ≈$1,360 projected, ≈$60 over; overspend + flight actual > estimate + museum duplicate; only approved/edited lines saved | **Pass.** $1,359.50 projected, **$59.50 over**, risk high, all 3 flags, "Reduce Activities by ~$60". Approve 1,3,4,6 + edit 5 → $40, reject 2: exactly 5 Planned lines, museum line not written | terminal `run-d49503fcb1b8` ([transcript](scenario-transcripts/scenario-1-terminal.txt)); UI `run-323d8f2e5343` ([run](screenshots/ui-scenario1-run.png), [applied](screenshots/ui-scenario1-applied.png)); [evidence](multi-agent-workflow-evidence.md) |
| 2 | TRIP-1002, cap 2000 | Under cap | **Pass.** $657.50 projected, $1,342.50 remaining, no flags, risk low | `run-593b257f4e22` |
| 3 | TRIP-1003, cap 1500 | Cancelled JR pass ignored; flight overrun flagged | **Pass.** "JR rail pass" in ignored_cancelled; Transport +$40 flagged; $210 over; Food missing flagged | `run-fc705cb5c8c8` |
| 4 | TRIP-9999, cap 1000 | "No evidence found"; nothing invented | **Pass.** No draft lines, risk low, the only recommendation is to record lines first | `run-37fecb7cf396` |
| 5 | cap -50 / "abc" | 400; no run created | **Pass.** Both 400 "budget_cap must be a number greater than 0"; runs 4 → 4 | [transcript](scenario-transcripts/scenarios-2-3-4-5-8-10.txt) |
| 6 | Activity tool unavailable | "No data from Activity"; run continues | **Pass.** activity-database container stopped: evidence "no data from Activity: activity database unavailable", run reached awaiting_human_review | `run-2e5e97b7f51e` ([transcript](scenario-transcripts/scenarios-4-6-8-final.txt)) |
| 7 | Reject all | Nothing written; rejection in audit log | **Pass.** TRIP-1003 rows 3 → 3; audit `human_review` with `decision: reject` and note | terminal `run-4b96b38bc03a`, UI `run-9a3e1dcd7baf` ([screenshot](screenshots/ui-scenario7-rejected.png)) |
| 8 | "Also book a cheaper hotel" | Declined; nothing written to Accommodation | **Pass.** `declined_requests: ["book a cheaper hotel"]` plus a Declined finding; Accommodation 21 → 21 records; 0 `create_accommodation` calls in the audit log | `run-a2c30eb0e15f` |
| 9 | MULTI_AGENT_ENABLED=false | 403; CRUD still works | **Pass.** All 4 Multi-Agent routes 403; POST/GET/DELETE /expenses 201/200/204; UI shows "Multi-Agent disabled (cloud default)" | [transcript](scenario-transcripts/scenarios-7-9-terminal.txt), [screenshot](screenshots/ui-scenario9-disabled.png) |
| 10 | Same review submitted twice | 409; no duplicates | **Pass.** Second submit 409; TRIP-1001 rows 8 → 8 | `run-d49503fcb1b8` |

## Problems found during these runs (and fixed)

| Found in | Problem | Fix |
|---|---|---|
| First live run | qwen2.5:7b reviews dropped findings, rated risk "medium" while over the cap, suggested cutting costs while under the cap, and copied the prompt's "$60" example | The final review always uses the computed findings and risk; only in-scope LLM recommendations whose amounts appear in the computed data are kept (commit 8257406; tests "qwen2.5:7b live …") |
| Scenario 6 | The planner never chose the Activity tool, so an Activity outage was invisible | `list_activities` is added (with a stated reason) whenever the plan reads the itinerary (4dfd5df) |
| Scenario 4 | Generic LLM advice for a trip with no data | With no evidence, only the rule-based next step is shown (4dfd5df) |
| Pre-commit | pip-audit: flask 3.0.3, flask-cors 4.0.1, requests 2.32.3 had known advisories | Upgraded to 3.1.3 / 6.0.5 / 2.34.2; all tests re-run live (b8d4f3a) |
| Step 5 | budget-db accepted `"estimated_cost": "abc"` | Field validation in budget-db (5c6630a) |

Scenario 1's terminal run (`run-d49503fcb1b8`) happened before the Activity-dependency fix, so its plan reads 2
sources. The later runs read 3.
