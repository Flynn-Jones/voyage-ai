import os
import sqlite3


DB_FILE = os.environ.get(
    "DB_FILE",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "itinerary-db.sqlite"),
)


def get_connection():
    data_directory = os.path.dirname(DB_FILE)
    if data_directory:
        os.makedirs(data_directory, exist_ok=True)
    return sqlite3.connect(DB_FILE)


def initialize_database():
    """Create the service-owned SQLite file without defining Stage 2 tables."""
    connection = get_connection()
    connection.close()
