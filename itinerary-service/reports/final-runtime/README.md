# Final Student 5 runtime validation

Branch: `student-5/itinerary-feature`. Validation completed without application
changes, commits, pushes, merges or branch switches. Earlier implementation work
remains uncommitted. Docker Engine 29.6.2 was started through Docker Desktop.

## Actual deployed path

Browser/HTTP client -> localhost:3005 -> Nginx -> itinerary-be -> shared host
MCP/RAG -> itinerary database API / local Ollama.

Only itinerary-fe, itinerary-be and itinerary-db were built/started. Backend and
database are healthy. Shared MCP (7001), RAG (7002) and Ollama (11434) remain host
processes. They and the three enabled containers were left running for the showcase.
The validation override selects qwen2.5:0.5b without changing application defaults.

## Results

- Frontend HTML/JS/CSS and all service health endpoints: passed.
- Live Nginx CRUD/read/update/delete/day filter: passed.
- Rendered Edge create/edit/delete/day filter and AI review: passed.
- MCP full trip: 6 records; Day 2: 3 records; source and scope verified; bad day: 400.
- Shared RAG refresh: 12 itinerary chunks; Chroma vector store ready.
- RAG via Nginx and rendered browser: real Ollama answer for TRIP-1001 cost 199.50;
  answer_source=llm, model=qwen2.5:0.5b, High evidence confidence, six real citations.
- Unsupported weather question: Insufficient, no citations, retrieval_guard path.
  Unit tests verify this guard does not call Ollama.
- Rendered confidence, citations and insufficient state: passed; no JS exceptions.
- Disabled optional routes: 403; unavailable optional services: 502; Release 0
  reads remained available. Enabled URLs/flags restored afterward.
- Shared agentic MCP and RAG modes: observation, implementation and review completed.
  The original `agentic-loop.txt` used a validation-only question substitution.
  The follow-up `agentic-rag-normal.txt` runs the normal CLI directly after the
  itinerary collector question was changed to the cost question. No harness,
  request substitution or response mocking was used in the follow-up. It returned
  199.50, answer_source=llm, High confidence and six citations; the unsupported
  weather query still returned Insufficient with no citations.
- FastMCP SDK tool registration and get_itinerary invocation: passed. This was an
  SDK invocation, not an external stdio-client transport test.
- Final database: 12 records, no temporary HTTP/browser validation records remain.

## Final automated checks

Backend: 105 passed. Database: 16 passed. Shared itinerary contracts: 44 passed.
Agentic collectors: 5 passed. Total: 170 passed. Combined CI-style run with
MCP_ENABLED=false and RAG_ENABLED=false: 170 passed. `git diff --check`: clean.
Root and standalone Compose configurations validate.

## Evidence and harnesses

- `http-results.json`: full live API results.
- `browser-results.json`: actual rendered text and browser CRUD results.
- `browser-grounded-answer.png`, `browser-insufficient.png`: rendered screenshots.
- `agentic-rag-normal.txt`: current normal RAG loop evidence. Model commentary
  is advisory; the OBSERVE payload records the actual HTTP results.
- `agentic-loop.txt`: historical harness-assisted output; superseded for RAG.
- `disabled-results.json`, `unavailable-results.json`: failure-isolation checks.
- `installed-packages.txt`: complete installed dependency/version inventory.
- `validate_http.py`: repeatable live validation harness; worth keeping as an
  optional manual validation tool, not a CI unit test or application component.
- `validate_browser.py`: optional CDP harness; requires an isolated Edge instance
  exposing port 9222 and the venv's websocket-client dependency.
- `run_agentic.py`: obsolete question-substitution harness; exclude from commit.
- `check_optional.ps1`: temporary deployment-check harness that restores enabled
  defaults; changes backend/frontend containers, not database contents.
- `model.override.yml`: temporary showcase configuration, not a replacement for
  the application's Compose defaults.
- `host-processes.json` and server logs are machine-specific runtime artifacts;
  they need not be committed. `.venv` and generated Chroma/corpus data are ignored.

Installed into an isolated `.venv`: mcp 1.30.0, chromadb 1.5.9, requests 2.34.2
and their required transitive dependencies (full inventory above). Existing global
Python application/test packages were not replaced. Edge was already installed.

## Limits

qwen2.5:0.5b can abstain on schedule questions or generate imperfect AI-review
prose. Use the verified cost question for the RAG showcase. Legacy day-only AI
review still spans trips. Other students' live services were not started; their
enrichment adapters retain graceful fallback. No remote GitHub workflow or cloud
deployment was performed. Default qwen2.5:7b runtime performance was not retested.

## Showcase

Open http://localhost:3005. Create/edit/delete a temporary item and filter a day.
Review Day 1. In MCP, enter TRIP-1001 and day 2: expect 3 records. In RAG, refresh,
enter TRIP-1001 (leave day blank), and ask `What is the estimated itinerary cost?`:
expect 199.50, High confidence and six citations. Ask `What is tomorrow's weather?`:
expect Insufficient. Run the normal CLI from the repository root:

```powershell
$env:AGENTIC_ITINERARY_TRIP = 'TRIP-1001'
$env:ITINERARY_BACKEND_URL = 'http://localhost:3005'
$env:AGENTIC_IMPLEMENTATION_MODEL = 'qwen2.5:0.5b'
$env:AGENTIC_REVIEW_MODEL = 'qwen2.5:0.5b'
.\.venv\Scripts\python.exe ai-services/agentic_loop/app_main.py
```

Select `2` for RAG, then `0` to exit. The follow-up validation piped those menu
choices directly to this entrypoint and saved stdout as `agentic-rag-normal.txt`.
All 170 tests passed again with disabled CI flags after the collector change.

## Commit selection

Keep README.md, validate_http.py, validate_browser.py, check_optional.ps1,
model.override.yml, http-results.json, browser-results.json,
browser-grounded-answer.png, browser-insufficient.png, disabled-results.json,
unavailable-results.json, installed-packages.txt and agentic-rag-normal.txt.
The model override is an explicit optional showcase configuration; the package
inventory documents the validation environment, not an application lockfile.
The PowerShell failure-check harness recreates frontend/backend containers and
restores the standard enabled configuration; use it only on the demo stack.

Exclude host-processes.json and all *.log files (machine-specific process state
and transient logs). Exclude run_agentic.py and agentic-loop.txt as superseded
harness-assisted artifacts. Leave .venv, browser profiles, caches and generated
RAG indexes/audit data uncommitted. Files have not been staged or deleted by this
review. The current normal-loop output retains local prompt-directory paths for
traceability; its HTTP evidence uses demo data and contains no credentials.
