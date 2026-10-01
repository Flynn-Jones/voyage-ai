# VoyageAI — Agentic AI Travel Planning & Itinerary Management System

VoyageAI is an Agentic AI travel planning application that helps users build
and manage personalised trips using structured information about
destinations, accommodation, activities, budgets and itineraries. Rather than
integrating live flight, hotel or mapping APIs, VoyageAI uses a controlled,
pre-populated travel dataset — letting the team focus on microservice
architecture, CRUD functionality and AI integration rather than third-party
API plumbing.

A user can ask, for example:

> "Plan a 10-day Japan trip under $4,000 across Tokyo, Kyoto and Osaka,
> focused on food, nightlife and culture."

VoyageAI uses the data stored across its five features to select
destinations, accommodation and activities, respect the budget, construct an
itinerary, and review the plan for issues — following a **Plan → Act →
Observe → Adapt** agentic workflow.

## Why this project

The brief requires one integrated Agentic AI application built by a team of
five, where each student independently owns a frontend, backend/API and
database microservice, implements CRUD, populates each table with 10+
records, and integrates an approved AI model. VoyageAI splits naturally into
five features that are independent enough to satisfy that requirement, but
genuinely dependent on each other in the finished product — a real itinerary
needs destinations, accommodation, activities and a budget together, not five
disconnected demos.

## The five features

| #   | Feature                   | Owns                                                              | Example AI task                                                 |
| --- | ------------------------- | ----------------------------------------------------------------- | --------------------------------------------------------------- |
| 1   | **Destination Manager**   | Cities, countries, descriptions, avg. daily cost, travel style    | "Compare Tokyo and Kyoto for someone into nightlife and food."  |
| 2   | **Accommodation Manager** | Hotels/stays per destination, price, rating, amenities            | "Recommend Tokyo accommodation under $150/night for nightlife." |
| 3   | **Activity Manager**      | Attractions/experiences per destination, cost, duration, category | "Recommend Tokyo activities for someone into food and tech."    |
| 4   | **Budget Manager**        | Estimated vs actual cost per expense/category/destination         | "Identify where this trip could reduce spending."               |
| 5   | **Itinerary Manager**     | Day-by-day schedule tying everything together                     | "Is Day 4 of the itinerary too busy?"                           |

Each feature is a fully independent microservice (own frontend, backend, and
SQLite database) — see [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for how
they're containerised and how they talk to each other.

## Tech stack

- **Python** — backend logic
- **Flask** — REST APIs for each backend and database layer
- **HTMX** — frontend microservices (no heavy JS framework)
- **HTML/CSS/JavaScript** — interface
- **SQLite** — one database file per feature
- **Docker / Docker Compose** — containerisation and local integration
- **GitHub Actions** — one CI workflow per student, plus integration and
  cloud-deployment workflows
- **Ollama** (Qwen/Llama/DeepSeek) — the AI model backing each feature's
  agentic functionality

## Releases

| Release | Adds                                                                                                                                         |
| ------- | -------------------------------------------------------------------------------------------------------------------------------------------- |
| **0**   | Five CRUD microservices, shared homepage, Ollama integration, basic AI functionality per feature, Docker Compose integration, per-student CI |
| **1**   | MCP server (structured tool access to app data) + RAG server (grounded travel knowledge)                                                     |
| **2**   | Multi-agent system (Planner → Worker → Reviewer → Human review) producing a full "Build My Trip" plan                                        |

Full detail on each release, the architecture, and how services communicate
is in [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

## Running the project locally

### Prerequisites

- Docker Desktop (Compose v2)
- [Ollama](https://ollama.com) running on the host with the project model pulled:
  ```bash
  ollama pull qwen2.5:7b
  ```
  Every backend reaches it at `host.docker.internal:11434`, so it stays on the
  host and is never containerised.

### 1. Start the containerised microservices

From the repo root — this builds and runs all six stacks (18 containers):

```bash
docker compose up -d --build
```

The root compose file creates its own `voyage-net` bridge network, so **no
`docker network create` step is needed**. (That one-time setup is only required
when running a feature from its own folder — see below.)

### 2. Start the shared AI servers

The AI-Mode, MCP, and RAG layers are deliberately **not containerised** — the
brief calls for one shared local MCP server and one shared local RAG server used
by every feature. Containerised backends reach them over
`host.docker.internal`, the same way they reach Ollama.

One-time setup:

```bash
cd ai-services
python3 -m venv venv
./venv/bin/pip install -r mcp-server/requirements.txt -r rag-server/requirements.txt
```

Then run each in its own terminal:

```bash
cd ai-services/mcp-server && ../venv/bin/python mcp_http_server.py   # :7001
cd ai-services/rag-server && ../venv/bin/python rag_http_server.py   # :7002
```

Without these two running, the feature UIs still work; only their MCP and RAG
modes return an unavailable error.

### 3. Open the app

| Feature | Frontend | Backend/API | Database |
| --- | --- | --- | --- |
| Shared (homepage) | [3000](http://localhost:3000) | 5000 | 6000 |
| Destination (student-1) | [3001](http://localhost:3001) | 5011 &rarr; 5001 | 6001 |
| Accommodation | [3002](http://localhost:3002) | 5002 | 6002 |
| Activity | [3003](http://localhost:3003) | 5003 | 6003 |
| Budget | [3004](http://localhost:3004) | 5004 | 6004 |
| Itinerary | [3005](http://localhost:3005) | 5005 | 6005 |

Shared, non-containerised: **MCP server 7001**, **RAG server 7002**, Ollama 11434.

The destination backend is the one exception to the `X00N` port convention: its
host port is remapped to 5011 because another process on some dev machines owns
5001. Container-to-container traffic still uses 5001.

### 4. Smoke-test

```bash
curl localhost:7001/health          # MCP server + its registered tools
curl localhost:7002/health          # RAG server
curl localhost:5002/health          # a feature backend
curl localhost:6002/health          # a feature database

# one MCP call and one RAG call through a feature's backend/API
curl -X POST localhost:5002/accommodation/mcp/by-destination \
  -H 'Content-Type: application/json' -d '{"destination":"Tokyo"}'
curl -X POST localhost:5002/accommodation/rag/answer \
  -H 'Content-Type: application/json' -d '{"query":"What stays are logged for Tokyo?"}'
```

The first RAG call builds the corpus and can take up to a minute while the model
loads.

Every tier answers `/health` except the budget backend and budget database, which
do not expose it yet — check those with `curl localhost:5004/` and
`curl localhost:6004/expenses` instead.

### Running one feature on its own

Each feature folder keeps a standalone compose file. These share the external
`microservices-net` network for cross-feature calls, so create it once:

```bash
docker network create microservices-net     # one-time, standalone mode only
cd accommodation-service && docker compose up -d --build
```

Feature folders: `shared/`, `student-1/`, `accommodation-service/`,
`activity-service/`, `budget-service/`, `itinerary-service/`. The helper scripts
`./up-all.sh` and `./down-all.sh` start and stop every folder this way.

To run a feature with MCP and RAG disabled the way CI does:

```bash
cd accommodation-service
docker compose -f docker-compose.yml -f docker-compose.ci.yml up -d --build
```

### Stopping

```bash
docker compose down        # from the repo root
docker compose down -v     # also wipes the SQLite volumes
```
