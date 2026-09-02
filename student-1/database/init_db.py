"""Creates the destination SQLite schema and seeds it deterministically.

Session 1 scope proved the database service owns its own SQLite file inside
the named volume, with the registered schema in place. Session 2 adds the
seed data and CRUD support: seeding only runs when the table is empty, so
restarting the container never duplicates rows or wipes user-created data.
"""
import json
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

# (destination_id, city, country, description, average_daily_cost,
#  recommended_trip_length, travel_style, categories)
SEED_DESTINATIONS = [
    (1, "Tokyo", "Japan", "Neon-lit metropolis mixing ultramodern tech with historic temples.",
     150.0, 6, "City break", ["food", "culture", "nightlife", "shopping"]),
    (2, "Kyoto", "Japan", "Former imperial capital known for temples, shrines and gardens.",
     130.0, 4, "Cultural", ["culture", "history", "nature"]),
    (3, "Osaka", "Japan", "Japan's kitchen, famous for street food and lively nightlife.",
     125.0, 3, "Food and nightlife", ["food", "nightlife"]),
    (4, "Seoul", "South Korea", "Fast-paced capital blending K-pop culture with ancient palaces.",
     120.0, 5, "City break", ["culture", "shopping", "nightlife"]),
    (5, "Bangkok", "Thailand", "Bustling capital known for street food, temples and river life.",
     70.0, 5, "Budget", ["food", "culture", "budget"]),
    (6, "Singapore", "Singapore", "Ultra-clean city-state famed for its skyline and hawker food.",
     180.0, 3, "City break", ["food", "shopping", "family"]),
    (7, "Sydney", "Australia", "Harbour city with iconic beaches and the famous Opera House.",
     190.0, 5, "Coastal", ["beach", "nature", "family"]),
    (8, "Melbourne", "Australia", "Laneways, coffee culture and a thriving arts scene.",
     165.0, 4, "Arts and coffee", ["food", "culture", "arts"]),
    (9, "Paris", "France", "The City of Light, home to world-class art, food and romance.",
     200.0, 5, "Romantic", ["culture", "food", "romance"]),
    (10, "Rome", "Italy", "Ancient ruins and Renaissance art layered through a living city.",
     155.0, 4, "Historical", ["history", "culture", "food"]),
]


def get_connection(db_file=None):
    path = db_file or DB_FILE
    data_dir = os.path.dirname(path)
    if data_dir:
        os.makedirs(data_dir, exist_ok=True)
    conn = sqlite3.connect(path)
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


def seed_if_empty(conn):
    """Seed the deterministic destination list only if the table is empty.

    Returns the number of rows inserted (0 if seeding was skipped), so a
    restart never duplicates seed rows and never wipes user-created data.
    """
    count = conn.execute("SELECT COUNT(*) FROM destinations").fetchone()[0]
    if count:
        print(f"destinations already contains {count} rows; skipping seed")
        return 0

    conn.executemany(
        """
        INSERT INTO destinations (
            destination_id, city, country, description, average_daily_cost,
            recommended_trip_length, travel_style, categories
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                destination_id, city, country, description, average_daily_cost,
                recommended_trip_length, travel_style, json.dumps(categories),
            )
            for (
                destination_id, city, country, description, average_daily_cost,
                recommended_trip_length, travel_style, categories,
            ) in SEED_DESTINATIONS
        ],
    )
    conn.commit()
    return len(SEED_DESTINATIONS)


def main():
    conn = get_connection()
    init_schema(conn)
    inserted = seed_if_empty(conn)
    conn.close()
    print(f"destination database initialized at {DB_FILE} ({inserted} rows seeded)")


if __name__ == "__main__":
    main()
