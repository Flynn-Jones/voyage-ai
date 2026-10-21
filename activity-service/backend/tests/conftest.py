import os
import sys

import pytest

BACKEND_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_ROOT not in sys.path:
    sys.path.insert(0, BACKEND_ROOT)


class DummyResponse:
    """Minimal stand-in for requests.Response, matching the pattern already
    used in accommodation-service's own tests (test_forwarding.py)."""

    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload

    @property
    def text(self):
        return "" if self._payload is None else str(self._payload)


@pytest.fixture
def client():
    """A Flask test client. The backend never touches SQLite directly (it
    reads AND writes through database-service's HTTP API), so every test
    that hits create/read/update/delete mocks db_client's requests calls —
    no temp database file needed here any more."""
    import app as app_module
    flask_app = app_module.create_app()
    with flask_app.test_client() as test_client:
        yield test_client
