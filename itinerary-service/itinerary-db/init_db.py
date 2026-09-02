"""Create the itinerary schema and seed it only when the table is empty."""
from database import get_connection


SCHEMA = """
CREATE TABLE IF NOT EXISTS itinerary_items (
    itinerary_item_id INTEGER PRIMARY KEY AUTOINCREMENT,
    trip_reference TEXT NOT NULL,
    day INTEGER NOT NULL,
    start_time TEXT NOT NULL,
    end_time TEXT NOT NULL,
    activity_id INTEGER NOT NULL,
    destination_id INTEGER NOT NULL,
    estimated_cost REAL NOT NULL,
    notes TEXT
);

CREATE INDEX IF NOT EXISTS idx_itinerary_items_trip_reference
    ON itinerary_items (trip_reference);
CREATE INDEX IF NOT EXISTS idx_itinerary_items_day
    ON itinerary_items (day);
CREATE INDEX IF NOT EXISTS idx_itinerary_items_activity_id
    ON itinerary_items (activity_id);
CREATE INDEX IF NOT EXISTS idx_itinerary_items_destination_id
    ON itinerary_items (destination_id);
"""


SEED_ITEMS = [
    ("TRIP-1001", 1, "09:00", "10:30", 101, 1, 24.00, "Guided historic centre walk"),
    ("TRIP-1001", 1, "11:00", "12:30", 102, 1, 18.50, "Visit the local art museum"),
    ("TRIP-1001", 1, "14:00", "15:30", 103, 1, 32.00, "Riverside food tour"),
    ("TRIP-1001", 2, "08:30", "10:00", 104, 1, 12.00, "Breakfast market visit"),
    ("TRIP-1001", 2, "10:30", "13:00", 105, 1, 45.00, "Architecture and gardens tour"),
    ("TRIP-1001", 2, "18:00", "20:00", 106, 1, 68.00, "Evening cooking class"),
    ("TRIP-1002", 1, "09:30", "11:30", 201, 2, 20.00, "Ancient landmarks walking tour"),
    ("TRIP-1002", 1, "13:00", "15:00", 202, 2, 27.50, "Gallery and cultural district"),
    ("TRIP-1002", 2, "10:00", "12:00", 203, 2, 35.00, "Regional food tasting"),
    ("TRIP-1003", 1, "08:00", "09:30", 301, 3, 15.00, "Temple visit before peak hours"),
    ("TRIP-1003", 1, "11:00", "13:30", 302, 3, 55.00, "Technology district tour"),
    ("TRIP-1003", 2, "17:00", "19:00", 303, 3, 40.00, "Sunset observation deck"),
]


def initialize_database(db_file=None):
    connection = get_connection(db_file)
    try:
        connection.executescript(SCHEMA)
        count = connection.execute("SELECT COUNT(*) FROM itinerary_items").fetchone()[0]
        if count == 0:
            connection.executemany(
                """
                INSERT INTO itinerary_items (
                    trip_reference, day, start_time, end_time, activity_id,
                    destination_id, estimated_cost, notes
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                SEED_ITEMS,
            )
        connection.commit()
    finally:
        connection.close()
