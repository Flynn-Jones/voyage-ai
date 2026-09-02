from datetime import datetime, timezone
from flask import Blueprint, abort, jsonify, request
from db import SessionLocal
from models import Accommodation, Amenity
from sqlalchemy import distinct, func, select

bp = Blueprint("accommodations", __name__, url_prefix="/accommodations")


def serialize_accommodation(acc: Accommodation, full: bool = True) -> dict:
    """Helper to convert Accommodation ORM model into a JSON-ready dictionary."""
    data = {
        "id": acc.id,
        "name": acc.name,
        "destination_id": acc.destination_id,
        "destination_city": acc.destination_city,
        "type": acc.type,
        "price_per_night": acc.price_per_night,
        "rating": acc.rating,
        "location": acc.location,
        "amenities": [amenity.name for amenity in acc.amenities],
    }
    if full:
        data.update(
            {
                "latitude": acc.latitude,
                "longitude": acc.longitude,
                "description": acc.description,
                "created_at": (
                    acc.created_at.isoformat() if acc.created_at else None
                ),
                "updated_at": (
                    acc.updated_at.isoformat() if acc.updated_at else None
                ),
            }
        )
    return data


# --- 1. LIST / SEARCH / FILTER / SORT ---
@bp.get("")
def list_accommodations():
    """Query Parameters:

    - q: Text search on name, description, or location
    - destination_id: Filter by destination ID (e.g. 'dest-tokyo')
    - destination_city: Filter by city name (e.g. 'Tokyo')
    - type: Filter by accommodation type ('hotel', 'hostel', etc.)
    - min_price / max_price: Price range bounds
    - min_rating: Minimum rating (0.0 to 5.0)
    - amenities: Comma-separated amenity names (e.g. 'WiFi,Pool,Onsen')
    - sort_by: 'price_asc', 'price_desc', 'rating_desc', 'name_asc',
    'created_at_desc'
    - limit / offset: Pagination parameters
    """
    # Parse query parameters
    q = request.args.get("q", type=str)
    destination_id = request.args.get("destination_id", type=str)
    destination_city = request.args.get("destination_city", type=str)
    acc_type = request.args.get("type", type=str)
    min_price = request.args.get("min_price", type=float)
    max_price = request.args.get("max_price", type=float)
    min_rating = request.args.get("min_rating", type=float)
    amenities_param = request.args.get("amenities", type=str)
    sort_by = request.args.get("sort_by", default="created_at_desc", type=str)
    limit = max(1, min(request.args.get("limit", default=20, type=int), 100))
    offset = max(0, request.args.get("offset", default=0, type=int))

    with SessionLocal() as session:
        stmt = select(Accommodation)

        # Keyword Search
        if q:
            term = f"%{q.strip()}%"
            stmt = stmt.where(
                (Accommodation.name.ilike(term))
                | (Accommodation.description.ilike(term))
                | (Accommodation.location.ilike(term))
            )

        # Exact / Direct Filters
        if destination_id:
            stmt = stmt.where(Accommodation.destination_id == destination_id)

        if destination_city:
            stmt = stmt.where(
                Accommodation.destination_city.ilike(destination_city)
            )

        if acc_type:
            stmt = stmt.where(Accommodation.type == acc_type.lower())

        # Numeric Range Filters
        if min_price is not None:
            stmt = stmt.where(Accommodation.price_per_night >= min_price)
        if max_price is not None:
            stmt = stmt.where(Accommodation.price_per_night <= max_price)
        if min_rating is not None:
            stmt = stmt.where(Accommodation.rating >= min_rating)

        # Amenity Filter (Requires accommodation to contain ALL requested amenities)
        if amenities_param:
            required_amenities = [
                a.strip() for a in amenities_param.split(",") if a.strip()
            ]
            if required_amenities:
                stmt = (
                    stmt.join(Accommodation.amenities)
                    .where(Amenity.name.in_(required_amenities))
                    .group_by(Accommodation.id)
                    .having(
                        func.count(distinct(Amenity.id))
                        == len(required_amenities)
                    )
                )

        # Sorting
        sort_options = {
            "price_asc": Accommodation.price_per_night.asc(),
            "price_desc": Accommodation.price_per_night.desc(),
            "rating_desc": Accommodation.rating.desc().nullslast(),
            "name_asc": Accommodation.name.asc(),
            "created_at_desc": Accommodation.created_at.desc(),
        }
        order_clause = sort_options.get(
            sort_by, Accommodation.created_at.desc()
        )
        stmt = stmt.order_by(order_clause)

        # Total count query (subquery preserves group_by / having filters)
        count_stmt = select(func.count()).select_from(stmt.subquery())
        total = session.scalar(count_stmt) or 0

        # Pagination
        stmt = stmt.offset(offset).limit(limit)
        results = session.scalars(stmt).all()

        return jsonify(
            {
                "total": total,
                "limit": limit,
                "offset": offset,
                "data": [serialize_accommodation(a) for a in results],
            }
        )


# --- 2. GET BY ID ---
@bp.get("/<int:acc_id>")
def get_accommodation(acc_id: int):
    with SessionLocal() as session:
        acc = session.get(Accommodation, acc_id)
        if not acc:
            abort(404, description=f"Accommodation with id {acc_id} not found")
        return jsonify(serialize_accommodation(acc))


# --- 3. CREATE ---
@bp.post("")
def create_accommodation():
    data = request.get_json() or {}

    # Basic field validation
    required_fields = ["name", "destination_id", "price_per_night"]
    for field in required_fields:
        if field not in data:
            abort(400, description=f"Missing required field: '{field}'")

    with SessionLocal() as session:
        # Resolve amenities by name or ID
        amenity_ids = data.get("amenity_ids", [])
        amenity_names = data.get("amenities", [])

        amenities = []
        if amenity_ids:
            amenities = list(
                session.scalars(
                    select(Amenity).where(Amenity.id.in_(amenity_ids))
                ).all()
            )
        elif amenity_names:
            amenities = list(
                session.scalars(
                    select(Amenity).where(Amenity.name.in_(amenity_names))
                ).all()
            )

        new_acc = Accommodation(
            name=data["name"],
            destination_id=data["destination_id"],
            destination_city=data.get("destination_city"),
            type=data.get("type"),
            price_per_night=float(data["price_per_night"]),
            rating=float(data["rating"]) if data.get("rating") else None,
            location=data.get("location"),
            latitude=float(data["latitude"]) if data.get("latitude") else None,
            longitude=(
                float(data["longitude"]) if data.get("longitude") else None
            ),
            description=data.get("description"),
            amenities=amenities,
        )

        session.add(new_acc)
        session.commit()
        session.refresh(new_acc)

        return jsonify(serialize_accommodation(new_acc)), 201


# --- 4. UPDATE (PATCH / PUT) ---
@bp.patch("/<int:acc_id>")
@bp.put("/<int:acc_id>")
def update_accommodation(acc_id: int):
    data = request.get_json() or {}

    with SessionLocal() as session:
        acc = session.get(Accommodation, acc_id)
        if not acc:
            abort(404, description=f"Accommodation with id {acc_id} not found")

        # Direct attribute updates
        updatable_fields = [
            "name",
            "destination_id",
            "destination_city",
            "type",
            "price_per_night",
            "rating",
            "location",
            "latitude",
            "longitude",
            "description",
        ]
        for field in updatable_fields:
            if field in data:
                setattr(acc, field, data[field])

        # Relationship updates (if provided)
        if "amenity_ids" in data:
            amenities = list(
                session.scalars(
                    select(Amenity).where(Amenity.id.in_(data["amenity_ids"]))
                ).all()
            )
            acc.amenities = amenities
        elif "amenities" in data:
            amenities = list(
                session.scalars(
                    select(Amenity).where(Amenity.name.in_(data["amenities"]))
                ).all()
            )
            acc.amenities = amenities

        acc.updated_at = datetime.now(timezone.utc)
        session.commit()
        session.refresh(acc)

        return jsonify(serialize_accommodation(acc))


# --- 5. DELETE ---
@bp.delete("/<int:acc_id>")
def delete_accommodation(acc_id: int):
    with SessionLocal() as session:
        acc = session.get(Accommodation, acc_id)
        if not acc:
            abort(404, description=f"Accommodation with id {acc_id} not found")

        session.delete(acc)
        session.commit()

        return jsonify({"message": f"Accommodation {acc_id} deleted successfully"}), 200
