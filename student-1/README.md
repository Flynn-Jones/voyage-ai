# Destination Manager (Student 1)

Three containers implementing `frontend -> backend -> database API -> SQLite`.
Session 1 proved the skeleton; Session 2 adds the destination CRUD pipeline
and 10+ seed records. `destination-database` is the only service that opens
SQLite — the backend talks to it exclusively over HTTP.

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
