"""SQLite connection and schema for the activity-service database."""
import os
import sqlite3

DB_PATH = os.environ.get(
    "ACTIVITY_DB_PATH", os.path.join(os.path.dirname(__file__), "activity-db.sqlite")
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS activities (
    activity_id INTEGER PRIMARY KEY AUTOINCREMENT,
    activity_name TEXT NOT NULL,
    activity_type TEXT,
    activity_cost REAL,
    duration TEXT
);

CREATE TABLE IF NOT EXISTS activities_assignment (
    assignment_id INTEGER PRIMARY KEY AUTOINCREMENT,
    activity_id INTEGER NOT NULL,
    assignment_time TEXT,
    FOREIGN KEY (activity_id) REFERENCES activities (activity_id)
);
"""


def get_connection():
    conn = sqlite3.connect(DB_PATH)
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
