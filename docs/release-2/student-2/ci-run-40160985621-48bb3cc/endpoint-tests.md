## Post-commit endpoint tests (student-2 backend, live stack)

| Test | Endpoint function | Result |
|---|---|---|
| `tests/test_endpoints.py::test_post_valid_accommodation_is_201_and_reads_back` | POST /accommodations -> create_accommodation (201, read back from the database) | PASS |
| `tests/test_endpoints.py::test_get_list_includes_created_record` | GET /accommodations -> list_accommodations (lists the created record) | PASS |
