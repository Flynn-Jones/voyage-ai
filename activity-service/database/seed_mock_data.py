"""Seed the activities table with 15 unique mock activities, plus scheduled
times for most of them, for demos and manual testing. Not run on startup — a
fresh database stays empty (the database-service tests rely on that).
Idempotent: an activity whose name is already present, or an assignment
already scheduled for that activity at that time, is skipped, so re-running
never creates duplicates.

Run inside the container:  docker exec <db-container> python seed_mock_data.py
Run locally:               python seed_mock_data.py
"""
from init_db import get_connection, init_schema

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


def seed(conn=None):
    """Insert any MOCK_ACTIVITIES not already present (matched by name), then
    any MOCK_ASSIGNMENTS not already present (matched by activity + time).
    Returns (activities_inserted, assignments_inserted)."""
    close_after = conn is None
    conn = conn or get_connection()
    init_schema(conn)

    existing = {row["activity_name"] for row in conn.execute("SELECT activity_name FROM activities")}
    new_activities = [a for a in MOCK_ACTIVITIES if a[0] not in existing]
    conn.executemany(
        "INSERT INTO activities (activity_name, activity_type, activity_cost, duration) VALUES (?, ?, ?, ?)",
        new_activities,
    )

    ids = {row["activity_name"]: row["activity_id"] for row in conn.execute("SELECT activity_id, activity_name FROM activities")}
    scheduled = {
        (row["activity_id"], row["assignment_time"])
        for row in conn.execute("SELECT activity_id, assignment_time FROM activities_assignment")
    }
    new_assignments = [
        (ids[name], time) for name, time in MOCK_ASSIGNMENTS if (ids[name], time) not in scheduled
    ]
    conn.executemany(
        "INSERT INTO activities_assignment (activity_id, assignment_time) VALUES (?, ?)",
        new_assignments,
    )

    conn.commit()
    if close_after:
        conn.close()
    return len(new_activities), len(new_assignments)


if __name__ == "__main__":
    activities, assignments = seed()
    print(
        f"Inserted {activities} mock activities ({len(MOCK_ACTIVITIES) - activities} already present), "
        f"{assignments} mock assignments ({len(MOCK_ASSIGNMENTS) - assignments} already present)."
    )
