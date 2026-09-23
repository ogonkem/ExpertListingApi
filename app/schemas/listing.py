import uuid
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Any, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PlainSerializer,
    SerializerFunctionWrapHandler,
    StringConstraints,
    field_validator,
    model_serializer,
    model_validator,
)

from app.core.config import get_settings
from app.db.models import Listing, ListingType
from app.schemas.common import PageParams

_settings = get_settings()

KOBO_PER_NAIRA = 100
MAX_BEDROOMS = 50


def naira_to_kobo(naira: Decimal) -> int:
    # Exact: inputs are validated to at most 2 decimal places.
    return int(naira * KOBO_PER_NAIRA)


def kobo_to_naira(kobo: int) -> Decimal:
    return Decimal(kobo) / KOBO_PER_NAIRA


def _naira_to_json(value: Decimal) -> int | float:
    # Emit a JSON number, not Pydantic's default string. Whole amounts stay exact ints;
    # fractional ones are bounded to 15 significant digits (see NairaIn), which a float
    # round-trips exactly, so no precision is lost on the wire.
    return int(value) if value == value.to_integral_value() else float(value)


# 13 integer digits (just under ₦10 trillion) + 2 decimal places; fits BIGINT kobo.
NairaIn = Annotated[Decimal, Field(max_digits=15, decimal_places=2, gt=0)]
NairaFilter = Annotated[Decimal, Field(max_digits=15, decimal_places=2, ge=0)]
NairaOut = Annotated[Decimal, PlainSerializer(_naira_to_json, return_type=int | float)]

Title = Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=200)]
Description = Annotated[str, StringConstraints(strip_whitespace=True, max_length=5000)]
Address = Annotated[str, StringConstraints(strip_whitespace=True, max_length=300)]
City = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
Bedrooms = Annotated[int, Field(ge=0, le=MAX_BEDROOMS)]
Latitude = Annotated[float, Field(ge=-90, le=90)]
Longitude = Annotated[float, Field(ge=-180, le=180)]


class LocationIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lat: Latitude
    lng: Longitude


class LocationOut(BaseModel):
    lat: float
    lng: float


class ListingCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: Title
    description: Description | None = None
    price: NairaIn
    listing_type: ListingType
    bedrooms: Bedrooms
    location: LocationIn
    address: Address | None = None
    city: City | None = None
    agent_id: uuid.UUID

    @property
    def price_kobo(self) -> int:
        return naira_to_kobo(self.price)


# Columns that are NOT NULL in the database: a PATCH may omit them but not null them.
NON_NULLABLE_FIELDS = ("title", "price", "listing_type", "bedrooms", "location", "agent_id")


class ListingUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: Title | None = None
    description: Description | None = None
    price: NairaIn | None = None
    listing_type: ListingType | None = None
    bedrooms: Bedrooms | None = None
    location: LocationIn | None = None
    address: Address | None = None
    city: City | None = None
    agent_id: uuid.UUID | None = None

    # Only runs for values actually supplied (defaults aren't validated), so omitting a
    # field is fine but sending an explicit null is rejected.
    @field_validator(*NON_NULLABLE_FIELDS)
    @classmethod
    def _reject_explicit_null(cls, value: Any) -> Any:
        if value is None:
            raise ValueError("may not be null")
        return value

    @model_validator(mode="after")
    def _require_at_least_one_field(self) -> Self:
        if not self.model_fields_set:
            raise ValueError("at least one field must be provided")
        return self

    def changes(self) -> dict[str, Any]:
        """Supplied fields only, with price converted to kobo."""
        values = self.model_dump(exclude_unset=True, exclude={"location"})
        if "price" in values:
            values["price"] = naira_to_kobo(values["price"])
        return values


class ListingOut(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "id": "0b8f6a52-5c1e-4a8e-9a57-3f1f2d8c7e10",
                    "title": "Serviced 3 bedroom flat in Lekki Phase 1",
                    "description": "24h power, pool and gym. Close to Admiralty Way.",
                    "price": 8500000,
                    "currency": "NGN",
                    "listing_type": "rent",
                    "bedrooms": 3,
                    "location": {"lat": 6.4474, "lng": 3.4746},
                    "address": "15 Admiralty Way, Lekki Phase 1",
                    "city": "Lagos",
                    "agent_id": "7d3f0f3e-4b8f-4c1e-9d6a-2f3c1b5a9e01",
                    "created_at": "2026-09-23T10:15:00Z",
                    "updated_at": "2026-09-23T10:15:00Z",
                }
            ]
        }
    )

    id: uuid.UUID
    title: str
    description: str | None
    price: NairaOut
    currency: str
    listing_type: ListingType
    bedrooms: int
    location: LocationOut
    address: str | None
    city: str | None
    agent_id: uuid.UUID
    created_at: datetime
    updated_at: datetime
    distance_km: float | None = None

    @field_validator("distance_km")
    @classmethod
    def _round_distance(cls, value: float | None) -> float | None:
        return None if value is None else round(value, 2)

    @model_serializer(mode="wrap")
    def _omit_absent_distance(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        # distance_km only means something for geo searches; leave it out otherwise.
        data: dict[str, Any] = handler(self)
        if data.get("distance_km") is None:
            data.pop("distance_km", None)
        return data

    @classmethod
    def from_model(
        cls, listing: Listing, *, lat: float, lng: float, distance_km: float | None = None
    ) -> Self:
        """Build from an ORM row plus coordinates/distance computed by PostGIS."""
        return cls(
            id=listing.id,
            title=listing.title,
            description=listing.description,
            price=kobo_to_naira(listing.price),
            currency=listing.currency,
            listing_type=listing.listing_type,
            bedrooms=listing.bedrooms,
            location=LocationOut(lat=lat, lng=lng),
            address=listing.address,
            city=listing.city,
            agent_id=listing.agent_id,
            created_at=listing.created_at,
            updated_at=listing.updated_at,
            distance_km=distance_km,
        )


class SortOrder(StrEnum):
    DISTANCE = "distance"
    PRICE_ASC = "price_asc"
    PRICE_DESC = "price_desc"
    NEWEST = "newest"


class SearchParams(PageParams):
    model_config = ConfigDict(extra="forbid", validate_by_name=True, validate_by_alias=True)

    listing_type: ListingType | None = Field(default=None, alias="type")
    min_price: NairaFilter | None = None
    max_price: NairaFilter | None = None
    min_bedrooms: Bedrooms | None = None
    max_bedrooms: Bedrooms | None = None
    lat: Latitude | None = None
    lng: Longitude | None = None
    radius_km: Annotated[float, Field(gt=0, le=_settings.max_radius_km)] | None = None
    sort: SortOrder = SortOrder.NEWEST

    @model_validator(mode="after")
    def _check_combinations(self) -> Self:
        geo = {"lat": self.lat, "lng": self.lng, "radius_km": self.radius_km}
        missing = [name for name, value in geo.items() if value is None]
        if 0 < len(missing) < len(geo):
            raise ValueError(
                f"lat, lng and radius_km must be provided together (missing: {', '.join(missing)})"
            )
        if self.sort is SortOrder.DISTANCE and missing:
            raise ValueError("sort=distance requires lat, lng and radius_km")
        if (
            self.min_price is not None
            and self.max_price is not None
            and self.min_price > self.max_price
        ):
            raise ValueError("min_price must be less than or equal to max_price")
        if (
            self.min_bedrooms is not None
            and self.max_bedrooms is not None
            and self.min_bedrooms > self.max_bedrooms
        ):
            raise ValueError("min_bedrooms must be less than or equal to max_bedrooms")
        return self

    @property
    def min_price_kobo(self) -> int | None:
        return None if self.min_price is None else naira_to_kobo(self.min_price)

    @property
    def max_price_kobo(self) -> int | None:
        return None if self.max_price is None else naira_to_kobo(self.max_price)
