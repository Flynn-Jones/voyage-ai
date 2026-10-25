## Post-commit endpoint tests (student-4 backend, live stack)

| Test | Endpoint function | Result |
|---|---|---|
| `tests/test_endpoints.py::test_post_valid_expense_is_201_and_reads_back` | POST /expenses -> create_expense (201, read back from the database) | PASS |
| `tests/test_endpoints.py::test_put_valid_update_persists` | PUT /expenses/<id> -> update_expense (update persisted, other fields unchanged) | PASS |
