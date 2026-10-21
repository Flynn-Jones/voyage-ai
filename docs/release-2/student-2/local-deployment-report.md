# Local deployment report: Accommodation (student-2)

Run live on this host on 2026-10-21: all three Accommodation tiers in Docker, plus the shared
non-containerised MCP server, Multi-Agent Server and Ollama, started separately per the Release 2
architecture (`ai-services/CLAUDE.md`: these three run on the host, never in Docker).

## Start commands (one terminal each)

| Tier | Command | Port |
|---|---|---|
| Ollama | already running on this host (`qwen2.5:7b` pulled) | 11434 |
| MCP server | `cd ai-services/mcp-server && ACCOMMODATION_DB_URL=http://localhost:6002 PORT=7001 python mcp_http_server.py` | 7001 |
| Multi-Agent Server | `cd ai-services/multi-agent-server && MCP_SERVICE_URL=http://localhost:7001 python server.py` | 7004 |
| Accommodation (all 3 tiers) | `cd accommodation-service && docker compose up -d --build` | 3002 / 5002 / 6002 |

## Health output

```
$ curl http://localhost:6002/health
{"database": "healthy", "status": "healthy"}

$ curl http://localhost:5002/health
{"database": "healthy", "status": "healthy"}

$ curl -o /dev/null -w "%{http_code}\n" http://localhost:3002/
200

$ curl http://localhost:7001/health
{"status":"ok","service":"mcp-server","transport":"streamable-http","mcp_path":"/mcp",
 "tools":["get_itinerary","list_expenses","get_accommodation_by_destination",
 "search_accommodations","create_accommodation","project_files","ci_report",
 "list_destinations","list_activities", ...]}

$ curl http://localhost:7004/health
{"status":"success","result":{"status":"ok","service":"multi-agent-server","port":7004,
 "ollama_reachable":true,"mcp_reachable":true,"rag_reachable":false,
 "workflows":["accommodation_shortlist","budget_reconcile","example_readonly"]}}
```

`accommodation_shortlist` is auto-discovered by the Multi-Agent Server's registry with no
registration step beyond dropping the file into `workflows/`.

## Test results

| Suite | Result |
|---|---|
| `accommodation-service/backend` unit tests (mocked MCP/RAG/MAS/LLM) | 64 passed |
| `ai-services/multi-agent-server` `test_accommodation_shortlist.py` | 23 passed |
| `accommodation-service` live endpoint tests (`pytest -m live`) | 6 passed |
| `accommodation-service/frontend` unit tests | 2 passed |

## Scenario results (3 run live against the real stack)

| ID | Scenario | Expected | Actual | Status |
|---|---|---|---|---|
| SC-1 | Shortlist, destination=Tokyo, cap=$150, interests=[pool] | A ranked shortlist, Reviewer flags the top pick's interest mismatch | 3 items shortlisted, `weak_match` finding, risk medium | **PASS** |
| SC-2 | Human partially approves SC-1's run | Only ids [2,3] kept, run reaches `applied` | `approved_items` = hotel + guesthouse, `state: applied` | **PASS** |
| SC-3 | Shortlist, destination=Tokyo, cap=$20 (nothing fits) | Empty shortlist, `no_candidates`, risk low | items=[], risk low, LLM recommendation grounded to the fallback text | **PASS** |
| SC-4 | Human rejects SC-3's run | `state: rejected`, nothing reported to `/applied` | `state: rejected`, `applied_recorded: null` | **PASS** |
| SC-5 | `create_accommodation` live via backend | 201, read back via accommodation-db | 201, db record matches | **PASS** |
| SC-6 | `list_accommodations` live via backend, price filter | created record appears/disappears by `max_price` | both assertions hold | **PASS** |
| SC-7 | Every AI-shaped route under `docker-compose.ci.yml` | MCP/RAG/AI-Mode/Multi-Agent → 403 | all 4 → 403 | **PASS** |

Full transcripts: [`scenario-transcripts/multi-agent-scenarios.txt`](scenario-transcripts/multi-agent-scenarios.txt),
[`scenario-transcripts/verify-ai-disabled.txt`](scenario-transcripts/verify-ai-disabled.txt). Full
Multi-Agent run detail: [`multi-agent-workflow-evidence.md`](multi-agent-workflow-evidence.md).

## Problems found during these runs (and fixed)

| Problem | Fix |
|---|---|
| `/ai/recommend` and `/ai/health` had no disable switch — would stay live by default in the cloud | Added `AI_ENABLED` (same pattern as `MCP_ENABLED`/`RAG_ENABLED`); routes return 403 when off |
| Accommodation had no path to reach the shared Multi-Agent Server at all | Added `services/multi_agent_api.py` + `routes/multi_agent_mode.py`, mirroring Budget's client shape |
| `backend/src/app.py` had 6 Ruff E402 findings (blueprint imports after a `sys.path` mutation) | Moved `flask`/`flask_cors` imports above the `sys.path` edit; moved blueprint imports inside `create_app()` |
| `flask-cors` 4.0.1 and `requests` 2.32.3 carried 12 known advisories between them | Bumped to `flask-cors==6.0.0`, `requests==2.34.2` across backend and frontend; full test suites re-verified green on the new versions |
| Ruff auto-fix initially removed `create_app` from `backend/app.py` as "unused" | It is re-exported for `backend/tests/*.py` to import — restored with a justified `# noqa: F401` |
