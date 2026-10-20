# Changes each feature needs for the cloud (Release 2)

The AWS deployment (`scripts/deploy/aws/`) runs every container **unchanged**. It handles ports, service
names, `itinerary-be`, and activity's shared SQLite file without touching feature code. The items below are
what still breaks or is at risk in the cloud, and **only the owner should change them**. Evidence comes from
reading the code and from a local rehearsal of the cloud smoke test
([local-smoke-rehearsal-20261019.md](local-smoke-rehearsal-20261019.md)).

**Priority:** 🔴 breaks a required cloud check · 🟠 security or reliability · 🟡 tidy-up

| # | Pri | Who | Change | Why / evidence |
|---|---|---|---|---|
| 1 | 🔴 | **Budget** | `budget-fe/templates/tabs/*.html` (4 files) set `const API_BASE = "http://localhost:5004"`. Add an nginx `/api/` proxy to `budget-backend:5004` like itinerary-fe's `nginx.conf`, and use `API_BASE = "/api"`. | In the cloud the browser calls the *user's own* localhost, so the whole Budget UI fails. The backend stays private, so no new ALB port is needed. |
| 2 | 🔴 | **All 5 backends** | Read `AI_ENABLED` (default `true`) and, when it's `false`, return a disabled response **before** any input validation or Ollama call: HTTP 403 (or 503) with a body containing "disabled", like your MCP/RAG routes do. Routes: destination `POST /api/destinations/ai-compare` · accommodation `POST /ai/recommend` · activity `POST /api/activity/ai-summary`, `/api/activity/ai-chat` · budget `POST /budget/ai-analyse`, `/budget/ai-add-expense` · itinerary `POST /api/itinerary/ai-review`. Do the same with `MULTI_AGENT_ENABLED` when you add your workflow. | Release 2 requires AI-Mode off in the cloud. In the local rehearsal with MCP/RAG off, **all 5 AI-Mode routes still ran**: destination returned a real Ollama comparison, and accommodation, budget and itinerary returned 200. The cloud smoke test fails until this is done. |
| 3 | 🔴 | **All 5 frontends** | Navbar links hardcode `http://localhost:300N`. Build them from the page's host: Jinja `http://{{ request.host.split(':')[0] }}:3002`, or for static pages the script in `shared/design-system/navbar-static.html`. | Cross-feature navigation sends cloud users to their own machine. Already fixed for the shared home page and the design-system partials, which you can copy. |
| 4 | 🔴 | **Students 1–5** | Make your workflow run on **every push to `main`** (keep `paths:` for pull requests only). | Until then the deploy gate runs in `latest-main` mode, accepting a skipped feature's latest green run on main (recorded in the gate table). Once every workflow runs on every main push, the gate can switch to `strict`: all six green **for the exact commit being deployed**. |
| 5 | 🟠 | Accommodation, Activity, Budget | `app.run(..., debug=True)` in 9 entrypoints → `debug=os.environ.get("FLASK_DEBUG") == "1"`. | The Werkzeug debugger allows code execution. The accommodation and activity **frontends are public** behind the ALB. See `shared/database/src/app.py` for the pattern. |
| 6 | 🟠 | Budget | Add `GET /health` to budget-be and budget-db. | Health checks use `/` and `/expenses` meanwhile. A real health route is clearer and cheaper. |
| 7 | 🟠 | Activity | activity-backend opens the database's SQLite file directly (`ACTIVITY_DB_PATH`). Go through the activity-database HTTP API instead. | It forces both containers into one Fargate task sharing `/data`. Fixing it lets them scale and restart independently (documented in `docker-compose.yml` too). |
| 8 | 🟡 | Accommodation | Dockerfiles: backend `EXPOSE 6002` but listens on 5002; frontend `EXPOSE 3000` but listens on 3002. | Cosmetic: ECS uses explicit ports, but it misleads anyone reading the Dockerfile. |
| 9 | 🟡 | Activity | `/prompts` is a bind mount in compose, not copied into the image. | Only matters if MCP is ever enabled in the cloud (it isn't in Release 2). |
| 10 | 🟡 | All | Containers use Flask's development server (`app.run`). Consider `gunicorn` in the Dockerfile CMD. | Fine for the demo; not production-grade. |
| 11 | 🟡 | All | Add your folder to the pre-commit scope (one line in `.pre-commit-config.yaml`). | See `docs/release-2/pre-commit.md`. |

Nothing here blocks the **first** deployment. Frontends, CRUD and MCP/RAG-disabled checks already pass in the
rehearsal. Items 1–3 make the cloud review go from FAIL to PASS. Item 4 lets the gate move from `latest-main`
to `strict`.
