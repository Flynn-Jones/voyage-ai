from datetime import datetime
from typing import TYPE_CHECKING
from db import Base
from models.accommodation_amenity import accommodation_amenities
from sqlalchemy import CheckConstraint, DateTime, Float, Index, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

if TYPE_CHECKING:
    from models.amenity import Amenity


class Accommodation(Base):
    __tablename__ = "accommodations"
    __table_args__ = (
        CheckConstraint(
            "type IN ('hotel','hostel','ryokan','apartment','guesthouse')",
            name="check_accommodation_type",
        ),
        CheckConstraint("price_per_night >= 0", name="check_positive_price"),
        CheckConstraint("rating BETWEEN 0 AND 5", name="check_valid_rating"),
        CheckConstraint("latitude BETWEEN -90 AND 90", name="check_valid_latitude"),
        CheckConstraint(
            "longitude BETWEEN -180 AND 180", name="check_valid_longitude"
        ),
        Index("idx_accommodations_destination_id", "destination_id"),
        Index("idx_accommodations_coordinates", "latitude", "longitude"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    destination_id: Mapped[str] = mapped_column(String(50), nullable=False)
    destination_city: Mapped[str | None] = mapped_column(String, nullable=True)
    type: Mapped[str | None] = mapped_column(String, nullable=True)
    price_per_night: Mapped[float] = mapped_column(Float, nullable=False)
    rating: Mapped[float | None] = mapped_column(Float, nullable=True)
    location: Mapped[str | None] = mapped_column(String, nullable=True)
    latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    longitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    description: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=func.now(), onupdate=func.now()
    )

    amenities: Mapped[list["Amenity"]] = relationship(
        secondary=accommodation_amenities,
        back_populates="accommodations",
        lazy="selectin",
    )
