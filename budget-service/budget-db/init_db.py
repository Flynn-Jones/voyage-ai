"""Creates the budget SQLite schema and (re)loads the seed data."""
import os
import sqlite3

DB_FILE = os.environ.get(
    "DB_FILE",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "budget-db.sqlite"),
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS expenses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trip_reference TEXT NOT NULL,
    expense TEXT NOT NULL,
    category TEXT NOT NULL,
    estimated_cost REAL NOT NULL,
    actual_cost REAL,
    destination_id INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'Planned',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""

SEED_TIMESTAMP = "2026-08-01T00:00:00Z"

SEED_EXPENSES = [
    ("TRIP-1001", "Flight to Paris", "Transport", 450.00, 480.00, 1, "Paid"),
    ("TRIP-1001", "Hotel Le Marais - 4 nights", "Accommodation", 620.00, 620.00, 1, "Paid"),
    ("TRIP-1001", "Museum passes", "Activities", 90.00, None, 1, "Planned"),
    ("TRIP-1002", "Train to Rome", "Transport", 120.00, 115.00, 2, "Paid"),
    ("TRIP-1002", "Airbnb - Trastevere", "Accommodation", 380.00, None, 2, "Planned"),
    ("TRIP-1002", "Cooking class", "Activities", 75.00, None, 2, "Planned"),
    ("TRIP-1003", "Flight to Tokyo", "Transport", 900.00, 940.00, 3, "Paid"),
    ("TRIP-1003", "Shinjuku hotel - 5 nights", "Accommodation", 700.00, None, 3, "Planned"),
    ("TRIP-1003", "JR rail pass", "Transport", 210.00, None, 3, "Cancelled"),
    ("TRIP-1004", "Car rental - Gold Coast", "Transport", 180.00, 175.00, 4, "Paid"),
    ("TRIP-1004", "Theme park tickets", "Activities", 260.00, None, 4, "Planned"),
    ("TRIP-1005", "Ferry to island", "Transport", 60.00, None, 5, "Cancelled"),
]


def get_connection():
    data_dir = os.path.dirname(DB_FILE)
    if data_dir:
        os.makedirs(data_dir, exist_ok=True)
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn


def init_schema(conn):
    conn.executescript(SCHEMA)
    conn.commit()


def seed_database(conn):
    conn.execute("DELETE FROM expenses")
    conn.executemany(
        """
        INSERT INTO expenses (
            trip_reference, expense, category, estimated_cost, actual_cost,
            destination_id, status, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                trip_reference, expense, category, estimated_cost, actual_cost,
                destination_id, status, SEED_TIMESTAMP, SEED_TIMESTAMP,
            )
            for (
                trip_reference, expense, category, estimated_cost, actual_cost,
                destination_id, status,
            ) in SEED_EXPENSES
        ],
    )
    conn.commit()


def main():
    conn = get_connection()
    init_schema(conn)
    seed_database(conn)
    conn.close()
    print(f"Initialized {DB_FILE} with {len(SEED_EXPENSES)} expenses.")


if __name__ == "__main__":
    main()
