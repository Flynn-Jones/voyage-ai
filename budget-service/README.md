# Budget service (Student 4)

Trip expense tracking with AI-Mode, MCP, RAG and a Multi-Agent "Trip Budget Reconciler".

| Container | Port | Notes |
|---|---|---|
| budget-fe | 3004 | Static nginx; proxies `/api/` to budget-backend:5004 |
| budget-be | 5004 | Flask API: CRUD, AI-Mode, MCP, RAG and Multi-Agent routes |
| budget-db | 6004 | Flask + SQLite (`expenses`), seeds 10+ rows when empty |

Feature switches (env): `AI_ENABLED`, `MCP_ENABLED`, `RAG_ENABLED`, `MULTI_AGENT_ENABLED`.
All four are `true` locally and `false` in CI and the cloud.

## Run

```bash
docker compose up -d --build        # from budget-service/, or use the root docker-compose.yml
```

The Multi-Agent tab needs the shared Multi-Agent Server (:7004), MCP (:7001), RAG (:7002)
and Ollama running on the host. See `ai-services/multi-agent-server/README.md`.

## Test

```bash
python -m pytest -m "not live"      # unit tests, no containers needed
python -m pytest -m live            # POST/PUT /expenses against running containers
```

CI: `.github/workflows/student-4.yml`. Release 2 evidence: `docs/release-2/student-4/`.

## Pre-commit

`budget-service/` is in the shared pre-commit scope (ruff S rules, detect-secrets, pip-audit).
Run `scripts/test/precommit-report.sh student-4` for a timestamped scan report.
