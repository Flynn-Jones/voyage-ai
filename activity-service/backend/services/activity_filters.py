"""Search, filter and sort for GET /api/view_activities query parameters.

Each activity gets its assignment_time (first assignment, the same one the
detail page shows) so the list can filter and sort by date."""
from datetime import date

from services.ai_client import _parse_duration_hours

FILTER_PARAMS = (
    "q", "type", "min_cost", "max_cost", "min_duration", "max_duration",
    "date_from", "date_to", "sort", "include_dates",
)


def _cost(activity):
    return activity.get("activity_cost")


def _hours(activity):
    return _parse_duration_hours(activity.get("duration"))


def _day(activity):
    return (activity.get("assignment_time") or "")[:10]


# Missing values sort last for every key.
SORTS = {
    "name": lambda a: (a.get("activity_name") or "").lower(),
    "cost_asc": lambda a: (_cost(a) is None, _cost(a) or 0),
    "cost_desc": lambda a: (_cost(a) is None, -(_cost(a) or 0)),
    "duration_asc": lambda a: (_hours(a) is None, _hours(a) or 0),
    "date_asc": lambda a: (not a.get("assignment_time"), a.get("assignment_time") or ""),
}


def wants_filtering(args):
    return any(name in args for name in FILTER_PARAMS)


def _number(args, name):
    raw = (args.get(name) or "").strip()
    if not raw:
        return None
    try:
        value = float(raw)
    except ValueError:
        raise ValueError(f"{name} must be a number") from None
    if value < 0:
        raise ValueError(f"{name} must not be negative")
    return value


def _date(args, name):
    raw = (args.get(name) or "").strip()
    if not raw:
        return None
    try:
        date.fromisoformat(raw)
    except ValueError:
        raise ValueError(f"{name} must be a date in YYYY-MM-DD format") from None
    return raw


def parse_filters(args):
    """Raises ValueError with a user-facing message on a bad parameter."""
    sort = (args.get("sort") or "").strip()
    if sort and sort not in SORTS:
        raise ValueError(f"sort must be one of: {', '.join(SORTS)}")
    return {
        "q": (args.get("q") or "").strip().lower(),
        "type": (args.get("type") or "").strip().lower(),
        "min_cost": _number(args, "min_cost"),
        "max_cost": _number(args, "max_cost"),
        "min_duration": _number(args, "min_duration"),
        "max_duration": _number(args, "max_duration"),
        "date_from": _date(args, "date_from"),
        "date_to": _date(args, "date_to"),
        "sort": sort,
    }


def _matches(activity, filters):
    if filters["q"]:
        haystack = f"{activity.get('activity_name') or ''} {activity.get('activity_type') or ''}".lower()
        if filters["q"] not in haystack:
            return False

    if filters["type"] and (activity.get("activity_type") or "").strip().lower() != filters["type"]:
        return False

    cost = _cost(activity)
    if filters["min_cost"] is not None and (cost is None or cost < filters["min_cost"]):
        return False
    if filters["max_cost"] is not None and (cost is None or cost > filters["max_cost"]):
        return False

    # An unparseable duration fails open, as in ai_client.filter_candidates.
    hours = _hours(activity)
    if hours is not None:
        if filters["min_duration"] is not None and hours < filters["min_duration"]:
            return False
        if filters["max_duration"] is not None and hours > filters["max_duration"]:
            return False

    day = _day(activity)
    if filters["date_from"] and (not day or day < filters["date_from"]):
        return False
    if filters["date_to"] and (not day or day > filters["date_to"]):
        return False

    return True


def apply_filters(activities, assignments, filters):
    first_assignment = {}
    for assignment in assignments:
        first_assignment.setdefault(assignment.get("activity_id"), assignment.get("assignment_time"))

    results = [
        {**activity, "assignment_time": first_assignment.get(activity.get("activity_id"))}
        for activity in activities
    ]
    results = [activity for activity in results if _matches(activity, filters)]
    if filters["sort"]:
        results.sort(key=SORTS[filters["sort"]])
    return results
