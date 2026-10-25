## Post-commit endpoint tests (student-3 backend, live stack)

| Test | Endpoint function | Result |
|---|---|---|
| `test_backend_endpoints.py::test_add_activity_is_201_and_persists_in_the_database` | POST /api/add_activity -> add_activity (201, read back from the database) | PASS |
| `test_backend_endpoints.py::test_view_activities_filters_and_sorts` | GET /api/view_activities -> view_activities (filters and sort) | PASS |
