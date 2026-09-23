import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.errors import NotFoundError
from app.repositories.listing_repo import ListingRecord, ListingRepository
from app.schemas.common import PageParams, Paginated
from app.schemas.listing import ListingCreate, ListingOut, ListingUpdate


def to_out(record: ListingRecord) -> ListingOut:
    # kobo -> naira happens in ListingOut.from_model.
    return ListingOut.from_model(
        record.listing, lat=record.lat, lng=record.lng, distance_km=record.distance_km
    )


class ListingService:
    def __init__(self, session: AsyncSession, repo: ListingRepository | None = None) -> None:
        self.session = session
        self.repo = repo or ListingRepository(session)

    @staticmethod
    def _not_found(listing_id: uuid.UUID) -> NotFoundError:
        return NotFoundError("Listing not found", details={"id": str(listing_id)})

    async def create(self, data: ListingCreate) -> ListingOut:
        values = data.model_dump(exclude={"price", "location"})
        values["price"] = data.price_kobo  # naira -> kobo
        record = await self.repo.create(values, lat=data.location.lat, lng=data.location.lng)
        await self.session.commit()
        return to_out(record)

    async def get(self, listing_id: uuid.UUID) -> ListingOut:
        record = await self.repo.get_by_id(listing_id)
        if record is None:
            raise self._not_found(listing_id)
        return to_out(record)

    async def list_page(self, params: PageParams) -> Paginated[ListingOut]:
        records, total = await self.repo.list_paginated(
            limit=params.page_size, offset=params.offset
        )
        return Paginated[ListingOut].build(
            [to_out(r) for r in records],
            total=total,
            page=params.page,
            page_size=params.page_size,
        )

    async def update(self, listing_id: uuid.UUID, data: ListingUpdate) -> ListingOut:
        location = data.location
        record = await self.repo.update(
            listing_id,
            data.changes(),  # price already converted to kobo
            lat=location.lat if location else None,
            lng=location.lng if location else None,
        )
        if record is None:
            raise self._not_found(listing_id)
        await self.session.commit()
        return to_out(record)

    async def delete(self, listing_id: uuid.UUID) -> None:
        if not await self.repo.delete(listing_id):
            raise self._not_found(listing_id)
        await self.session.commit()
