# Multi-Agent workflow evidence: Destination (Student 1)

Workflow `destination_recommend` on the shared Multi-Agent Server (`ai-services/multi-agent-server/workflows/destination_recommend.py`).

- **Read-only and MCP-only.** `allowed_tools = ["list_destinations"]`, `uses_rag = False`. The server never writes `destination-db`; the Destination backend only forwards the human decision and closes the run with `POST /runs/<id>/applied` (`created_ids: []`, target `none (read-only destination_recommend)`).
- **Planner** (Ollama `qwen2.5:7b`, deterministic fallback) chooses the evidence source. **Worker** calls `list_destinations` over MCP (which reads the Destination Database API), filters by country, then scores and ranks in Python (interest matches, daily-budget fit, trip-length fit). **Reviewer** phrases findings and recommendations; findings are always the computed checks, risk can't drop below the computed risk, and any recommendation that writes or chooses a destination is rejected.
- **Human Review**: approve, partial, correct or reject, from the terminal (`cli.py review`) or from the Destination UI (`/multi-agent`). A second decision on the same run is refused with 409.

## Runs (real, local, 2026-10-22)

Host services: Ollama, the shared MCP server (`:7001`) and the Multi-Agent Server (`:7004`), all non-containerised. Destination frontend, backend and database ran in Docker Compose. The MAS wrote its run store and audit log to temporary paths so the canonical `runs.db` / `logs/audit.jsonl` were not touched; the audit lines are copied to [audit-excerpts.jsonl](audit-excerpts.jsonl).

| Run | Entry point | Input | Decision | Final state | Transcript |
|---|---|---|---|---|---|
| `run-fac00aec8399` | terminal (`cli.py`) | interests food, nightlife; max $130/day; 3 days | `partial` (approve 3, 5; edit on 5; reviewer `terminal-flynn`) | `applied` | [terminal-partial](scenario-transcripts/terminal-partial-run-fac00aec8399.txt) |
| `run-04c260a2a0c4` | UI → backend → MAS | culture, history; max $160/day; 4 days; Japan | `approve` | `applied` | [ui-approve](scenario-transcripts/ui-approve-run-04c260a2a0c4.txt) |
| `run-7cc542b9f5a9` | UI → backend → MAS | same | `correct` (approve 2, 3; reject 1; correction on 2) | `applied` | [ui-correct](scenario-transcripts/ui-correct-run-7cc542b9f5a9.txt) |
| `run-fb4d8769b3e9` | UI → backend → MAS | same | `reject` | `rejected` | [ui-reject](scenario-transcripts/ui-reject-run-fb4d8769b3e9.txt) |

Each history shows the full chain: `created → plan (planner) → work (worker) → review (reviewer) → human_review (awaiting) → human_review (decision) → applied`, except the rejected run, which stops after the decision. In `run-fac00aec8399` the Planner's first LLM attempt was rejected by validation ("plan uses 'None', which is not an allowed read-only source") and the retry succeeded; both attempts are in the history.

## Checks made during these runs

- **Tools used:** across the 19 audit lines for the four runs, the only tool recorded is `list_destinations` (in the `work` step). The Planner and Reviewer steps are recorded as LLM steps (`model: qwen2.5:7b`, `source: llm`). No write tool and no RAG call.
- **No data change:** SHA-256 of the sorted `GET /destinations` JSON (10 records) was `feb84d95…c3d5` before, after the terminal run, after the three UI runs, and again after create/update/delete of a throwaway record.
- **Isolation:** the Destination backend answered 404 for `GET` and `POST …/review` on a run belonging to another workflow, and that run's state stayed `awaiting_human_review`.
- **Failure handling:** with the Multi-Agent Server stopped, the UI page, the "start" action and the backend route all returned a clean "unavailable" message (HTTP 503), and Destination create/read/update/delete kept working.
- **Cloud profile:** with `DESTINATION_MULTI_AGENT_ENABLED=false` every `/api/destinations/multi-agent/*` route returns 503 `{"status": "disabled"}` and the UI hides the form (live tests in [post-commit-cicd-report.md](post-commit-cicd-report.md)).

## Not covered

- The shared RAG server was not started for this session (its refresh rewrites a tracked corpus file). Destination's existing RAG route is unchanged, its offline tests pass, and with the RAG server down it returns a clean "unavailable"; a live RAG answer was not re-checked.
- The UI was exercised through its HTTP routes and rendered HTML, not by eye in a browser.
- Canonical `runs.db` / `logs/audit.jsonl` evidence for the agentic loop's `multi-agent-review` mode still has to be produced by running the workflow against the normal Multi-Agent Server.
