"""Backend validation for complete itinerary item representations."""
import math
import re


REQUIRED_FIELDS = (
    "trip_reference",
    "day",
    "start_time",
    "end_time",
    "activity_id",
    "destination_id",
    "estimated_cost",
)
TIME_PATTERN = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")


class ValidationError(ValueError):
    pass


def _positive_integer(value, field):
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValidationError(f"{field} must be a positive integer")
    return value


def validate_itinerary_item(data):
    if not isinstance(data, dict):
        raise ValidationError("request body must be a JSON object")

    missing = [field for field in REQUIRED_FIELDS if field not in data]
    if missing:
        raise ValidationError(f"missing required fields: {', '.join(missing)}")

    trip_reference = data["trip_reference"]
    if not isinstance(trip_reference, str) or not trip_reference.strip():
        raise ValidationError("trip_reference must be a non-empty string")

    day = _positive_integer(data["day"], "day")
    activity_id = _positive_integer(data["activity_id"], "activity_id")
    destination_id = _positive_integer(data["destination_id"], "destination_id")

    start_time = data["start_time"]
    end_time = data["end_time"]
    if not isinstance(start_time, str) or not TIME_PATTERN.fullmatch(start_time):
        raise ValidationError("start_time must use 24-hour HH:MM format")
    if not isinstance(end_time, str) or not TIME_PATTERN.fullmatch(end_time):
        raise ValidationError("end_time must use 24-hour HH:MM format")
    if end_time <= start_time:
        raise ValidationError("end_time must be later than start_time")

    estimated_cost = data["estimated_cost"]
    if isinstance(estimated_cost, bool) or not isinstance(estimated_cost, (int, float)):
        raise ValidationError("estimated_cost must be numeric")
    if not math.isfinite(estimated_cost) or estimated_cost < 0:
        raise ValidationError("estimated_cost must be a finite number of at least 0")

    notes = data.get("notes")
    if notes is not None and not isinstance(notes, str):
        raise ValidationError("notes must be a string or null")

    return {
        "trip_reference": trip_reference.strip(),
        "day": day,
        "start_time": start_time,
        "end_time": end_time,
        "activity_id": activity_id,
        "destination_id": destination_id,
        "estimated_cost": float(estimated_cost),
        "notes": notes,
    }
