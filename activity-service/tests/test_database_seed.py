"""Tests for the database tier's startup seeding (create_app -> seed_if_empty).

Every test points init_db.DB_PATH at a temp file via the db_path fixture.
Run with the rest of the suite, from activity-service/: python -m pytest
"""
import sqlite3

import init_db
import seed_mock_data
from conftest import load_database_app, seed_activity, seed_assignment

ROW_QUERIES = {
    "activities": "SELECT * FROM activities ORDER BY 1",
    "activities_assignment": "SELECT * FROM activities_assignment ORDER BY 1",
}
COUNT_QUERIES = {
    "activities": "SELECT COUNT(*) FROM activities",
    "activities_assignment": "SELECT COUNT(*) FROM activities_assignment",
}


def _rows(db_path, table):
    conn = sqlite3.connect(db_path)
    rows = conn.execute(ROW_QUERIES[table]).fetchall()
    conn.close()
    return rows


def test_fresh_database_is_populated_on_startup(db_path):
    with load_database_app().create_app().test_client() as client:
        activities = client.get("/activities").get_json()
        assignments = client.get("/assignments").get_json()

    assert len(activities) == len(seed_mock_data.MOCK_ACTIVITIES)
    assert {a["activity_name"] for a in activities} == {a[0] for a in seed_mock_data.MOCK_ACTIVITIES}
    assert len(assignments) == len(seed_mock_data.MOCK_ASSIGNMENTS)


def test_populated_database_is_left_untouched(db_path):
    init_db.init_schema()  # a pre-existing DB already has its schema
    activity_id = seed_activity(db_path, activity_name="My Own Activity", activity_cost=12.5)
    assignment_id = seed_assignment(db_path, activity_id, assignment_time="2026-09-20T10:00")
    activities_before = _rows(db_path, "activities")
    assignments_before = _rows(db_path, "activities_assignment")

    load_database_app().create_app()

    assert _rows(db_path, "activities") == activities_before
    assert _rows(db_path, "activities_assignment") == assignments_before
    assert activities_before[0][0] == activity_id and assignments_before[0][0] == assignment_id
    assert len(activities_before) == 1 and len(assignments_before) == 1  # no demo rows added


def test_repeated_startup_is_idempotent(db_path):
    app_module = load_database_app()
    app_module.create_app()
    activities_first = _rows(db_path, "activities")
    assignments_first = _rows(db_path, "activities_assignment")

    app_module.create_app()
    app_module.create_app()

    assert _rows(db_path, "activities") == activities_first
    assert _rows(db_path, "activities_assignment") == assignments_first
    assert len(activities_first) == len(seed_mock_data.MOCK_ACTIVITIES)
    assert len(assignments_first) == len(seed_mock_data.MOCK_ASSIGNMENTS)


def _count(db_path, table):
    conn = sqlite3.connect(db_path)  # a separate connection: sees only committed rows
    count = conn.execute(COUNT_QUERIES[table]).fetchone()[0]
    conn.close()
    return count


def test_seed_if_empty_does_not_commit_callers_pending_work(db_path):
    init_db.init_schema()
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO activities (activity_name, activity_type, activity_cost, duration) "
        "VALUES ('Caller Pending', 'Adventure', 1.0, '1 hour')"
    )

    assert seed_mock_data.seed_if_empty(conn) == (0, 0)  # the pending row makes the table non-empty
    assert _count(db_path, "activities") == 0  # still uncommitted

    conn.rollback()
    assert conn.execute("SELECT COUNT(*) FROM activities").fetchone()[0] == 0  # caller's connection still usable
    assert _count(db_path, "activities") == 0
    conn.close()


def test_seed_if_empty_demo_rows_join_callers_transaction_and_roll_back(db_path):
    init_db.init_schema()
    conn = sqlite3.connect(db_path)  # plain connection: no row_factory, like many callers
    conn.execute("INSERT INTO activities_assignment (activity_id, assignment_time) VALUES (999, '2026-01-01T00:00')")

    counts = seed_mock_data.seed_if_empty(conn)

    assert counts == (len(seed_mock_data.MOCK_ACTIVITIES), len(seed_mock_data.MOCK_ASSIGNMENTS))
    assert _count(db_path, "activities") == 0 and _count(db_path, "activities_assignment") == 0  # uncommitted
    conn.rollback()
    assert _count(db_path, "activities") == 0 and _count(db_path, "activities_assignment") == 0
    conn.close()


def test_seed_if_empty_commits_only_when_caller_commits(db_path):
    conn = sqlite3.connect(db_path)  # fresh file: seed_if_empty also has to create the schema

    counts = seed_mock_data.seed_if_empty(conn)
    assert counts == (len(seed_mock_data.MOCK_ACTIVITIES), len(seed_mock_data.MOCK_ASSIGNMENTS))
    assert _count(db_path, "activities") == 0  # not committed by seed_if_empty

    conn.commit()
    assert _count(db_path, "activities") == len(seed_mock_data.MOCK_ACTIVITIES)
    assert seed_mock_data.seed_if_empty(conn) == (0, 0)  # populated now, and no duplicates
    conn.close()
