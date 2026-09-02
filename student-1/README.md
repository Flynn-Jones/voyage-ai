# Destination Manager (Student 1)

Three containers implementing `frontend -> backend -> database API -> SQLite`.
Session 1 proved the skeleton; Session 2 added the destination CRUD pipeline
and 10+ seed records; Session 3 adds a server-rendered Flask + HTMX frontend
on top of it. `destination-database` is the only service that opens
SQLite — the backend talks to it exclusively over HTTP, and the frontend
talks only to the backend.

## Run

```bash
docker network create microservices-net 2>/dev/null || true
docker compose -f student-1/docker-compose.yml up -d --build
```

## Ports

| Service               | Host port | URL                          |
| --------------------- | --------- | ----------------------------- |
| destination-frontend  | 3001      | http://localhost:3001         |
| destination-backend   | 5001      | http://localhost:5001/health  |
| destination-database  | 6001      | http://localhost:6001/health  |

Other team services reach the database over the shared `microservices-net`
network as `http://destination-db:6001` (or `http://destination-database:6001`).

## Destination routes (backend, port 5001)

| Method | Route                          | Notes                                             |
| ------ | ------------------------------- | -------------------------------------------------- |
| GET    | `/api/destinations`             | optional `city`, `country`, `travel_style` filters |
| GET    | `/api/destinations/<id>`        | 404 if missing                                     |
| POST   | `/api/destinations`             | 400 if `city`/`country` missing or fields invalid  |
| PUT    | `/api/destinations/<id>`        | partial update; 404 if missing, 400 if invalid     |
| DELETE | `/api/destinations/<id>`        | 204; 404 if missing                                |

If the database service is unreachable, these routes return **503**
`{"error": "destination database unavailable"}` instead of crashing.

The database service exposes the same shape one layer down on port 6001
(`/destinations`, `/destinations/<id>`) — this is what other students' backends
call directly over `microservices-net` for cross-feature reads.

### Fields

`destination_id, city, country, description, average_daily_cost,
recommended_trip_length, travel_style, categories`

`categories` is a JSON array on the wire (e.g. `["food", "nightlife"]`) and is
stored as JSON text in SQLite.

### Seed data

10 deterministic records (ids 1–10), inserted only when the table is empty —
restarting the container never duplicates them and never wipes rows created
through the API: Tokyo, Kyoto, Osaka, Seoul, Bangkok, Singapore, Sydney,
Melbourne, Paris, Rome.

### Example

```bash
curl http://localhost:5001/api/destinations

curl -X POST http://localhost:5001/api/destinations \
  -H "Content-Type: application/json" \
  -d '{"city":"Demo City","country":"Australia","description":"Temporary test record","average_daily_cost":150,"recommended_trip_length":3,"travel_style":"City break","categories":["food"]}'

# use the destination_id from the response above, not a hardcoded value
curl -X PUT http://localhost:5001/api/destinations/<id> \
  -H "Content-Type: application/json" \
  -d '{"average_daily_cost":175}'

curl -X DELETE http://localhost:5001/api/destinations/<id>
```

## Frontend (Session 3, port 3001)

Server-rendered Flask + HTMX UI. All persistence goes through the backend at
`BACKEND_SERVICE_URL` (compose sets `http://destination-backend:5001`) — the
frontend never opens SQLite and never calls the database API directly.

| Route | Methods | Purpose |
| ----- | ------- | ------- |
| `/` | GET | List destinations; `?q=` free-text search and `?country=` filter, both applied in the frontend since the backend only supports exact-match `city`/`country`/`travel_style` filters. Returns an HTMX partial when the request carries `HX-Request: true`. |
| `/destinations/new` | GET, POST | Create form / submit. |
| `/destinations/<id>` | GET | Detail page. |
| `/destinations/<id>/edit` | GET, POST | Edit form / submit (partial update). |
| `/destinations/<id>/delete` | GET, POST | Delete confirmation page / perform delete. |
| `/health` | GET | Frontend's own liveness. |

Search and filter run over one unfiltered `GET /api/destinations` fetch, so
the search box catches city, country, description, travel style and
categories, and the country dropdown always lists every country even while
filtered. The search/filter form is progressively enhanced: it works as a
plain GET form without JavaScript, and HTMX (loaded from `unpkg.com`, no
local asset) swaps in just the `#results` fragment when available.

Categories are entered as a comma-separated string
(`food, culture, nightlife`) and converted to/from the backend's JSON array.
Invalid numeric input (e.g. a non-numeric `average_daily_cost`) is forwarded
to the backend as-is rather than rejected locally, so the backend's own
validation message is what the user sees.

## AI comparison (Session 4, backend `POST /api/destinations/ai-compare`)

Retrieves the two named destinations through the database API (never invents
data), builds one grounded prompt, and makes a single Ollama chat completion
call. Logs `[PLAN]`/`[ACT]`/`[OBSERVE]`/`[ADAPT]` stages at `INFO` level.

| Method | Route | Notes |
| ------ | ----- | ----- |
| POST | `/api/destinations/ai-compare` | Body: `{"city_a", "city_b", "preferences"}`. 400 if either city is missing or they're the same; 404 if a city isn't in the database; 503 if the database is unreachable; 502 if Ollama is unreachable or returns something unexpected. |

```bash
curl -X POST http://localhost:5001/api/destinations/ai-compare \
  -H "Content-Type: application/json" \
  -d '{"city_a":"Tokyo","city_b":"Kyoto","preferences":"nightlife and food"}'
```

Frontend: `/compare` (GET/POST) — two dropdowns populated from the live
destination list plus a preferences field, submitted via an HTMX `hx-post`
that swaps in the result (with a spinner) without a full page reload; also
works as a plain form POST with no JavaScript.

### Configuration

Ollama's base URL and model are read from environment variables, not
hardcoded — see `student-1/.env.example` for the names and defaults.
`docker-compose.yml` interpolates them as `${OLLAMA_BASE_URL:-...}` /
`${OLLAMA_MODEL:-...}` so they can be overridden from the shell or a local
`.env` (never committed) without editing the compose file.

**Prerequisite:** the model tag must already be pulled on the machine running
Ollama — `ollama pull qwen2.5:0.5b` (or whichever tag `OLLAMA_MODEL` names).
Run `ollama list` to check what's installed before demoing; the default here
(`qwen2.5:0.5b`) was chosen because it's small and fast, not because it's the
best available — swap `OLLAMA_MODEL` to `llama3.1:8b` for higher-quality
(slower) output if the demo machine has it pulled.

If Ollama is unreachable, `/api/destinations/ai-compare` returns 502 with
`{"error": "AI comparison service is unavailable."}` — CRUD is unaffected.

## Test

```bash
pip install -r student-1/tests/requirements.txt
pytest student-1/tests -v
```

- `test_health.py` — Session 1 smoke tests (live stack required).
- `test_seed_idempotent.py` — offline; seeding fills an empty table once,
  never duplicates, never wipes user-created rows.
- `test_service_failure.py` — offline; backend maps an unreachable database
  to a controlled 503.
- `test_destinations_api.py` — live stack required; full CRUD lifecycle,
  seed contents, and 404/400 error paths through the backend.
- `test_frontend_routes.py` — offline; frontend routes against a
  monkeypatched backend: search/filter, the HTMX partial-vs-full-page
  branch, create/edit/delete, and that invalid numeric input never raises.
- `test_frontend_live.py` — live stack required; the same behaviours plus a
  full create → edit → delete lifecycle driven through the frontend's own
  routes, using the id the app returns rather than a hard-coded one.
