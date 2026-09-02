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

Destination and Activity names are not enriched yet; their identifiers are
displayed directly. Cross-service Destination and Activity enrichment is planned
for a later stage.

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
