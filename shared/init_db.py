"""Creates the shared SQLite schema and (re)loads seed data from expected_shared_data.json."""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "database-service"))

from db import DB_PATH, get_connection, init_schema  # noqa: E402

SEED_FILE = os.path.join(os.path.dirname(__file__), "expected_shared_data.json")


def load_seed_data():
    with open(SEED_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def seed_database(conn, data):
    conn.execute("DELETE FROM trips")
    conn.execute("DELETE FROM users")

    for user in data["users"]:
        conn.execute(
            "INSERT INTO users (id, name, preferences, travel_style) VALUES (?, ?, ?, ?)",
            (user["id"], user["name"], user["preferences"], user["travel_style"]),
        )

    for trip in data["trips"]:
        conn.execute(
            "INSERT INTO trips (id, user_id, trip_name, start_date, end_date, status) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                trip["id"],
                trip["user_id"],
                trip["trip_name"],
                trip["start_date"],
                trip["end_date"],
                trip["status"],
            ),
        )

    conn.commit()


def main():
    conn = get_connection()
    init_schema(conn)
    data = load_seed_data()
    seed_database(conn, data)
    conn.close()
    print(f"Initialized {DB_PATH} with {len(data['users'])} users and {len(data['trips'])} trips.")


if __name__ == "__main__":
    main()
