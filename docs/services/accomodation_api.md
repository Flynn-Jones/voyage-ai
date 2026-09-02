# Accommodation Service API Documentation

---

## Overview

- **Base URL**: `http://accommodation-db:6002`
- **Content-Type**: `application/`
- **Database**: SQLite (SQLAlchemy 2.0 ORM)

---

## Endpoints

### 1. Health Check

Verifies service uptime and database connectivity.

- **Method**: `GET`
- **Route**: `/health`
- **Success Response (`200 OK`)**:

```
{
"database": "healthy",
"status": "healthy"
}
```

- **Error Response (`503 Service Unavailable`)**:

```
{
"database": "unhealthy: <error details>",
"status": "unhealthy"
}
```

---

### 2. List / Search / Filter Accommodations

Retrieves a paginated list of accommodations with support for full-text search, multi-field filtering, amenity matching, and sorting.

- **Method**: `GET`
- **Route**: `/accommodations`
- **Query Parameters**:

| Parameter          | Type     | Default           | Description                                                             | Example                     |
| ------------------ | -------- | ----------------- | ----------------------------------------------------------------------- | --------------------------- |
| `q`                | `string` | —                 | Case-insensitive search on `name`, `description`, or `location`         | `q=kabukicho`               |
| `destination_id`   | `string` | —                 | Exact logical ID reference                                              | `destination_id=dest-tokyo` |
| `destination_city` | `string` | —                 | Case-insensitive city search                                            | `destination_city=Tokyo`    |
| `type`             | `string` | —                 | `hotel`, `hostel`, `ryokan`, `apartment`, `guesthouse`                  | `type=ryokan`               |
| `min_price`        | `float`  | —                 | Minimum price per night                                                 | `min_price=50.0`            |
| `max_price`        | `float`  | —                 | Maximum price per night                                                 | `max_price=200.0`           |
| `min_rating`       | `float`  | —                 | Minimum review score (`0.0` to `5.0`)                                   | `min_rating=4.5`            |
| `amenities`        | `string` | —                 | Comma-separated list of required amenities (AND logic)                  | `amenities=WiFi,Onsen`      |
| `sort_by`          | `string` | `created_at_desc` | `price_asc`, `price_desc`, `rating_desc`, `name_asc`, `created_at_desc` | `sort_by=price_asc`         |
| `limit`            | `int`    | `20`              | Results per page (min `1`, max `100`)                                   | `limit=10`                  |
| `offset`           | `int`    | `0`               | Number of records to skip                                               | `offset=20`                 |

- **Success Response (`200 OK`)**:

```
{
    "total": 1,
    "limit": 20,
    "offset": 0,
    "data": [{
        "id": 1,
        "name": "Shinjuku Granbell Hotel",
        "destination_id": "dest-tokyo",
        "destination_city": "Tokyo",
        "type": "hotel",
        "price_per_night": 145.0,
        "rating": 4.3,
        "location": "Shinjuku",
        "latitude": 35.6947,
        "longitude": 139.7025,
        "description": "Modern hotel steps from Kabukicho.",
        "created_at": "2026-08-31T03:37:05",
        "updated_at": "2026-08-31T03:37:05",
        "amenities": [
            "24-Hour Front Desk",
            "Air Conditioning",
            "Bar",
            "WiFi"
        ]
    }]
}
```

---

### 3. Get Accommodation by ID

Retrieves a single accommodation record including all details and attached amenities.

- **Method**: `GET`
- **Route**: `/accommodations/<int:id>`
- **Success Response (`200 OK`)**:

```
{
    "id": 1,
    "name": "Shinjuku Granbell Hotel",
    "destination_id": "dest-tokyo",
    "destination_city": "Tokyo",
    "type": "hotel",
    "price_per_night": 145.0,
    "rating": 4.3,
    "location": "Shinjuku",
    "latitude": 35.6947,
    "longitude": 139.7025,
    "description": "Modern hotel steps from Kabukicho.",
    "created_at": "2026-08-31T03:37:05",
    "updated_at": "2026-08-31T03:37:05",
    "amenities": [
        "24-Hour Front Desk",
        "Air Conditioning",
        "Bar",
        "WiFi"
    ]
}
```

- **Error Response (`404 Not Found`)**:

```
{
    "message": "Accommodation with id 999 not found"
}
```

---

### 4. Create Accommodation

Creates a new accommodation record and associates amenities by ID or name.

- **Method**: `POST`
- **Route**: `/accommodations`
- **Request Body**:

| Field              | Type       | Required | Description                                                     |
| ------------------ | ---------- | -------- | --------------------------------------------------------------- |
| `name`             | `string`   | Yes      | Name of the accommodation                                       |
| `destination_id`   | `string`   | Yes      | Logical destination reference (e.g., `dest-tokyo`)              |
| `price_per_night`  | `float`    | Yes      | Non-negative price value                                        |
| `destination_city` | `string`   | No       | City name for denormalized display                              |
| `type`             | `string`   | No       | Allowed: `hotel`, `hostel`, `ryokan`, `apartment`, `guesthouse` |
| `rating`           | `float`    | No       | Rating between `0.0` and `5.0`                                  |
| `location`         | `string`   | No       | Neighborhood / district                                         |
| `latitude`         | `float`    | No       | Decimal coordinate between `-90.0` and `90.0`                   |
| `longitude`        | `float`    | No       | Decimal coordinate between `-180.0` and `180.0`                 |
| `description`      | `string`   | No       | Detailed property description                                   |
| `amenity_ids`      | `int[]`    | No       | List of existing amenity IDs                                    |
| `amenities`        | `string[]` | No       | List of amenity names (alternative to `amenity_ids`)            |

- **Example Request**:

```
{
    "name": "Kyoto Ryokan Gion",
    "destination_id": "dest-kyoto",
    "destination_city": "Kyoto",
    "type": "ryokan",
    "price_per_night": 220.0,
    "rating": 4.8,
    "location": "Gion",
    "latitude": 35.0037,
    "longitude": 135.7772,
    "description": "Traditional luxury inn in the historical district.",
    "amenities": ["WiFi", "Onsen", "Breakfast Included"]
}
```

- **Success Response (`201 Created`)**:

```
{
    "id": 22,
    "name": "Kyoto Ryokan Gion",
    "destination_id": "dest-kyoto",
    "destination_city": "Kyoto",
    "type": "ryokan",
    "price_per_night": 220.0,
    "rating": 4.8,
    "location": "Gion",
    "latitude": 35.0037,
    "longitude": 135.7772,
    "description": "Traditional luxury inn in the historical district.",
    "created_at": "2026-08-31T15:56:51",
    "updated_at": "2026-08-31T15:56:51",
    "amenities": ["WiFi", "Onsen", "Breakfast Included"]
}
```

---

### 5. Update Accommodation

Updates specific fields of an existing accommodation (supports partial updates).

- **Method**: `PATCH` / `PUT`
- **Route**: `/accommodations/<int:id>`
- **Example Request**:

```
{
    "price_per_night": 160.0,
    "rating": 4.5,
    "amenities": ["WiFi", "Bar", "Pool"]
}
```

- **Success Response (`200 OK`)**:

```
{
    "id": 1,
    "name": "Shinjuku Granbell Hotel",
    "destination_id": "dest-tokyo",
    "destination_city": "Tokyo",
    "type": "hotel",
    "price_per_night": 160.0,
    "rating": 4.5,
    "location": "Shinjuku",
    "latitude": 35.6947,
    "longitude": 139.7025,
    "description": "Modern hotel steps from Kabukicho.",
    "created_at": "2026-08-31T03:37:05",
    "updated_at": "2026-08-31T16:00:12",
    "amenities": ["WiFi", "Bar", "Pool"]
}
```

---

### 6. Delete Accommodation

Removes an accommodation and its relationship associations (cascaded).

- **Method**: `DELETE`
- **Route**: `/accommodations/<int:id>`
- **Success Response (`200 OK`)**:

```
{
    "message": "Accommodation 1 deleted successfully"
}
```

- **Error Response (`404 Not Found`)**:

```
{
    "message": "Accommodation with id 999 not found"
}
```
