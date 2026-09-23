import enum
import uuid
from datetime import datetime

from geoalchemy2 import Geography, WKBElement
from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Enum,
    FetchedValue,
    Index,
    MetaData,
    SmallInteger,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import CHAR, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_name)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class ListingType(enum.StrEnum):
    RENT = "rent"
    SALE = "sale"
    SHORTLET = "shortlet"


class Listing(Base):
    __tablename__ = "listings"
    __table_args__ = (
        CheckConstraint("price > 0", name="price_positive"),
        CheckConstraint("bedrooms >= 0", name="bedrooms_non_negative"),
        # Declared explicitly (spatial_index=False below) so autogenerate sees a
        # stable, named index instead of GeoAlchemy2's implicit one.
        Index("ix_listings_location", "location", postgresql_using="gist"),
        Index("ix_listings_type_price_bedrooms", "listing_type", "price", "bedrooms"),
    )
    # Fetch server-generated values (id, timestamps) via RETURNING on insert/update.
    __mapper_args__ = {"eager_defaults": True}

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text)
    # Stored in kobo (1 NGN = 100 kobo); converted to naira at the schema boundary.
    price: Mapped[int] = mapped_column(BigInteger)
    currency: Mapped[str] = mapped_column(CHAR(3), server_default=text("'NGN'"))
    listing_type: Mapped[ListingType] = mapped_column(
        Enum(
            ListingType,
            name="listing_type",
            values_callable=lambda e: [member.value for member in e],
        )
    )
    bedrooms: Mapped[int] = mapped_column(SmallInteger)
    location: Mapped[WKBElement] = mapped_column(
        Geography(geometry_type="POINT", srid=4326, spatial_index=False)
    )
    address: Mapped[str | None] = mapped_column(String(300))
    city: Mapped[str | None] = mapped_column(String(100))
    # Opaque reference to an agent in another system; no agents table (see README).
    agent_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # Maintained by the listings_set_updated_at trigger (see initial migration).
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), server_onupdate=FetchedValue()
    )
