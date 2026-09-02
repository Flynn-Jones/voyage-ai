"""Deterministic observations used to ground the AI itinerary review."""
from datetime import datetime
from math import isfinite


SHORT_GAP_MINUTES = 30
LONG_DAY_MINUTES = 8 * 60
SUBSTANTIAL_BREAK_MINUTES = 60


def _minutes(value):
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.strptime(value, "%H:%M")
    except ValueError:
        return None
    return parsed.hour * 60 + parsed.minute


def analyze_schedule(items):
    """Return facts about a day's schedule without making AI judgments."""
    valid_intervals = []
    invalid_schedule_items = []
    invalid_cost_items = []
    total_cost = 0.0

    for item in items:
        item_id = item.get("itinerary_item_id")
        start = _minutes(item.get("start_time"))
        end = _minutes(item.get("end_time"))
        issues = []
        if start is None:
            issues.append("invalid or missing start_time")
        if end is None:
            issues.append("invalid or missing end_time")
        if start is not None and end is not None and end <= start:
            issues.append("end_time is not later than start_time")
        if issues:
            invalid_schedule_items.append({"itinerary_item_id": item_id, "issues": issues})
        else:
            valid_intervals.append({"item": item, "start": start, "end": end})

        cost = item.get("estimated_cost")
        if isinstance(cost, bool) or not isinstance(cost, (int, float)) or not isfinite(cost) or cost < 0:
            invalid_cost_items.append({"itinerary_item_id": item_id, "value": cost})
        else:
            total_cost += float(cost)

    valid_intervals.sort(key=lambda interval: (interval["start"], interval["end"]))
    overlaps = []
    for index, first in enumerate(valid_intervals):
        for second in valid_intervals[index + 1 :]:
            if second["start"] >= first["end"]:
                break
            overlaps.append({
                "first_item_id": first["item"].get("itinerary_item_id"),
                "second_item_id": second["item"].get("itinerary_item_id"),
                "overlap_minutes": min(first["end"], second["end"]) - second["start"],
            })

    gaps = []
    for previous, following in zip(valid_intervals, valid_intervals[1:]):
        gap = following["start"] - previous["end"]
        if gap >= 0:
            gaps.append({
                "previous_item_id": previous["item"].get("itinerary_item_id"),
                "next_item_id": following["item"].get("itinerary_item_id"),
                "gap_minutes": gap,
            })

    earliest = valid_intervals[0]["start"] if valid_intervals else None
    latest = max((entry["end"] for entry in valid_intervals), default=None)
    day_span = latest - earliest if earliest is not None else 0
    total_duration = sum(entry["end"] - entry["start"] for entry in valid_intervals)
    short_gaps = [gap for gap in gaps if gap["gap_minutes"] < SHORT_GAP_MINUTES]
    longest_gap = max((gap["gap_minutes"] for gap in gaps), default=0)

    return {
        "item_count": len(items),
        "valid_schedule_item_count": len(valid_intervals),
        "earliest_start": valid_intervals[0]["item"]["start_time"] if valid_intervals else None,
        "latest_end": f"{latest // 60:02d}:{latest % 60:02d}" if latest is not None else None,
        "day_span_minutes": day_span,
        "total_scheduled_minutes": total_duration,
        "total_estimated_cost": round(total_cost, 2),
        "overlaps": overlaps,
        "gaps": gaps,
        "short_gaps": short_gaps,
        "long_continuous_day": bool(valid_intervals and day_span >= LONG_DAY_MINUTES and longest_gap < SUBSTANTIAL_BREAK_MINUTES),
        "invalid_schedule_items": invalid_schedule_items,
        "invalid_cost_items": invalid_cost_items,
    }
