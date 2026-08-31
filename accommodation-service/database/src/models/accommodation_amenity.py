from db import Base
from sqlalchemy import Column, ForeignKey, Integer, Table

accommodation_amenities = Table(
    "accommodation_amenities",
    Base.metadata,
    Column(
        "accommodation_id",
        Integer,
        ForeignKey("accommodations.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "amenity_id",
        Integer,
        ForeignKey("amenities.id", ondelete="CASCADE"),
        primary_key=True,
    ),
)
