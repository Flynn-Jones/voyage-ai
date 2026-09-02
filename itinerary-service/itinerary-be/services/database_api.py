"""HTTP boundary for the itinerary database service.

CRUD methods will be introduced in Stage 2. Keeping database access behind this
module prevents the backend from opening SQLite directly.
"""
import os


def get_database_service_url():
    return os.environ.get("DATABASE_SERVICE_URL", "http://localhost:6005").rstrip("/")
