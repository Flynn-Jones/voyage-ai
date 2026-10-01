"""Shapes shared-MCP tool results into budget-domain response contracts.

The accommodation tool returns Student 2's full reference records. Budget only
cares about what turns a stay into an expense: its name, its nightly rate, and
the destination the expense belongs to. Each stay therefore carries a
`suggested_expense` block the UI can drop straight into the create-expense form.
"""

ACCOMMODATION_CATEGORY = "Accommodation"
DEFAULT_STATUS = "Planned"


def _suggested_expense(record):
    return {
        "expense": record.get("name"),
        "category": ACCOMMODATION_CATEGORY,
        "estimated_cost": record.get("price_per_night"),
        "destination_id": record.get("destination_id"),
        "status": DEFAULT_STATUS,
    }


def format_accommodation_rates(result):
    """Turn a get_accommodation_by_destination result into expense-ready stays."""
    if not isinstance(result, dict) or "error" in result:
        return result

    records = result.get("accommodations") or []
    stays = [
        {
            "name": record.get("name"),
            "type": record.get("type"),
            "destination_city": record.get("destination_city"),
            "destination_id": record.get("destination_id"),
            "price_per_night": record.get("price_per_night"),
            "rating": record.get("rating"),
            "location": record.get("location"),
            "suggested_expense": _suggested_expense(record),
        }
        for record in records
        if isinstance(record, dict)
    ]

    # Cheapest first — the point of the lookup is choosing a rate to budget for.
    stays.sort(key=lambda s: s["price_per_night"] if isinstance(s["price_per_night"], (int, float)) else float("inf"))

    return {
        "destination": result.get("destination"),
        "matched_by": result.get("matched_by"),
        "count": len(stays),
        "stays": stays,
    }
