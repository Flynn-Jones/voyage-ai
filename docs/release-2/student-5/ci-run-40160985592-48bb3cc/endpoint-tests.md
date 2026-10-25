## Post-commit endpoint tests (student-5 backend, live stack)

| Test | Endpoint function | Result |
|---|---|---|
| `itinerary-service/ci/test_endpoints.py::test_post_create_returns_201_and_persists` | POST /api/itinerary -> create_item (201, persisted in the database) | PASS |
| `itinerary-service/ci/test_endpoints.py::test_put_update_returns_200_and_persists` | PUT /api/itinerary/<id> -> update_item (200, persisted in the database) | PASS |
