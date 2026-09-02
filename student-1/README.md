# Destination Manager (Student 1)

Session 1 skeleton: three containers proving the architecture
`frontend -> backend -> database API -> SQLite` before CRUD/AI logic lands.

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

## Scope

Session 1 registers only `/` and `/health` on each service — no destination
data routes yet. See `docs/ARCHITECTURE.md` for the full team architecture
and `student-1/tests/test_health.py` for the smoke tests.

## Test

```bash
pip install -r student-1/tests/requirements.txt
pytest student-1/tests -v
```
