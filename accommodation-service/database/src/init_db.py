import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from db import Base, SessionLocal, engine
from models import Accommodation, Amenity
from sqlalchemy import select

SEED_FILE = Path(__file__).resolve().parent.parent / "seed.json"


def init_db(force: bool = False):
    with SessionLocal() as session:
        if force:
            print("Force reset requested. Dropping all tables...")
            Base.metadata.drop_all(bind=engine)
            session.commit()

        # Create schema if missing
        Base.metadata.create_all(bind=engine)

        # Check if already populated
        has_data = (
            session.scalar(select(Accommodation.id).limit(1)) is not None
        )
        if has_data and not force:
            print("Database already contains data. Skipping seeding.")
            return

        if not SEED_FILE.exists():
            print(f"Error: {SEED_FILE} not found.")
            return

        data = json.loads(SEED_FILE.read_text(encoding="utf-8"))

        # 1. Seed Amenities
        amenity_map = {}
        for item in data.get("amenities", []):
            amenity = session.get(Amenity, item["id"])
            if not amenity:
                amenity = Amenity(id=item["id"], name=item["name"])
                session.add(amenity)
            amenity_map[item["name"]] = amenity

        session.flush()

        # 2. Seed Accommodations
        for item in data.get("accommodations", []):
            acc_amenities = [
                amenity_map[name]
                for name in item.get("amenities", [])
                if name in amenity_map
            ]

            created_at = (
                datetime.fromisoformat(item["created_at"])
                if item.get("created_at")
                else datetime.now(timezone.utc)
            )
            updated_at = (
                datetime.fromisoformat(item["updated_at"])
                if item.get("updated_at")
                else datetime.now(timezone.utc)
            )

            acc = Accommodation(
                id=item["id"],
                name=item["name"],
                destination_id=item["destination_id"],
                destination_city=item.get("destination_city"),
                type=item.get("type"),
                price_per_night=item["price_per_night"],
                rating=item.get("rating"),
                location=item.get("location"),
                latitude=item.get("latitude"),
                longitude=item.get("longitude"),
                description=item.get("description"),
                created_at=created_at,
                updated_at=updated_at,
                amenities=acc_amenities,
            )
            session.add(acc)

        session.commit()
        print("Database initialized and seeded successfully.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Initialize and seed the database."
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Wipe existing data and reseed from scratch",
    )
    args = parser.parse_args()
    init_db(force=args.force)
