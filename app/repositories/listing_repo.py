import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from geoalchemy2 import Geography, Geometry
from sqlalchemy import Select, cast, delete, func, insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.db.models import Listing, ListingType
from app.schemas.listing import SortOrder

# `geometry` (no typmod) for reading coordinates back out of the geography column.
_GEOMETRY = Geometry(geometry_type=None, srid=-1)
_GEOGRAPHY_POINT = Geography(geometry_type="POINT", srid=4326, spatial_index=False)


def point(lat: float, lng: float) -> ColumnElement[Any]:
    """ST_SetSRID(ST_MakePoint(lng, lat), 4326)::geography.

    ST_MakePoint takes (x, y) = (longitude, latitude), the reverse of how people usually
    say coordinates. Everything outside this module deals in named lat/lng.
    """
    return cast(func.ST_SetSRID(func.ST_MakePoint(lng, lat), 4326), _GEOGRAPHY_POINT)


LAT = func.ST_Y(cast(Listing.location, _GEOMETRY)).label("lat")
LNG = func.ST_X(cast(Listing.location, _GEOMETRY)).label("lng")


@dataclass(frozen=True, slots=True)
class GeoFilter:
    lat: float
    lng: float
    radius_km: float


@dataclass(frozen=True, slots=True)
class SearchFilters:
    """Repository-level filters. Prices are in kobo; None means "not filtered"."""

    listing_type: ListingType | None = None
    min_price: int | None = None
    max_price: int | None = None
    min_bedrooms: int | None = None
    max_bedrooms: int | None = None
    geo: GeoFilter | None = None


@dataclass(frozen=True, slots=True)
class ListingRecord:
    listing: Listing
    lat: float
    lng: float
    distance_km: float | None = None


def _conditions(filters: SearchFilters) -> list[ColumnElement[bool]]:
    """AND-able predicates for only the filters that were provided."""
    conditions: list[ColumnElement[bool]] = []
    if filters.listing_type is not None:
        conditions.append(Listing.listing_type == filters.listing_type)
    if filters.min_price is not None:
        conditions.append(Listing.price >= filters.min_price)
    if filters.max_price is not None:
        conditions.append(Listing.price <= filters.max_price)
    if filters.min_bedrooms is not None:
        conditions.append(Listing.bedrooms >= filters.min_bedrooms)
    if filters.max_bedrooms is not None:
        conditions.append(Listing.bedrooms <= filters.max_bedrooms)
    if filters.geo is not None:
        # Geography ST_DWithin takes metres and is index-assisted: it expands to a
        # bounding-box && against ix_listings_location (GIST) plus an exact recheck.
        center = point(filters.geo.lat, filters.geo.lng)
        conditions.append(func.ST_DWithin(Listing.location, center, filters.geo.radius_km * 1000))
    return conditions


def _distance_km(geo: GeoFilter) -> ColumnElement[Any]:
    # Geography distance is in metres on the WGS84 spheroid.
    return (func.ST_Distance(Listing.location, point(geo.lat, geo.lng)) / 1000).label("distance_km")


def search_statement(
    filters: SearchFilters, sort: SortOrder, limit: int, offset: int
) -> Select[Any]:
    """The page query for a search (exposed for EXPLAIN / tests)."""
    columns: list[Any] = [Listing, LAT, LNG]
    distance = _distance_km(filters.geo) if filters.geo is not None else None
    if distance is not None:
        columns.append(distance)

    # Every ordering ends with id so ties (same price, same created_at, same distance)
    # never reshuffle between pages.
    order_by: list[ColumnElement[Any]]
    match sort:
        case SortOrder.DISTANCE:
            if distance is None:
                raise ValueError("sort=distance requires a point")
            order_by = [distance.asc(), Listing.id.asc()]
        case SortOrder.PRICE_ASC:
            order_by = [Listing.price.asc(), Listing.id.asc()]
        case SortOrder.PRICE_DESC:
            order_by = [Listing.price.desc(), Listing.id.asc()]
        case SortOrder.NEWEST:
            order_by = [Listing.created_at.desc(), Listing.id.desc()]

    return (
        select(*columns)
        .where(*_conditions(filters))
        .order_by(*order_by)
        .limit(limit)
        .offset(offset)
    )


class ListingRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(self, values: dict[str, Any], *, lat: float, lng: float) -> ListingRecord:
        stmt = (
            insert(Listing).values(**values, location=point(lat, lng)).returning(Listing, LAT, LNG)
        )
        row = (await self.session.execute(stmt)).one()
        return ListingRecord(row.Listing, row.lat, row.lng)

    async def bulk_create(
        self, rows: Sequence[tuple[dict[str, Any], float, float]], *, chunk_size: int = 500
    ) -> int:
        """Insert many (values, lat, lng) rows with one multi-VALUES INSERT per chunk."""
        for start in range(0, len(rows), chunk_size):
            chunk = rows[start : start + chunk_size]
            await self.session.execute(
                insert(Listing).values(
                    [values | {"location": point(lat, lng)} for values, lat, lng in chunk]
                )
            )
        return len(rows)

    async def count(self) -> int:
        return await self.session.scalar(select(func.count()).select_from(Listing)) or 0

    async def delete_all(self) -> int:
        result = await self.session.execute(delete(Listing))
        return int(result.rowcount)  # type: ignore[attr-defined]

    async def get_by_id(self, listing_id: uuid.UUID) -> ListingRecord | None:
        stmt = select(Listing, LAT, LNG).where(Listing.id == listing_id)
        row = (await self.session.execute(stmt)).one_or_none()
        return None if row is None else ListingRecord(row.Listing, row.lat, row.lng)

    async def list_paginated(self, *, limit: int, offset: int) -> tuple[list[ListingRecord], int]:
        return await self.search(SearchFilters(), SortOrder.NEWEST, limit=limit, offset=offset)

    async def search(
        self, filters: SearchFilters, sort: SortOrder, *, limit: int, offset: int
    ) -> tuple[list[ListingRecord], int]:
        conditions = _conditions(filters)

        # Total via a separate COUNT over the filtered subquery rather than
        # COUNT(*) OVER () on the page query:
        # - a window count only arrives on returned rows, so a page past the end would
        #   report total=0 instead of the real total;
        # - the window forces Postgres to compute distance and sort *every* matching row
        #   just to count them, whereas this COUNT skips the distance and ORDER BY and
        #   lets the page query stop early under LIMIT (e.g. a GIST-backed KNN scan).
        # The cost is a second round trip, which is cheap next to either of those.
        count_stmt = select(func.count()).select_from(
            select(Listing.id).where(*conditions).subquery()
        )
        total = await self.session.scalar(count_stmt) or 0
        if total == 0 or offset >= total:
            return [], total

        rows = (await self.session.execute(search_statement(filters, sort, limit, offset))).all()
        return [
            ListingRecord(r.Listing, r.lat, r.lng, getattr(r, "distance_km", None)) for r in rows
        ], total

    async def update(
        self,
        listing_id: uuid.UUID,
        values: dict[str, Any],
        *,
        lat: float | None = None,
        lng: float | None = None,
    ) -> ListingRecord | None:
        changes = dict(values)
        if lat is not None and lng is not None:
            changes["location"] = point(lat, lng)
        if not changes:
            return await self.get_by_id(listing_id)

        stmt = (
            update(Listing)
            .where(Listing.id == listing_id)
            .values(**changes)
            .returning(Listing, LAT, LNG)
            # Refresh any instance already in the identity map with the returned row
            # (including the trigger-maintained updated_at).
            .execution_options(synchronize_session=False, populate_existing=True)
        )
        row = (await self.session.execute(stmt)).one_or_none()
        return None if row is None else ListingRecord(row.Listing, row.lat, row.lng)

    async def delete(self, listing_id: uuid.UUID) -> bool:
        stmt = delete(Listing).where(Listing.id == listing_id).returning(Listing.id)
        deleted = await self.session.scalar(stmt.execution_options(synchronize_session=False))
        return deleted is not None
