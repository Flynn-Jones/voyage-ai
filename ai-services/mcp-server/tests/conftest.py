import sys
from pathlib import Path

import pytest
from starlette.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import mcp_http_server  # noqa: E402


@pytest.fixture(scope="session")
def client():
    # One app for the whole session: FastMCP's StreamableHTTPSessionManager
    # can only run once per instance, so per-module clients would collide.
    with TestClient(mcp_http_server.create_app(), base_url="http://localhost:7001") as c:
        yield c
