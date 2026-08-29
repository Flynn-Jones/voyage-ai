# VoyageAI — Architecture & Implementation Documentation

This document covers the microservice architecture, containerisation strategy, networking rules, and how each feature and release maps onto that architecture.
---

## 1. Project description

VoyageAI is an Agentic AI travel planning application built by a team of five students, one feature each. Rather than integrating live flight, hotel or mapping APIs, it uses a controlled, pre-populated travel dataset — the team's effort goes into correct microservice architecture, CRUD functionality, and meaningful AI integration, not third-party API plumbing.

The application is split into five independent-but-connected features:

1. Destination Manager
2. Accommodation Manager
3. Activity Manager
4. Budget Manager
5. Itinerary Manager

A shared microservice provides the unified homepage/navigation and a shared access layer used across features. See section 3 for the full feature breakdown.

---

## 2. High-level architecture

Each feature - and the shared service - is a **fully independent project**, each with its own `docker-compose.yml` and three layers, matching the required "own frontend, backend, database" structure per student:

```
voyageai/
├── docker-compose.yml            # root file: brings up ALL services together
├── shared/
│   ├── docker-compose.yml        # also runs standalone
│   ├── frontend/Dockerfile
│   ├── backend/Dockerfile
│   └── database/Dockerfile
├── destination-service/
│   ├── docker-compose.yml
│   ├── frontend/Dockerfile
│   ├── backend/Dockerfile
│   └── database/Dockerfile
├── accommodation-service/   ... same shape
├── activity-service/        ... same shape
├── budget-service/          ... same shape
└── itinerary-service/       ... same shape
```

Six independent projects total (`shared` + 5 features) = 6 compose files, 18 containers, 18 Dockerfiles (one per layer-folder, three per project).

### Why three separate Dockerfiles per feature, not one

A single Dockerfile builds one image. To get frontend, backend, and database running as three genuinely separate containers, each layer needs its own Dockerfile — a multi-stage single Dockerfile only produces multiple images if each stage is explicitly built with `--target`, which fights the simplicity of "one Dockerfile per layer-folder" used here.

### Frontend is a Flask app, not a static file server

Each `frontend/` folder is itself a small Flask app (`app.py`) serving server-rendered HTMX templates — not a static nginx container. This matches the stated stack ("Flask APIs, HTMX frontends") and means the frontend's Dockerfile follows the same shape as the backend and database Dockerfiles (`python:slim` base, `pip install -r requirements.txt`, `CMD ["python", "app.py"]`), just listening on port `3000` instead of `5000`/`6000`.

### Why SQLite needs a thin HTTP wrapper

SQLite is an embedded, file-based database — it has no server process listening on a network port. So each `database-service` is a small Flask app that:

1. Opens a single `.db` file on disk, and
2. Exposes CRUD/query endpoints on internal port `6000`.

This is what makes "database as its own containerised layer with a port" meaningful, and lets the backend (and, per the access rules below, other features) talk to it the same way they'd talk to any HTTP service.

The `.db` file itself lives in a **named Docker volume** (`<feature>-db-data`), so it survives container rebuilds. Only one process should ever write to a given SQLite file at a time — satisfied here because each `database-service` container is the sole owner of its own file.

---

## 3. Ports

| Layer    | Internal port (fixed) | Shared | Feature N |
| -------- | --------------------- | ------ | --------- |
| frontend | 3000                  | 3000   | 300N      |
| backend  | 5000                  | 5000   | 500N      |
| database | 6000                  | 6000   | 600N      |

The **internal** port is always the same regardless of which feature — only the host-facing (external) port differs. This is what lets every `database-service` be called at the same fixed internal `:6000` regardless of which feature owns it.

| N   | Feature               | Frontend | Backend | Database |
| --- | --------------------- | -------- | ------- | -------- |
| 1   | Destination Manager   | 3001     | 5001    | 6001     |
| 2   | Accommodation Manager | 3002     | 5002    | 6002     |
| 3   | Activity Manager      | 3003     | 5003    | 6003     |
| 4   | Budget Manager        | 3004     | 5004    | 6004     |
| 5   | Itinerary Manager     | 3005     | 5005    | 6005     |

> Ports above 5900 are blocked by some browsers for direct navigation (X11 / IRC protocol conflicts) — use `curl` or Postman to hit `database-service` endpoints directly rather than a browser address bar, or remap the host port if browser access is needed.

---

## 4. Networking rules

Because each feature is its own compose project, they don't share a network automatically — Compose isolates each project by default. Every project declares two networks:

- **`internal`** (private, one per project): frontend ↔ backend ↔ database, using service names (`backend`, `database`) and internal ports. Never shared with any other project.
- **`microservices-net`** (external, shared by all 6 projects): **only** `database-service` containers join it.

### The core access rule

**Frontends never leave their own project. Backends never talk to each other. The only cross-feature door is the database layer.**

- `backend-service` applies business logic for its _own_ frontend and _own_ database only — it has no cross-project network path at all.
- `database-service` is a standalone HTTP data-access layer, reachable both by its own project's backend (over `internal`) and by every other project's backend (over `microservices-net`).

This means if the Itinerary Manager needs Activity data, its backend calls the Activity Manager's **database layer** directly:

```
requests.get("http://activity-service-db:6000/activities?destination=Tokyo")
```

— never the Activity Manager's backend. This mirrors the real relationship between the features: Itinerary needs raw Activity/Accommodation/Budget
_data_, not business rules that live in someone else's backend.

### One-time setup

```
docker network create microservices-net
```

Required before starting any individual project or the root compose file, since every project's `microservices-net` is declared `external: true`.

### Root compose file

A root `docker-compose.yml` uses Compose's `include:` key to bring up all 6 projects with a single command, while every project still works standalone:

```
include:
  - shared/docker-compose.yml
  - destination-service/docker-compose.yml
  - accommodation-service/docker-compose.yml
  - activity-service/docker-compose.yml
  - budget-service/docker-compose.yml
  - itinerary-service/docker-compose.yml
```

`include:` requires Docker Compose v2.20+; check with `docker compose version` on whatever machine will run or grade this.

---

## 5. Feature data models (starting point)

Each table needs 10+ pre-populated records per the brief. Suggested starting schemas:

**Destination Manager**
`city, country, description, avg_daily_cost, recommended_trip_length, travel_style, categories`

**Accommodation Manager**
`name, destination_id, type, price_per_night, rating, location, amenities, description`

**Activity Manager**
`name, destination_id, category, cost, duration, description, recommended_time_of_day`

**Budget Manager**
`expense, category, estimated_cost, actual_cost, destination_id, status`

**Itinerary Manager**
`trip_id, day, start_time, end_time, activity_id, destination_id, estimated_cost, notes`

Cross-references (`destination_id`, `activity_id`, etc.) are resolved by calling the owning feature's `database-service` over `microservices-net` rather than a foreign key into another feature's SQLite file — each database is fully owned by exactly one feature.

---

## 6. AI integration by release

### Release 0 — Agentic AI Foundations

Each feature implements a basic **Plan → Act → Observe → Adapt** loop against its own data, using Ollama with an approved model (Qwen/Llama/DeepSeek).

Example (Activity Manager):

| Step    | Action                                                         |
| ------- | -------------------------------------------------------------- |
| Plan    | Determine the user's preferences and required activity info    |
| Act     | Retrieve the relevant activity records from `database-service` |
| Observe | Identify activities matching the stated preferences            |
| Adapt   | Generate a recommendation from the available options           |

### Release 1 — MCP and RAG

**MCP server** exposes structured tools over the application's own data:

- `get_destinations()`
- `get_accommodations()`
- `get_activities()`
- `get_budget()`
- `get_itinerary()`

This lets the AI decide which feature's data it needs before answering — e.g. "Can I afford Universal Studios in my Osaka itinerary?" requires both the Activity cost and the remaining Budget.

**RAG server** provides a separate travel knowledge base (destination guides, transport, etiquette, food guides) so answers like "What temple etiquette should I know in Kyoto?" are grounded in retrieved documents rather than model recall alone.

### Release 2 — Multi-Agent Travel Planning

A shared Multi-Agent System adds the final "Build My Trip" feature:

- **Planner Agent** — breaks the request into steps (retrieve destinations → find accommodation → select activities → check budget → build itinerary → validate)
- **Worker Agent** — executes those steps against the five features' data **Reviewer Agent** — checks the generated plan for budget overruns, overloaded days, missing accommodation, or unmet preferences
- **Human review** — the user can approve, request changes, or reject the generated plan

---

## 7. CI/CD

Per the reference repo structure, each feature gets its own GitHub Actions workflow (`destination-service-ci.yml`, etc.) building and testing that feature's three containers independently, plus:

- `integration-ci.yml` — brings up the full system (via the root compose file) and runs integration tests across features
- `cloud-deployment.yml` — deploys the integrated application

---

## 8. Summary of key decisions and why

| Decision                                                                | Reason                                                                                                                                                           |
| ----------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 3 Dockerfiles per feature, not 1                                        | Each layer is a genuinely separate container; a single Dockerfile only produces one image (or several via `--target`, which adds complexity for no benefit here) |
| SQLite wrapped in a Flask `database-service`                            | SQLite has no server process; wrapping it in HTTP makes "database as a containerised layer with a port" meaningful                                               |
| One compose file per feature, not one root file with everything inlined | Matches the reference architecture and lets each student build/run/rebuild independently                                                                         |
| `database-service` is the only cross-feature network door               | Reflects that features need each other's _data_, not each other's _business logic_; keeps ownership of business rules strictly per-feature                       |
| Root `docker-compose.yml` via `include:`                                | One-command full-stack startup without losing each feature's independence                                                                                        |
