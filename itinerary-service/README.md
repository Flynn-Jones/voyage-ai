# VoyageAI Itinerary Manager

Student 5's Itinerary Manager is scaffolded as three separately containerised layers:

```text
Browser -> itinerary-fe -> itinerary-be -> itinerary-db -> SQLite
```

## Ports

| Layer | Container | Host port |
| --- | --- | ---: |
| Frontend | `itinerary-fe` | 3005 |
| Backend | `itinerary-be` | 5005 |
| Database API | `itinerary-db` | 6005 |

The backend reaches its database API over the private Compose network at
`http://itinerary-db:6005`. The SQLite file is owned only by the database
container and is persisted in the `itinerary-db-data` named volume.

## Start standalone

From the repository root, create the shared external network once and start
the service:

```bash
docker network inspect microservices-net >/dev/null 2>&1 || docker network create microservices-net
docker compose -f itinerary-service/docker-compose.yml up --build
```

Then open <http://localhost:3005>. Health endpoints are available at
<http://localhost:5005/health> and <http://localhost:6005/health>.

Stop the service with:

```bash
docker compose -f itinerary-service/docker-compose.yml down
```

This is the Stage 1 scaffold only. Itinerary CRUD, day views, reference-service
integration, and Ollama-powered AI review will be added in later stages.
