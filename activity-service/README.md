# Activity Manager (Student 3)

Three containers implementing `frontend -> backend -> database API -> SQLite`.
`activity-database` is the only service that opens SQLite; the backend talks
to it exclusively over HTTP, and the frontend talks only to the backend. AI
Mode uses Ollama; MCP, RAG and Multi-Agent Mode use the shared, host-run
servers in `ai-services/`.

```
activity-service/
  backend/     Flask API (port 5003): routes/ + services/
  database/    Flask API over SQLite (port 6003)
  frontend/    Flask + HTMX UI (port 3003 on the host)
  tests/       every test for all three tiers, one pytest run
  prompts/     prompts for MCP Mode, Multi-Agent Mode and the agentic loop
  docs/        reports and evidence
```

## Run

As part of the whole VoyageAI stack (from the repo root):

```bash
./up-all.sh
```

Or on its own:

```bash
docker network create microservices-net 2>/dev/null || true
docker compose -f activity-service/docker-compose.yml up -d --build
```

## Ports

| Service             | Host port | URL                           |
| ------------------- | --------- | ----------------------------- |
| activity-frontend   | 3003      | http://localhost:3003         |
| activity-backend    | 5003      | http://localhost:5003/health  |
| activity-database   | 6003      | http://localhost:6003/health  |

The frontend listens on 3004 inside its container; the host side is 3003.

## Activity routes (backend, port 5003)

| Method | Route                                          | Notes |
| ------ | ---------------------------------------------- | ----- |
| GET    | `/api/view_activities`                         | optional `q`, `type`, `min_cost`, `max_cost`, `min_duration`, `max_duration` (hours), `date_from`, `date_to`, `sort`; 400 on a bad filter |
| GET    | `/api/view_activity/<id>`                      | 404 if missing |
| GET    | `/api/activities/<id>/assignments`             | scheduled times for one activity |
| POST   | `/api/add_activity`                            | 400 if a required field is missing or cost isn't a number |
| PATCH  | `/api/edit_activity/<id>`                      | partial update |
| DELETE | `/api/delete_activity/<id>`                    | also deletes its assignments |
| POST   | `/api/activity/ai-summary`, `/api/activity/ai-chat` | AI Mode (Ollama) |
| POST   | `/api/activity/mcp/*`                          | MCP Mode, via the shared MCP server |
| POST   | `/api/activity/rag/{refresh,retrieve,answer}`  | RAG Mode, via the shared RAG server (`scope="activity"`) |
| POST/GET | `/api/activity/multi-agent/*`                | Multi-Agent Mode, via the shared Multi-Agent Server |

If the database service is unreachable, the CRUD routes return **502**
`{"error": "database service unavailable"}` instead of crashing.

## Configuration

`docker-compose.yml` reads optional overrides from `activity-service/.env`;
`.env.example` lists every variable with its default.

The MCP (:7001), RAG (:7002) and Multi-Agent (:7004) servers run on the host,
not in Docker. See the root `README.md` for how to start them. Without them
the Manual and AI pages still work; only those three modes report that they
are unavailable.

## Test

```bash
cd activity-service
pip install -r tests/requirements.txt
python -m pytest
```

By default every test is offline: the database tier runs against a temp
SQLite file, and the backend, Ollama and the feature databases are faked. The
`test_shared_*_bridge.py` tests drive the real shared MCP and RAG servers
in-process, and skip themselves if `mcp` or `chromadb` isn't installed.

`tests/test_backend_endpoints.py` is marked `live` and skipped by default. It
tests two backend endpoint functions, `add_activity` and `view_activities`,
against the running containers:

```bash
docker compose -f activity-service/docker-compose.yml up -d --build
cd activity-service && python -m pytest -m live -v
```

Pre-commit security scans (Ruff, detect-secrets, pip-audit) cover this
folder: `pre-commit run --files $(git ls-files activity-service)`.

To append a dated pass/fail table to `docs/test-results.md`:

```bash
python tests/report.py --label "Before <change>"
```

## CI

`.github/workflows/student-3.yml` uses the same job graph as student-1.yml:
`security-scans` (the pre-commit hooks: Ruff, detect-secrets, pip-audit),
`offline-tests` and `compose-validate` run in parallel. Then `build-and-smoke`
starts the containers, runs the live tests above, fails unless both endpoint
functions pass, and checks that MCP, RAG and Multi-Agent return 403. Finally
`evidence-pack` uploads the reports. Evidence and reports:
`docs/release-2/student-3/`.
