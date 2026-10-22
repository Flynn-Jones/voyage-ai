# VoyageAI Itinerary Manager

Student 5's Itinerary Manager provides a day-by-day trip schedule with a
three-layer, separately containerised architecture:

```text
Browser -> itinerary-fe -> itinerary-be -> itinerary-db -> SQLite
```

The frontend calls only the public backend API. The backend applies validation
and communicates with the database API over HTTP; it never opens SQLite. The
database container exclusively owns the SQLite file, which is persisted in the
`itinerary-db-data` Docker volume.

## Ports

| Layer | Container | Host port |
| --- | --- | ---: |
| Frontend | `itinerary-fe` | 3005 |
| Backend | `itinerary-be` | 5005 |
| Database API | `itinerary-db` | 6005 |

Inside Docker, the backend reaches the database API at
`http://itinerary-db:6005` through `DATABASE_SERVICE_URL`.
Provisional cross-service reads use `DESTINATION_SERVICE_URL` (default
`http://destination-db:6001`) and `ACTIVITY_SERVICE_URL` (default
`http://activity-service-database:6003`) over `microservices-net`.
It reaches the host's local Ollama service at
`http://host.docker.internal:11434` through `OLLAMA_BASE_URL`. The default
`OLLAMA_MODEL` follows the team convention, `qwen2.5:7b`.

## Current functionality

- View the complete itinerary grouped by day and ordered by start time.
- Filter the itinerary to a specific day and move between available days.
- Create, edit, and delete itinerary items without a manual page refresh.
- Display times, destination and activity identifiers, estimated cost, and notes.
- Validate required values, positive identifiers, costs, and same-day time ranges.
- Show loading, empty, success, validation, and service-error states.
- Preserve itinerary records in a named Docker volume across container restarts.
- Seed 12 deterministic records only when the itinerary table is empty.
- Review one day with an Ollama-powered, advisory Plan -> Act -> Observe -> Adapt workflow.
- Enrich itinerary reads with provisional Destination and Activity details when available.

Destination and Activity contracts remain provisional. Failed enrichment never
prevents stored itinerary data from being read, created, or updated.

## Public backend API

Base URL from the host: `http://localhost:5005`

| Method | Route | Purpose |
| --- | --- | --- |
| GET | `/health` | Backend health check |
| GET | `/api/itinerary` | List all itinerary items |
| GET | `/api/itinerary/<item_id>` | Retrieve one itinerary item |
| POST | `/api/itinerary` | Create an itinerary item |
| PUT | `/api/itinerary/<item_id>` | Replace an itinerary item |
| DELETE | `/api/itinerary/<item_id>` | Delete an itinerary item |
| GET | `/api/itinerary/day/<day>` | List items for one day |
| POST | `/api/itinerary/ai-review` | Review one day using deterministic observations and local Ollama |

The browser uses the same `/api/itinerary` paths through the frontend's Nginx
proxy on port 3005.

The AI review request requires a non-empty `prompt` and a positive integer
`day`; a simple phrase such as `Day 4` may supply the day through the prompt.
Its response contains separate `plan`, `act`, `observe`, and `adapt` objects.

## Internal database API

The database routes are an internal persistence boundary used by the itinerary
backend. From the host their base URL is `http://localhost:6005`.

| Method | Route | Purpose |
| --- | --- | --- |
| GET | `/health` | Database API health check |
| GET | `/itinerary-items` | List all stored items |
| GET | `/itinerary-items/<item_id>` | Retrieve one stored item |
| POST | `/itinerary-items` | Create a stored item |
| PUT | `/itinerary-items/<item_id>` | Replace a stored item |
| DELETE | `/itinerary-items/<item_id>` | Delete a stored item |
| GET | `/itinerary-items/day/<day>` | List stored items for one day |

## Run standalone

From the repository root, create the shared external network once:

```bash
docker network inspect microservices-net >/dev/null 2>&1 || docker network create microservices-net
```

Build and start the Itinerary service:

```bash
docker compose -f itinerary-service/docker-compose.yml up -d --build
```

Open <http://localhost:3005>. Check the backend and database health endpoints at
<http://localhost:5005/health> and <http://localhost:6005/health>.

Stop the containers while preserving database data:

```bash
docker compose -f itinerary-service/docker-compose.yml down
```

Use `down --volumes` only when intentionally resetting the development database.

## Tests

From the repository root, run both Student 5 suites together:

```bash
python -m pytest itinerary-service/itinerary-be/tests itinerary-service/itinerary-db/tests -q
```

Run either layer independently:

```bash
python -m pytest itinerary-service/itinerary-be/tests -q
python -m pytest itinerary-service/itinerary-db/tests -q
```

Backend tests mock the database HTTP service. Database tests use isolated
temporary SQLite files and do not modify the Docker development database.
# Release 1: shared MCP and trip-scoped RAG

Release 0 CRUD, enrichment and AI review remain available independently of the
optional services. The new cards use relative `/api/itinerary/...` URLs through
Nginx and the backend; the browser never calls the shared servers directly.

| Component | Port | Runs where |
| --- | --- | --- |
| Itinerary frontend/backend/database | 3005 / 5005 / 6005 | Docker |
| Shared MCP | 7001 | Host Python process |
| Shared RAG | 7002 | Host Python process |
| Ollama | 11434 | Host |
| Shared agentic loop | CLI | Host |

## Start the local services

From the repository root, use a Python virtual environment and install:

```text
python -m pip install -r ai-services/mcp-server/requirements.txt
python -m pip install -r ai-services/rag-server/requirements.txt
python -m pip install -r ai-services/agentic_loop/requirements.txt
```

Start each shared HTTP server in its own terminal (leave `PORT` unset for defaults):

```text
python ai-services/mcp-server/mcp_http_server.py
python ai-services/rag-server/rag_http_server.py
```

They reach itinerary data at `http://localhost:6005/itinerary-items`; override
`ITINERARY_DB_URL` in both host processes if needed. Do not point it at the
backend's MCP route (that would recurse). Optional `RAG_DATA_DIR` changes the
shared index/audit storage directory. FastMCP stdio entrypoints remain available
for MCP clients; backend integration uses the shared HTTP wrappers.

Start the existing Itinerary containers:

```text
docker network create microservices-net
docker compose -f itinerary-service/docker-compose.yml up -d --build
```

Skip network creation if it already exists. Alternatively use root
`docker compose up -d --build` for the integrated stack; do not run both stacks
on the same published ports. No MCP/RAG/Ollama/agentic-loop container is added.

For itinerary RAG, existing AI review and the loop's LLM assessments, run Ollama on the host
(`ollama serve`, unless already running) and `ollama pull qwen2.5:7b` if needed.
Ollama must listen on an interface reachable from Docker for container AI review.
Scoped itinerary RAG retrieves exact trip/day records, computes supporting facts,
then calls local Ollama to generate the final answer. Set `OLLAMA_MODEL` and
`OLLAMA_GENERATE_URL` in the shared RAG host process (defaults `qwen2.5:7b` and
`http://localhost:11434/api/generate`). Insufficient context bypasses generation;
model failures return an availability error, never a deterministic fallback answer.

## Configuration

| Variable | Host Python default | Standalone Compose default |
| --- | --- | --- |
| `MCP_ENABLED` | true | true |
| `MCP_SERVICE_URL` | http://localhost:7001 | http://host.docker.internal:7001 |
| `MCP_TIMEOUT_SECONDS` | 30 | 30 |
| `RAG_ENABLED` | true | true |
| `RAG_SERVICE_URL` | http://localhost:7002 | http://host.docker.internal:7002 |
| `RAG_TIMEOUT_SECONDS` | 120 | 120 |

Root Compose reads the same six settings with an `ITINERARY_` prefix to avoid
changing Activity/Budget configuration. Existing Ollama settings are preserved.
See `.env.example`; from root explicitly pass
`--env-file itinerary-service/.env` if using a standalone environment file.
Disabled routes return 403; unreachable/malformed upstream services return 502.

## Demonstrate the browser paths

Open `http://localhost:3005`:

1. In **MCP itinerary lookup**, enter `TRIP-1001`, optionally day `2`.
   The shared read-only `get_itinerary` tool returns scoped records and a count.
2. In **RAG itinerary questions**, refresh shared knowledge after starting the
   database or editing records. This refresh updates the shared corpus for all users.
3. Enter `TRIP-1001` and ask `What activities are planned?`,
   `What is planned on Day 2 of TRIP-1001?`, or
   `What is the estimated itinerary cost?`.
4. Inspect the answer, confidence and source/chunk citations.
5. Ask `What is tomorrow's weather?` to demonstrate insufficient evidence.

Backend POST endpoints:

- `/api/itinerary/mcp/itinerary`: `{ "trip_reference": "TRIP-1001", "day": 2 }`
- `/api/itinerary/rag/refresh`: `{}`
- `/api/itinerary/rag/retrieve` and `/api/itinerary/rag/answer`:
  `{ "trip_reference": "TRIP-1001", "query": "What activities are planned?", "k": 5 }`

Trip is required. Day is optional. Unknown trip/day produces empty MCP records or
insufficient RAG evidence. No Release 1 endpoint creates, updates or deletes records.

## Shared agentic-loop validation

Set `AGENTIC_ITINERARY_TRIP=TRIP-1001` in the host environment, then run:

```text
python ai-services/agentic_loop/app_main.py
```

Select existing mode **1 (MCP)** and **2 (RAG)**. The opt-in collectors call the
real shared health endpoints and Student 5 backend, validate scoped records,
invalid-input handling, corpus refresh, retrieval, citations, confidence and
unsupported-question abstention. Without this variable, the original collectors
remain unchanged. Host overrides: `ITINERARY_BACKEND_URL`, `MCP_SERVICE_URL`,
`RAG_SERVICE_URL`. The loop prints evidence and LLM assessments; capture its output
for submission. Its RAG check refreshes the shared index.

## Tests and isolated live checks

```text
python -m pytest itinerary-service/itinerary-be/tests itinerary-service/itinerary-db/tests itinerary-service/tests -q
python itinerary-service/validation/live_release1.py
python itinerary-service/validation/live_release1.py --ollama
```

Ordinary tests mock dependencies. The optional live script starts actual backend,
database and shared HTTP servers on temporary ports with temporary SQLite/index
data, checks CRUD/MCP/RAG/collectors and disabled/stopped services, then shuts them
down. `--ollama` additionally tests existing AI review and both shared-loop modes
using real host Ollama. The base live RAG checks also require local Ollama; only
ordinary unit tests mock it. It does not validate browser rendering or Docker networking.
CI sets `MCP_ENABLED=false` and `RAG_ENABLED=false` for container smoke tests;
isolated unit tests explicitly enable mocked paths where needed.

## Release 2 configuration

The existing AI review, MCP and RAG modes remain enabled by default locally.
`AI_ENABLED=false` makes `/api/itinerary/ai-review` return HTTP 403 with
`{"error": "AI Mode is disabled"}` before validating input or contacting the
database/Ollama. AI configuration is loaded when the backend app is created;
restart/recreate it after changing the environment. MCP/RAG keep their existing
per-request flag handling and 403 responses.

Multi-Agent mode is opt-in locally and disabled by default. It uses the existing
shared server and the `itinerary_schedule_review` workflow described below.

| Setting | Host backend default | Standalone Compose default |
| --- | --- | --- |
| `AI_ENABLED` | true | true |
| `MCP_ENABLED` / `RAG_ENABLED` | true / true | true / true |
| `MULTI_AGENT_ENABLED` | false | false |
| `MULTI_AGENT_SERVICE_URL` | http://localhost:7004 | http://host.docker.internal:7004 |
| `MULTI_AGENT_TIMEOUT_SECONDS` | 240 | 240 |

The timeout follows the shared feature-client convention (seconds); invalid or
non-positive values fall back to 240. Existing MCP/RAG URL and timeout settings
above are unchanged. Nginx allows up to 650 seconds for Multi-Agent requests;
keep any timeout override below that proxy limit.

Root Compose uses `ITINERARY_` prefixes for every setting in this table and the
MCP/RAG URLs/timeouts. Set all four enabled flags to `false` for the cloud baseline.
Do not deploy the shared host AI services to the cloud. For example, standalone:

```bash
AI_ENABLED=false MCP_ENABLED=false RAG_ENABLED=false MULTI_AGENT_ENABLED=false \
  docker compose -f itinerary-service/docker-compose.yml up -d --build
```

Security scans now include `itinerary-service/` in the shared pre-commit scope.
See `docs/release-2/student-5/pre-commit-security-report.md` for results and
justified validation-only findings.

## Release 2 Multi-Agent itinerary review

`itinerary_schedule_review` is a plugin in the existing shared server on port 7004.
Planner -> Worker -> Reviewer -> Human Review uses the existing run store,
history, audit, retry/fallback and one-decision-per-run lifecycle. There is no
second server and no shared database writer.

The required `trip_reference` and optional positive integer `day` are enforced at
the backend, workflow, evidence and application boundaries. The read-only Worker
calls `get_itinerary` with that exact scope, sorts records by day/start/id, and
proposes shifting overlapping items later on the same day. Durations, activity,
destination and cost are preserved. Calculations are deterministic Python;
Planner/Reviewer use the shared model wrappers with validated, grounded fallback
output. Empty evidence and shifts beyond 23:59 require manual review. Malformed,
unavailable or out-of-scope evidence fails the run instead of producing changes.

Start the shared MCP server and the shared Multi-Agent Server on the host:

```text
python -m pip install -r ai-services/multi-agent-server/requirements.txt
python ai-services/mcp-server/mcp_http_server.py
python ai-services/multi-agent-server/server.py
```

Run the two servers in separate terminals. `get_itinerary` reads the database
API at localhost:6005 by default (`ITINERARY_DB_URL`). Ollama provides Planner/
Reviewer model output; the server records fallback sources when it is unavailable.
RAG is not used for this structured schedule task. Enable the owning backend:

```powershell
# Root integrated Compose
$env:ITINERARY_MULTI_AGENT_ENABLED = "true"
docker compose up -d --build
# Or standalone: set MULTI_AGENT_ENABLED=true and use itinerary-service/docker-compose.yml.
```

Open the Multi-Agent section at localhost:3005. Enter a trip and optional day.
Inspect Planner, Worker, Reviewer and history, then approve all, partially accept
selected proposals, correct times/notes, or reject all. Corrections preserve
duration and cannot move records to another trip/day. Recording a decision does
not save changes. **Apply approved changes** is a separate action, enabled only
for approving decisions. Load/refresh by run ID to recover a run after a reload.
The seeded trips have no overlaps; create a temporary trip with overlapping
records through normal CRUD to demonstrate proposals.

All public routes are under `/api/itinerary/multi-agent` and return the shared
`status/result` or `status/error` envelope:

| Method | Route suffix | Purpose |
| --- | --- | --- |
| POST | `/run` | Start the fixed Itinerary workflow with `{trip_reference, day?}` |
| GET | `/runs/<run_id>` | Inspect this workflow's run and status |
| GET | `/runs/<run_id>/history` | Inspect its shared agent/human history |
| POST | `/runs/<run_id>/review` | Shared `approve`, `partial`, `correct`, `reject` decision contract |
| POST | `/runs/<run_id>/apply` | Apply only the stored, explicitly approved output |

All five routes return 403 before processing when disabled. Invalid input is 400;
unknown/foreign runs or workflows are 404; already decided/applied or stale/
conflicting changes are 409; unavailable/malformed services are 502 and shared
server request timeouts are 504. The workflow name is fixed, not browser-selected.

### Atomic application and retry

The backend validates the full run and every approved record against the shared
human decision, then posts to the **internal database API**
`POST /multi-agent/applications/<run_id>`. The database revalidates scope and
immutable fields, locks the transaction, compares original snapshots with current
records, rejects new/worsened overlaps (including partial/corrected selections),
and commits all updates plus a durable `itinerary_multi_agent_applications`
receipt together. No inserts/deletes are proposed. This schema addition is
idempotent on startup and preserves existing records.

The receipt prevents repeated writes across retries, concurrent requests and
restarts. An identical retry returns the existing receipt; a different payload
for the same run is rejected. The backend then reuses shared `POST /runs/<id>/applied`
with `target=itinerary-db`. The shared field is named `created_ids`; here it
contains the updated existing item IDs, also exposed as `updated_item_ids`.
If acknowledgement fails, the response explicitly says changes were saved and
acknowledgement is pending. Refresh and retry the same run; never start a new run
just to retry application. A stale-snapshot conflict requires a fresh review.

The internal database API has the same trusted-service/network boundary as
existing CRUD. This change does not introduce authentication or per-user run
ownership. Keep backend/database endpoints private in a deployed environment.
Real-world opening hours, transfers, reservations and travel feasibility remain
human checks; partial acceptance can leave existing overlaps, but cannot create
or worsen any overlap. Review is limited to 200 records per request/trip application.

### Automated validation

Run the existing Student 5 test command above; it now also covers the Multi-Agent
backend, atomic database application, a real local HTTP lifecycle with the shared
runner (model/transport mocked), and optional headless Edge UI scenarios. The UI
test needs Edge and `websocket-client`, otherwise it is explicitly skipped. It
uses a temporary browser profile and mock API, never the development database.

Run the shared server tests separately to preserve service module isolation:

```text
cd ai-services/multi-agent-server
python -m pytest -q
```

## Release 1 limits

- RAG is a snapshot: refresh after changes. High confidence describes relevant
  exact-scope evidence, not freshness or a guarantee that the model follows every instruction.
- Scoped RAG supports a conservative schedule/cost question vocabulary. It abstains
  on other requests rather than asking a model to guess. It is structured retrieval
  plus local-LLM generation grounded in those records and deterministic calculations,
  not a general-purpose semantic travel assistant. Citations/confidence remain server-owned.
- Answers use stored activity/destination IDs and notes, not invented names or currency.
- Cost answers use all scoped records, not only the first k retrieval results.
- Legacy unscoped shared RAG retains its existing relevance limitations. Chroma
  failure is reported as degraded; itinerary metadata retrieval still works.
- The old Release 0 day filter/review still spans trips. New MCP/RAG paths require
  a trip; no schema migration or broad Release 0 redesign was made.
- MCP transport naming for older shared tools is unchanged; only the new
  `get_itinerary` name is explicitly identical across stdio and HTTP.
