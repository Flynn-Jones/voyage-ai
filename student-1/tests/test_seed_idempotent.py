"""Offline unit tests for database/init_db.py seeding behaviour.

No Docker required: exercises init_db.py directly against a tmp_path SQLite
file, proving that seeding is a one-time, non-destructive operation.
"""
import importlib.util
import json
import os
import sys

MODULE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "database", "init_db.py"
)


def _load_init_db():
    spec = importlib.util.spec_from_file_location("destination_init_db", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules["destination_init_db"] = module
    spec.loader.exec_module(module)
    return module


init_db = _load_init_db()


def test_seed_fills_empty_table_with_ten_records(tmp_path):
    db_file = str(tmp_path / "seed_test.sqlite")
    conn = init_db.get_connection(db_file)
    init_db.init_schema(conn)

    inserted = init_db.seed_if_empty(conn)

    assert inserted == 10
    rows = conn.execute("SELECT * FROM destinations ORDER BY destination_id").fetchall()
    assert len(rows) == 10
    cities = {row["city"] for row in rows}
    assert "Tokyo" in cities
    assert "Kyoto" in cities

    tokyo = next(row for row in rows if row["city"] == "Tokyo")
    categories = json.loads(tokyo["categories"])
    assert isinstance(categories, list)
    assert len(categories) > 0

    conn.close()


def test_seed_does_not_duplicate_on_second_call(tmp_path):
    db_file = str(tmp_path / "seed_test.sqlite")
    conn = init_db.get_connection(db_file)
    init_db.init_schema(conn)

    first = init_db.seed_if_empty(conn)
    second = init_db.seed_if_empty(conn)

    assert first == 10
    assert second == 0
    count = conn.execute("SELECT COUNT(*) FROM destinations").fetchone()[0]
    assert count == 10

    conn.close()


def test_seed_does_not_wipe_user_created_rows(tmp_path):
    db_file = str(tmp_path / "seed_test.sqlite")
    conn = init_db.get_connection(db_file)
    init_db.init_schema(conn)
    init_db.seed_if_empty(conn)

    conn.execute(
        "INSERT INTO destinations (city, country, categories) VALUES (?, ?, ?)",
        ("Demo City", "Australia", json.dumps(["test"])),
    )
    conn.commit()

    inserted_again = init_db.seed_if_empty(conn)

    assert inserted_again == 0
    count = conn.execute("SELECT COUNT(*) FROM destinations").fetchone()[0]
    assert count == 11
    demo = conn.execute(
        "SELECT * FROM destinations WHERE city = ?", ("Demo City",)
    ).fetchone()
    assert demo is not None

    conn.close()
