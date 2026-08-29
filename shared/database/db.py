"""SQLite connection and schema for the shared users/trips database."""
import os
import sqlite3

DB_PATH = os.path.join(os.path.dirname(__file__), "shared.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    preferences TEXT,
    travel_style TEXT
);

CREATE TABLE IF NOT EXISTS trips (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    trip_name TEXT NOT NULL,
    start_date TEXT,
    end_date TEXT,
    status TEXT,
    FOREIGN KEY (user_id) REFERENCES users (id)
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
