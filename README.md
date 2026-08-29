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

```
docker network create microservices-net    # one-time setup
docker compose up --build                  # brings up all services via the root compose file
```

Each feature can also be run independently from its own folder:

```
cd destination-service && docker compose up --build
```

See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for the full port map and
networking rules.
