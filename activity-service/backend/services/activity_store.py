"""Direct SQLite writer for the activities / activities_assignment tables.

Flagged design decision (approved): activity-service/database-service is
read-only by design, and this backend must not modify it. So creates/updates/
deletes happen here via a direct connection to the same SQLite file instead
of an HTTP write endpoint on the database-service. In production the backend
and database-service containers will need to share a Docker volume for this
file (compose wiring is out of this task's scope); for local/dev both point
at the same relative path by default.
"""
import os
import sqlite3

DB_PATH = os.environ.get(
    "ACTIVITY_DB_PATH",
    os.path.join(
        os.path.dirname(__file__), "..", "..", "database-service", "activity-db.sqlite"
    ),
)

WRITABLE_FIELDS = ("activity_name", "activity_type", "activity_cost", "duration")


class ActivityNotFoundError(Exception):
    """Raised when the activity being updated/deleted doesn't exist."""


def _get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def create_activity(data):
    conn = _get_connection()
    cursor = conn.execute(
        "INSERT INTO activities (activity_name, activity_type, activity_cost, duration) "
        "VALUES (?, ?, ?, ?)",
        (
            data["activity_name"],
            data["activity_type"],
            float(data["activity_cost"]),
            data["duration"],
        ),
    )
    activity_id = cursor.lastrowid

    assignment_time = data.get("assignment_time")
    assignment_id = None
    if assignment_time:
        assignment_cursor = conn.execute(
            "INSERT INTO activities_assignment (activity_id, assignment_time) VALUES (?, ?)",
            (activity_id, assignment_time),
        )
        assignment_id = assignment_cursor.lastrowid

    conn.commit()
    conn.close()

    result = {
        "activity_id": activity_id,
        "activity_name": data["activity_name"],
        "activity_type": data["activity_type"],
        "activity_cost": float(data["activity_cost"]),
        "duration": data["duration"],
    }
    if assignment_id is not None:
        result["assignment_id"] = assignment_id
        result["assignment_time"] = assignment_time
    return result


def update_activity(activity_id, data):
    conn = _get_connection()
    existing = conn.execute(
        "SELECT * FROM activities WHERE activity_id = ?", (activity_id,)
    ).fetchone()
    if existing is None:
        conn.close()
        raise ActivityNotFoundError(activity_id)

    fields = {key: data[key] for key in WRITABLE_FIELDS if key in data}
    if fields:
        set_clause = ", ".join(f"{key} = ?" for key in fields)
        conn.execute(
            f"UPDATE activities SET {set_clause} WHERE activity_id = ?",
            (*fields.values(), activity_id),
        )

    if "assignment_time" in data:
        existing_assignment = conn.execute(
            "SELECT * FROM activities_assignment WHERE activity_id = ?", (activity_id,)
        ).fetchone()
        if existing_assignment:
            conn.execute(
                "UPDATE activities_assignment SET assignment_time = ? WHERE activity_id = ?",
                (data["assignment_time"], activity_id),
            )
        else:
            conn.execute(
                "INSERT INTO activities_assignment (activity_id, assignment_time) VALUES (?, ?)",
                (activity_id, data["assignment_time"]),
            )

    conn.commit()
    updated = conn.execute(
        "SELECT * FROM activities WHERE activity_id = ?", (activity_id,)
    ).fetchone()
    conn.close()
    return dict(updated)


def delete_activity(activity_id):
    conn = _get_connection()
    existing = conn.execute(
        "SELECT * FROM activities WHERE activity_id = ?", (activity_id,)
    ).fetchone()
    if existing is None:
        conn.close()
        raise ActivityNotFoundError(activity_id)

    conn.execute("DELETE FROM activities_assignment WHERE activity_id = ?", (activity_id,))
    conn.execute("DELETE FROM activities WHERE activity_id = ?", (activity_id,))
    conn.commit()
    conn.close()
