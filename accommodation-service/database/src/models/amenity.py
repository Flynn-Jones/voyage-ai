from typing import TYPE_CHECKING
from db import Base
from models.accommodation_amenity import accommodation_amenities
from sqlalchemy import Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

if TYPE_CHECKING:
    from models.accommodation import Accommodation


class Amenity(Base):
    __tablename__ = "amenities"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String, unique=True, nullable=False)

    accommodations: Mapped[list["Accommodation"]] = relationship(
        secondary=accommodation_amenities,
        back_populates="amenities",
    )
