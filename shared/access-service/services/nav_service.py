"""Shared navigation/config data: the five student feature links and their ports."""

FEATURES = [
    {"name": "Destinations", "port": 3001, "owner": "student-1"},
    {"name": "Accommodation", "port": 3002, "owner": "student-2"},
    {"name": "Activities", "port": 3003, "owner": "student-3"},
    {"name": "Budget", "port": 3004, "owner": "student-4"},
    {"name": "Itinerary", "port": 3005, "owner": "student-5"},
]


def get_features():
    return FEATURES
