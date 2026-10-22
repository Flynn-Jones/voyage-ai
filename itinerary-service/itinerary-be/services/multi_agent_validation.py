"""Untrusted run/proposal validation at the owning backend boundary."""
import re

from validation import validate_itinerary_item

WORKFLOW = "itinerary_schedule_review"
DECISIONS = {"approve": "approved", "partial": "partially_accepted", "correct": "corrected", "reject": "rejected"}
EDITABLE = {"start_time", "end_time", "notes"}
RUN_ID = re.compile(r"^run-[A-Za-z0-9_-]{1,80}$")


def scope(body):
    if not isinstance(body, dict):
        raise ValueError("body must be a JSON object")
    trip, day = body.get("trip_reference"), body.get("day")
    if not isinstance(trip, str) or not trip.strip() or len(trip) > 120:
        raise ValueError("trip_reference must be a non-empty string of at most 120 characters")
    if day is not None and (type(day) is not int or day < 1):
        raise ValueError("day must be a positive integer")
    return {"trip_reference": trip.strip(), "day": day}


def record(value, requested):
    result = validate_itinerary_item(value)
    item_id = value.get("itinerary_item_id")
    if type(item_id) is not int or item_id < 1:
        raise ValueError("invalid itinerary item id")
    if result["trip_reference"] != requested["trip_reference"] or (
        requested["day"] is not None and result["day"] != requested["day"]
    ):
        raise ValueError("record is outside the trip/day scope")
    return {"itinerary_item_id": item_id, **result}


def duration(row):
    def minutes(value):
        return int(value[:2]) * 60 + int(value[3:])
    return minutes(row["end_time"]) - minutes(row["start_time"])


def proposal(value, requested):
    if not isinstance(value, dict):
        raise ValueError("proposal must be an object")
    original = record(value.get("original"), requested)
    updated = record(value, requested)
    if value.get("id") != str(original["itinerary_item_id"]):
        raise ValueError("proposal id does not match its evidence")
    for key in set(original) - EDITABLE:
        if updated[key] != original[key]:
            raise ValueError(f"proposal cannot change {key}")
    if duration(updated) != duration(original):
        raise ValueError("time shifts must preserve duration")
    if not isinstance(value.get("reason"), str):
        raise ValueError("proposal needs an evidence-based reason")
    return {**updated, "id": value["id"], "original": original, "reason": value["reason"]}


def work_items(work, requested):
    if not isinstance(work, dict) or work.get("scope") != requested or not isinstance(work.get("summary"), str):
        raise ValueError("invalid Worker scope or summary")
    items, evidence = work.get("items"), work.get("evidence")
    if not isinstance(items, list) or len(items) > 200 or not isinstance(evidence, list) or len(evidence) != 1:
        raise ValueError("invalid Worker items/evidence")
    source = evidence[0]
    if not isinstance(source, dict) or source.get("scope") != requested or source.get("source") != "MCP:get_itinerary":
        raise ValueError("invalid evidence source/scope")
    rows = source.get("records")
    if not isinstance(rows, list) or len(rows) > 200:
        raise ValueError("invalid evidence records")
    normalized = [record(row, requested) for row in rows]
    by_id = {r["itinerary_item_id"]: r for r in normalized}
    clean = [proposal(item, requested) for item in items]
    if len(by_id) != len(rows) or len({i["id"] for i in clean}) != len(clean):
        raise ValueError("duplicate evidence/proposal ids")
    if any(by_id.get(i["itinerary_item_id"]) != i["original"] for i in clean):
        raise ValueError("proposal is unsupported by the supplied evidence")
    return clean


def decision_items(items, body, requested):
    if not isinstance(body, dict) or not isinstance(body.get("decision"), str) or body["decision"] not in DECISIONS:
        raise ValueError("decision must be approve, partial, correct or reject")
    order = [i["id"] for i in items]
    lists = {}
    for key in ("approved_ids", "rejected_ids"):
        values = body.get(key, [])
        if not isinstance(values, list) or any(type(v) not in (str, int) for v in values):
            raise ValueError(f"{key} must be a list of item ids")
        lists[key] = {str(v) for v in values}
    edits = body.get("edits", {})
    if not isinstance(edits, dict):
        raise ValueError("edits must be an object")
    if (lists["approved_ids"] | lists["rejected_ids"] | set(edits)) - set(order):
        raise ValueError("unknown proposal ids")
    if lists["approved_ids"] & lists["rejected_ids"] or set(edits) & lists["rejected_ids"]:
        raise ValueError("a proposal cannot be both accepted/edited and rejected")
    for changes in edits.values():
        if not isinstance(changes, dict) or not changes or set(changes) - EDITABLE:
            raise ValueError("corrections may only edit start_time, end_time and notes")
    decision = body["decision"]
    if decision == "approve" and lists["rejected_ids"]:
        raise ValueError("use partial to reject some proposals")
    if decision == "partial" and not (lists["approved_ids"] | set(edits)):
        raise ValueError("partial requires at least one selected proposal")
    if decision == "correct" and not edits:
        raise ValueError("correct requires at least one correction")
    keep = (set(order) if decision == "approve" else
            lists["approved_ids"] | set(edits) if decision == "partial" else
            set(order) - lists["rejected_ids"] if decision == "correct" else set())
    return [proposal({**item, **edits.get(item["id"], {})}, requested) for item in items if item["id"] in keep]
