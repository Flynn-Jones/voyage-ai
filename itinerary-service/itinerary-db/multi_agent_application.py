"""Atomic, durable application receipts in the database tier, never in the shared server."""
import json
import math
import re

from flask import Blueprint, current_app, jsonify, request

from database import get_connection
from validation import validate_itinerary_item

application_bp = Blueprint("multi_agent_application", __name__)
WORKFLOW = "itinerary_schedule_review"
EDITABLE = {"start_time", "end_time", "notes"}


class Conflict(ValueError):
    pass


def _minutes(value):
    return int(value[:2]) * 60 + int(value[3:])


def _record(row, trip, day):
    clean = validate_itinerary_item(row)
    item_id = row.get("itinerary_item_id")
    if type(item_id) is not int or item_id < 1 or not math.isfinite(clean["estimated_cost"]):
        raise ValueError("invalid item id or cost")
    if clean["trip_reference"] != trip or (day is not None and clean["day"] != day):
        raise ValueError("application cannot change trip/day scope")
    return {"itinerary_item_id": item_id, **clean}


def _payload(body):
    if not isinstance(body, dict) or body.get("workflow") != WORKFLOW:
        raise ValueError("application must belong to the Itinerary workflow")
    if body.get("decision") not in ("approve", "partial", "correct"):
        raise ValueError("an explicit approving human decision is required")
    trip, day = body.get("trip_reference"), body.get("day")
    if not isinstance(trip, str) or not trip.strip() or len(trip) > 120:
        raise ValueError("invalid trip_reference")
    trip = trip.strip()
    if day is not None and (type(day) is not int or day < 1):
        raise ValueError("invalid day")
    changes = body.get("changes")
    if not isinstance(changes, list) or len(changes) > 200:
        raise ValueError("changes must be a list of at most 200 approved updates")
    clean, seen = [], set()
    for item in changes:
        if not isinstance(item, dict):
            raise ValueError("invalid approved record")
        old = _record(item.get("original"), trip, day)
        new = _record(item, trip, day)
        if new["itinerary_item_id"] in seen or item.get("id") != str(old["itinerary_item_id"]):
            raise ValueError("duplicate or invalid proposal id")
        seen.add(new["itinerary_item_id"])
        if any(old[k] != new[k] for k in set(old) - EDITABLE):
            raise ValueError("only times and notes may change")
        if _minutes(new["end_time"]) - _minutes(new["start_time"]) != _minutes(old["end_time"]) - _minutes(old["start_time"]):
            raise ValueError("approved shifts must preserve duration")
        clean.append({"original": old, "updated": new})
    return {"workflow": WORKFLOW, "trip_reference": trip, "day": day, "decision": body["decision"], "changes": clean}


def _overlaps(rows):
    result = {}
    for index, left in enumerate(rows):
        for right in rows[index + 1:]:
            if left["day"] != right["day"]:
                continue
            overlap = min(_minutes(left["end_time"]), _minutes(right["end_time"])) - max(
                _minutes(left["start_time"]), _minutes(right["start_time"])
            )
            if overlap > 0:
                result[tuple(sorted((left["itinerary_item_id"], right["itinerary_item_id"])))] = overlap
    return result


def apply_once(connection, run_id, payload):
    serialized = json.dumps(payload, sort_keys=True, allow_nan=False)
    # Lock before checking the receipt and snapshot: concurrent retries serialize across processes.
    connection.execute("BEGIN IMMEDIATE")
    previous = connection.execute(
        "SELECT payload, result FROM itinerary_multi_agent_applications WHERE run_id = ?", (run_id,),
    ).fetchone()
    if previous:
        if previous["payload"] != serialized:
            raise Conflict("run already applied with different approved changes")
        return {**json.loads(previous["result"]), "replayed": True}
    rows = [dict(r) for r in connection.execute(
        "SELECT * FROM itinerary_items WHERE trip_reference = ?", (payload["trip_reference"],),
    ).fetchall()]
    if len(rows) > 200:
        raise Conflict("trip has more than 200 records; review and apply manually")
    originals = {row["itinerary_item_id"]: row for row in rows}
    updated = dict(originals)
    for change in payload["changes"]:
        old, new = change["original"], change["updated"]
        if originals.get(old["itinerary_item_id"]) != old:
            raise Conflict("itinerary changed since review; start a new run")
        updated[new["itinerary_item_id"]] = new
    before, after = _overlaps(rows), _overlaps(list(updated.values()))
    if any(duration > before.get(pair, 0) for pair, duration in after.items()):
        raise Conflict("selected/corrected changes introduce or worsen an overlap; start a new run")
    ids = []
    for change in payload["changes"]:
        row = change["updated"]
        connection.execute(
            "UPDATE itinerary_items SET start_time = ?, end_time = ?, notes = ? WHERE itinerary_item_id = ? AND trip_reference = ?",
            (row["start_time"], row["end_time"], row["notes"], row["itinerary_item_id"], payload["trip_reference"]),
        )
        ids.append(row["itinerary_item_id"])
    result = {"run_id": run_id, "updated_item_ids": ids, "replayed": False}
    connection.execute(
        "INSERT INTO itinerary_multi_agent_applications (run_id, payload, result) VALUES (?, ?, ?)",
        (run_id, serialized, json.dumps(result)),
    )
    return result


@application_bp.post("/multi-agent/applications/<run_id>")
def apply_approved(run_id):
    connection = None
    try:
        if not re.fullmatch(r"run-[A-Za-z0-9_-]{1,80}", run_id):
            raise ValueError("invalid run id")
        payload = _payload(request.get_json(silent=True))
        connection = get_connection(current_app.config["DB_FILE"])
        with connection:
            result = apply_once(connection, run_id, payload)
        return jsonify(result)
    except Conflict as exc:
        return jsonify(error=str(exc)), 409
    except ValueError as exc:
        return jsonify(error=str(exc)), 400
    finally:
        if connection is not None:
            connection.close()
