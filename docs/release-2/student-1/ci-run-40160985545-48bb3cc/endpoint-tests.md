## Post-commit endpoint tests (student-1 backend, live stack)

| Test | Endpoint function | Result |
|---|---|---|
| `test_destinations_api.py::test_seed_count` | GET /api/destinations -> list_destinations | PASS |
| `test_destinations_api.py::test_crud_lifecycle` | POST /api/destinations -> create_destination (then GET/PUT/DELETE/404) | PASS |
