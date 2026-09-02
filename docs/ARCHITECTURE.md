# VoyageAI — Architecture & Implementation Documentation

This document covers the microservice architecture, containerisation strategy, networking rules, and how each feature and release maps onto that architecture.
---

## 1. Project description

VoyageAI is an Agentic AI travel planning application built by a team of five students, one feature each. Rather than integrating live flight, hotel or mapping APIs, it uses a controlled, pre-populated travel dataset — the team's effort goes into correct microservice architecture, CRUD functionality, and meaningful AI integration, not third-party API plumbing.

The application is split into five independent-but-connected features:

1. Destination Manager (Student 1)
2. Accommodation Manager (Student 2)
3. Activity Manager (Student 3)
4. Budget Manager (Student 4)
5. Itinerary Manager (Student 5)

A shared microservice provides the unified homepage/navigation and a shared access layer used across features. See section 3 for the full feature breakdown.

---

## 2. High-level architecture

Each feature - and the shared service - is a **fully independent project**, each with its own `docker-compose.yml` and three layers, matching the required "own frontend, backend, database" structure per student:

```
voyageai/
├── docker-compose.yml            # root file: includes implemented service compose files
├── up-all.sh                     # robust script to build and start all available projects independently
├── down-all.sh                   # stops all service projects
├── shared/
│   ├── docker-compose.yml        # shared canonical service
│   ├── frontend/
│   ├── backend/
│   └── database/
├── accommodation-service/        # Student 2 accommodation management
├── budget-service/               # Student 4 budget management
│   ├── docker-compose.yml
│   ├── budget-fe/
│   ├── budget-be/
│   └── budget-db/
├── destination-service/          # Student 1 destination management (placeholder)
├── activity-service/             # Student 3 activity management (placeholder)
└── itinerary-service/            # Student 5 itinerary management (placeholder)
```

### Why three separate Dockerfiles per feature, not one

A single Dockerfile builds one image. To get frontend, backend, and database running as three genuinely separate containers, each layer needs its own Dockerfile or build context. In project-based folders, each layer is containerised independently.

### Frontend is a Flask app (or Nginx/Flask serving HTMX), not a static file server

Each frontend component serves server-rendered templates (often via Flask or Nginx serving static/proxied templates). This matches the stack ("Flask APIs, HTMX frontends") and follows container best practices.

### Why SQLite needs a thin HTTP wrapper

SQLite is an embedded, file-based database — it has no server process listening on a network port. So each `database-service` is a small Flask app that:

1. Opens a single `.db` file on disk, and
2. Exposes CRUD/query endpoints on internal port `6000` (or `600N`).

This is what makes "database as its own containerised layer with a port" meaningful, and lets the backend (and other features) talk to it the same way they'd talk to any HTTP service.

The `.db` file itself lives in a **named Docker volume** (`<feature>-db-data`), so it survives container rebuilds. Only one process should ever write to a given SQLite file at a time — satisfied here because each `database-service` container is the sole owner of its own file.

---

## 3. Ports

| Layer    | Internal port (fixed) | Shared | Feature N |
| -------- | --------------------- | ------ | --------- |
| frontend | 3000 (or mapped)      | 3000   | 300N      |
| backend  | 5000 (or mapped)      | 5000   | 500N      |
| database | 6000 (or mapped)      | 6000   | 600N      |

The **internal** port is standard per layer across services, while external host ports are mapped distinctly:

| N   | Feature               | Frontend | Backend | Database |
| --- | --------------------- | -------- | ------- | -------- |
| 0   | Shared Service        | 3000     | 5000    | 6000     |
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
- **`microservices-net`** (external, shared by all projects): **only** `database-service` (and backends needing cross-service queries) join it.

### The core access rule

**Frontends never leave their own project. Backends never talk to each other. The only cross-feature door is the database layer.**

- `backend-service` applies business logic for its _own_ frontend and _own_ database only.
- `database-service` is a standalone HTTP data-access layer, reachable both by its own project's backend (over `internal`) and by every other project's backend (over `microservices-net`).

This means if one service needs data from another feature, its backend calls that feature's **database layer** directly:

```
requests.get("http://accomodation-db:6002/accommodations")
```

— never another feature's backend. This mirrors the real relationship between features: services need raw domain _data_, not foreign business logic.

### One-time setup

```
docker network create microservices-net
```

Required before starting projects or the root compose file, since every project's `microservices-net` is declared `external: true`.

### Startup methods

1. **Via `up-all.sh` script (Recommended):**

   ```
   ./up-all.sh
   ```

   Iterates through each microservice directory (`shared`, `accommodation-service`, `budget-service`, etc.), checks for a `docker-compose.yml`, creates the external network if missing, and runs `docker compose up -d --build` independently for each project. This avoids service name collisions (`frontend`/`backend`/`database`) across independent compose files and gracefully skips unimplemented/missing folders.

2. **Via Root Compose File (`docker-compose.yml`):**
   Uses Compose's `include:` key to bring up implemented services:
   ```
   include:
     - shared/docker-compose.yml
     - budget-service/docker-compose.yml
     # - accommodation-service/docker-compose.yml
     # ...
   ```

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

Cross-references (`destination_id`, `activity_id`, etc.) are resolved by calling the owning feature's `database-service` over `microservices-net`.

---

## 6. AI integration by release

### Release 0 — Agentic AI Foundations

Each feature implements a basic **Plan → Act → Observe → Adapt** loop against its own data, using Ollama with an approved model (Qwen/Llama/DeepSeek).

### Release 1 — MCP and RAG

- **MCP server** exposes structured tools over application data.
- **RAG server** provides a separate travel knowledge base.

### Release 2 — Multi-Agent Travel Planning

A shared Multi-Agent System adds the "Build My Trip" feature (Planner Agent → Worker Agent → Reviewer Agent → Human review).

---

## 7. CI/CD

Each feature has its own GitHub Actions workflow building and testing its containers independently, alongside `integration-ci.yml` and `cloud-deployment.yml`.

---

## 8. Summary of key decisions and why

| Decision                                                  | Reason                                                                                                             |
| --------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------ |
| 3 Dockerfiles / build contexts per feature                | Each layer runs as a genuinely separate container                                                                  |
| SQLite wrapped in a Flask `database-service`              | SQLite has no server process; wrapping it in HTTP makes "database as a containerised layer with a port" meaningful |
| One compose file per feature + `up-all.sh`                | Lets each student build/run/rebuild independently without service name conflicts (`frontend`/`backend`/`database`) |
| `database-service` is the only cross-feature network door | Reflects that features need each other's _data_, not each other's _business logic_                                 |
| Root startup via `up-all.sh` or `include:`                | Enables one-command startup while maintaining project independence                                                 |
