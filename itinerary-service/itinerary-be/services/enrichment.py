"""Best-effort cross-service enrichment for stored itinerary records."""
from services import activity_api, destination_api


def enrich_items(items):
    destination_cache = {}
    activity_cache = {}
    destination_unavailable = False
    activity_unavailable = False
    enriched = []

    for stored_item in items:
        item = dict(stored_item)
        destination_id = item.get("destination_id")
        activity_id = item.get("activity_id")

        if destination_id not in destination_cache:
            if destination_unavailable:
                destination_cache[destination_id] = None
            else:
                try:
                    destination_cache[destination_id] = destination_api.get_destination(destination_id)
                except destination_api.DestinationNotFoundError:
                    destination_cache[destination_id] = None
                except destination_api.DestinationServiceError:
                    destination_cache[destination_id] = None
                    destination_unavailable = True
        if activity_id not in activity_cache:
            if activity_unavailable:
                activity_cache[activity_id] = None
            else:
                try:
                    activity_cache[activity_id] = activity_api.get_activity(activity_id)
                except activity_api.ActivityNotFoundError:
                    activity_cache[activity_id] = None
                except activity_api.ActivityServiceError:
                    activity_cache[activity_id] = None
                    activity_unavailable = True

        item["destination"] = destination_cache[destination_id]
        item["activity"] = activity_cache[activity_id]
        enriched.append(item)
    return enriched


def enrich_item(item):
    return enrich_items([item])[0]
