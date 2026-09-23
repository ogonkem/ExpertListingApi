import uuid
from dataclasses import dataclass
from typing import Any

from geoalchemy2 import Geography, Geometry
from sqlalchemy import Select, cast, delete, func, insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.db.models import Listing

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
class ListingRecord:
    listing: Listing
    lat: float
    lng: float
    distance_km: float | None = None


class ListingRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    @staticmethod
    def _with_coordinates() -> Select[tuple[Listing, float, float]]:
        return select(Listing, LAT, LNG)

    async def create(self, values: dict[str, Any], *, lat: float, lng: float) -> ListingRecord:
        stmt = (
            insert(Listing).values(**values, location=point(lat, lng)).returning(Listing, LAT, LNG)
        )
        row = (await self.session.execute(stmt)).one()
        return ListingRecord(row.Listing, row.lat, row.lng)

    async def get_by_id(self, listing_id: uuid.UUID) -> ListingRecord | None:
        stmt = self._with_coordinates().where(Listing.id == listing_id)
        row = (await self.session.execute(stmt)).one_or_none()
        return None if row is None else ListingRecord(row.Listing, row.lat, row.lng)

    async def list_paginated(self, *, limit: int, offset: int) -> tuple[list[ListingRecord], int]:
        total = await self.session.scalar(select(func.count()).select_from(Listing)) or 0
        stmt = (
            self._with_coordinates()
            # id breaks ties so pages stay stable when created_at collides.
            .order_by(Listing.created_at.desc(), Listing.id.desc())
            .limit(limit)
            .offset(offset)
        )
        rows = (await self.session.execute(stmt)).all()
        return [ListingRecord(r.Listing, r.lat, r.lng) for r in rows], total

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
