"""Cloud-readiness behaviour that does not need containers: AI-Mode switch, health checks,
budget-db validation and seed-only-when-empty."""
import importlib.util
import sqlite3
import sys
from pathlib import Path

import pytest

from app import create_app
from services import llm_client

BUDGET_DB = Path(__file__).resolve().parent.parent / "budget-db"


def load_budget_db(monkeypatch, db_file):
    """Import budget-db's init_db/app from file (budget-be already owns the name 'app')."""
    monkeypatch.setenv("DB_FILE", str(db_file))
    monkeypatch.syspath_prepend(str(BUDGET_DB))
    sys.modules.pop("init_db", None)
    init_db = importlib.import_module("init_db")
    spec = importlib.util.spec_from_file_location("budget_db_app", BUDGET_DB / "app.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return init_db, module


@pytest.mark.parametrize("path, body", [
    ("/budget/ai-analyse", {"trip_reference": "TRIP-1001"}),
    ("/budget/ai-add-expense", {"prompt": "Add a taxi for 20 dollars"}),
])
def test_ai_mode_routes_403_when_disabled(monkeypatch, path, body):
    monkeypatch.setattr(llm_client, "AI_ENABLED", False)
    response = create_app().test_client().post(path, json=body)
    assert response.status_code == 403
    assert response.get_json() == {"status": "error", "error": "AI-Mode disabled"}


def test_backend_health_has_no_dependencies():
    response = create_app().test_client().get("/health")  # budget-db is not running in unit tests
    assert response.status_code == 200 and response.get_json()["status"] == "ok"


def test_database_health_and_validation(monkeypatch, tmp_path):
    init_db, db_app = load_budget_db(monkeypatch, tmp_path / "budget.sqlite")
    init_db.main()
    client = db_app.create_app().test_client()
    assert client.get("/health").get_json()["status"] == "ok"
    ok = {"trip_reference": "T-1", "expense": "Taxi", "category": "Transport",
          "estimated_cost": 20, "destination_id": "dest-x"}
    assert db_app.validate_expense(ok) is None
    assert "estimated_cost" in db_app.validate_expense({**ok, "estimated_cost": "20"})
    assert "status must be one of" in db_app.validate_expense({**ok, "status": "Refunded"})
    assert db_app.validate_expense({"actual_cost": None}, partial=True) is None
    assert "no updatable fields" in db_app.validate_expense({}, partial=True)


def test_seed_only_when_empty(monkeypatch, tmp_path):
    db_file = tmp_path / "budget.sqlite"
    init_db, _ = load_budget_db(monkeypatch, db_file)
    init_db.main()
    count = lambda: sqlite3.connect(db_file).execute("SELECT COUNT(*) FROM expenses").fetchone()[0]  # noqa: E731
    seeded = count()
    assert seeded >= 10
    with sqlite3.connect(db_file) as conn:
        conn.execute("DELETE FROM expenses WHERE id = 1")
    init_db.main()                      # restart: keeps the user's data
    assert count() == seeded - 1
    monkeypatch.setenv("BUDGET_DB_RESEED", "1")
    init_db.main()                      # explicit reset
    assert count() == seeded
