"""Creates the destination SQLite schema (empty — no seed data yet).

Session 1 scope: prove the database service owns its own SQLite file inside
the named volume, with the registered schema in place. Seed rows and CRUD
routes are Session 2.
"""
import os
import sqlite3

DB_FILE = os.environ.get(
    "DB_FILE",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "destination-db.sqlite"),
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS destinations (
    destination_id INTEGER PRIMARY KEY AUTOINCREMENT,
    city TEXT NOT NULL,
    country TEXT NOT NULL,
    description TEXT,
    average_daily_cost REAL,
    recommended_trip_length INTEGER,
    travel_style TEXT,
    categories TEXT
);
"""


def get_connection():
    os.makedirs(os.path.dirname(DB_FILE), exist_ok=True)
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_schema(conn=None):
    close_after = conn is None
    conn = conn or get_connection()
    conn.executescript(SCHEMA)
    conn.commit()
    if close_after:
        conn.close()


if __name__ == "__main__":
    init_schema()
    print(f"destination database initialized at {DB_FILE}")
