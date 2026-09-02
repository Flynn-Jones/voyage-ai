"""Offline unit tests for backend/app.py's database-unavailable handling.

No Docker required: points DATABASE_SERVICE_URL at an unreachable address
(a discard port on loopback) before importing the backend app, then drives
it with Flask's test client to prove /api/destinations maps a connection
failure to a controlled 503, not a crash or a passthrough error.
"""
import importlib.util
import os
import sys

os.environ["DATABASE_SERVICE_URL"] = "http://127.0.0.1:9"

MODULE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend", "app.py"
)
BACKEND_DIR = os.path.dirname(MODULE_PATH)
if BACKEND_DIR not in sys.path:
    # backend/app.py imports llm_client (a sibling module, not a package);
    # loading app.py by path doesn't add its own directory to sys.path the
    # way running it as __main__ would, so this needs to be explicit.
    sys.path.insert(0, BACKEND_DIR)


def _load_backend_app():
    spec = importlib.util.spec_from_file_location("destination_backend_app", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules["destination_backend_app"] = module
    spec.loader.exec_module(module)
    return module


backend_app = _load_backend_app()


def test_list_destinations_returns_503_when_database_unreachable():
    client = backend_app.app.test_client()
    response = client.get("/api/destinations")
    assert response.status_code == 503
    assert "error" in response.get_json()


def test_create_destination_returns_503_when_database_unreachable():
    client = backend_app.app.test_client()
    response = client.post(
        "/api/destinations",
        json={"city": "Demo City", "country": "Australia"},
    )
    assert response.status_code == 503
    assert "error" in response.get_json()
