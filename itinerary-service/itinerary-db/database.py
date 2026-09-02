import os
import sqlite3


DEFAULT_DB_FILE = os.environ.get(
    "DB_FILE",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "itinerary-db.sqlite"),
)


def get_connection(db_file=None):
    database_path = db_file or DEFAULT_DB_FILE
    data_directory = os.path.dirname(database_path)
    if data_directory:
        os.makedirs(data_directory, exist_ok=True)
    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    return connection
