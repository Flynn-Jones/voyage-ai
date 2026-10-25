"""Seed the activities table with 15 unique mock activities, plus scheduled
times for most of them, for demos and manual testing. Seeded automatically on
startup (app.create_app -> seed_if_empty) only when the activities table is
empty, so a fresh database gets the demonstration records while a populated
one is never touched. Note: a database whose activities were deliberately all
deleted is therefore reseeded on the next restart.
Idempotent: an activity whose name is already present, or an assignment
already scheduled for that activity at that time, is skipped, so re-running
never creates duplicates.

Run inside the container:  docker exec <db-container> python seed_mock_data.py
Run locally:               python seed_mock_data.py
"""
from init_db import ensure_schema, get_connection, init_schema

# (activity_name, activity_type, activity_cost, duration). Durations use the
# free-text forms ai_client._parse_duration_hours understands.
MOCK_ACTIVITIES = [
    ("Harbour Bridge Climb", "Adventure", 189.0, "3.5 hours"),
    ("Blue Mountains Day Tour", "Nature", 145.0, "full day"),
    ("Opera House Backstage Tour", "Culture", 42.0, "1 hour"),
    ("Bondi to Coogee Coastal Walk", "Nature", 0.0, "2 hours"),
    ("Sunrise Hot Air Balloon Flight", "Adventure", 320.0, "4 hours"),
    ("Chinatown Street Food Crawl", "Food", 65.0, "3 hours"),
    ("Hunter Valley Wine Tasting", "Food", 110.0, "half-day"),
    ("Taronga Zoo Visit", "Family", 51.0, "5 hours"),
    ("Manly Beach Surf Lesson", "Water Sports", 85.0, "2 hours"),
    ("Aboriginal Heritage Walk", "Culture", 38.0, "90 minutes"),
    ("Harbour Sunset Kayak", "Water Sports", 95.0, "2.5 hours"),
    ("Art Gallery of NSW Guided Tour", "Sightseeing", 0.0, "75 minutes"),
    ("Rooftop Cocktail Masterclass", "Nightlife", 79.0, "2 hours"),
    ("Day Spa and Thermal Pools", "Wellness", 140.0, "3 hours"),
    ("Whale Watching Cruise", "Sightseeing", 99.0, "3 hours"),
]


# (activity_name, assignment_time). Keyed by name rather than id so it holds
# however the ids were assigned; times use the frontend datepicker's
# YYYY-MM-DDTHH:MM format. Some activities have two slots, some none.
MOCK_ASSIGNMENTS = [
    ("Harbour Bridge Climb", "2026-10-03T09:00"),
    ("Harbour Bridge Climb", "2026-10-10T16:30"),
    ("Blue Mountains Day Tour", "2026-10-04T07:30"),
    ("Opera House Backstage Tour", "2026-10-05T11:00"),
    ("Bondi to Coogee Coastal Walk", "2026-10-06T08:00"),
    ("Bondi to Coogee Coastal Walk", "2026-10-12T17:00"),
    ("Sunrise Hot Air Balloon Flight", "2026-10-07T05:15"),
    ("Chinatown Street Food Crawl", "2026-10-07T18:30"),
    ("Hunter Valley Wine Tasting", "2026-10-08T10:00"),
    ("Manly Beach Surf Lesson", "2026-10-09T09:30"),
    ("Aboriginal Heritage Walk", "2026-10-11T14:00"),
    ("Rooftop Cocktail Masterclass", "2026-10-11T19:00"),
    ("Whale Watching Cruise", "2026-10-13T10:30"),
]


def _insert_missing(conn):
    """Insert any MOCK_ACTIVITIES / MOCK_ASSIGNMENTS not already present on
    `conn`. Never commits or closes; rows are read by position so any
    row_factory works. Returns (activities_inserted, assignments_inserted)."""
    existing = {row[0] for row in conn.execute("SELECT activity_name FROM activities")}
    new_activities = [a for a in MOCK_ACTIVITIES if a[0] not in existing]
    conn.executemany(
        "INSERT INTO activities (activity_name, activity_type, activity_cost, duration) VALUES (?, ?, ?, ?)",
        new_activities,
    )

    ids = {name: activity_id for activity_id, name in conn.execute("SELECT activity_id, activity_name FROM activities")}
    scheduled = {tuple(row) for row in conn.execute("SELECT activity_id, assignment_time FROM activities_assignment")}
    new_assignments = [
        (ids[name], time) for name, time in MOCK_ASSIGNMENTS if (ids[name], time) not in scheduled
    ]
    conn.executemany(
        "INSERT INTO activities_assignment (activity_id, assignment_time) VALUES (?, ?)",
        new_assignments,
    )
    return len(new_activities), len(new_assignments)


def seed(conn=None):
    """Insert any MOCK_ACTIVITIES not already present (matched by name), then
    any MOCK_ASSIGNMENTS not already present (matched by activity + time), and
    commit. Returns (activities_inserted, assignments_inserted)."""
    close_after = conn is None
    conn = conn or get_connection()
    init_schema(conn)
    counts = _insert_missing(conn)
    conn.commit()
    if close_after:
        conn.close()
    return counts


def seed_if_empty(conn=None):
    """Seed only when the activities table has no rows; a populated database
    is left untouched. Returns (activities_inserted, assignments_inserted), or
    (0, 0) when nothing was seeded.

    With a caller-supplied `conn` this neither commits nor closes it: any
    schema/demo rows join the caller's open transaction, so the caller decides
    whether to commit or roll back. Without one, it opens its own connection,
    commits and closes it."""
    close_after = conn is None
    conn = conn or get_connection()
    try:
        ensure_schema(conn)
        if conn.execute("SELECT 1 FROM activities LIMIT 1").fetchone() is not None:
            return 0, 0
        counts = _insert_missing(conn)
        if close_after:
            conn.commit()
        return counts
    finally:
        if close_after:
            conn.close()


if __name__ == "__main__":
    activities, assignments = seed()
    print(
        f"Inserted {activities} mock activities ({len(MOCK_ACTIVITIES) - activities} already present), "
        f"{assignments} mock assignments ({len(MOCK_ASSIGNMENTS) - assignments} already present)."
    )
