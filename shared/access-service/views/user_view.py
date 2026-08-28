"""Shapes user data into the access-service's public response contract."""


def format_user(user):
    return {
        "id": user["id"],
        "name": user["name"],
        "preferences": user["preferences"],
        "travel_style": user["travel_style"],
    }


def format_users(users):
    return [format_user(user) for user in users]
