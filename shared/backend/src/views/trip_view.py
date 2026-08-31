"""Shapes trip data into the access's public response contract."""


def format_trip(trip):
    return {
        "id": trip["id"],
        "user_id": trip["user_id"],
        "trip_name": trip["trip_name"],
        "start_date": trip["start_date"],
        "end_date": trip["end_date"],
        "status": trip["status"],
    }


def format_trips(trips):
    return [format_trip(trip) for trip in trips]
