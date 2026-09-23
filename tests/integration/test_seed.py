import pytest
from httpx import AsyncClient
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Listing
from scripts.seed import seed
from tests.integration.conftest import ListingFactory

pytestmark = pytest.mark.integration


async def count(session: AsyncSession) -> int:
    return await session.scalar(select(func.count()).select_from(Listing)) or 0


async def test_seed_inserts_requested_count(db_session: AsyncSession) -> None:
    assert await seed(db_session, count=120, reset=False, rng_seed=1) == 120
    assert await count(db_session) == 120


async def test_reset_makes_seeding_repeatable(
    db_session: AsyncSession, listing_factory: ListingFactory
) -> None:
    await listing_factory(title="pre-existing listing")

    await seed(db_session, count=50, reset=True, rng_seed=3)
    first = set((await db_session.scalars(select(Listing.id))).all())
    await seed(db_session, count=50, reset=True, rng_seed=3)
    second = set((await db_session.scalars(select(Listing.id))).all())

    assert len(first) == 50
    assert first == second  # same ids: fully reproducible
    assert await count(db_session) == 50  # pre-existing row removed


async def test_without_reset_rows_are_appended(db_session: AsyncSession) -> None:
    await seed(db_session, count=10, reset=False, rng_seed=1)
    await seed(db_session, count=10, reset=False, rng_seed=2)

    assert await count(db_session) == 20


async def test_appending_twice_with_the_same_seed_does_not_collide(
    db_session: AsyncSession,
) -> None:
    # Regression: the same --seed used to regenerate the same ids -> pk violation.
    await seed(db_session, count=25, reset=False, rng_seed=42)
    await seed(db_session, count=25, reset=False, rng_seed=42)

    assert await count(db_session) == 50
    addresses = (await db_session.scalars(select(Listing.address))).all()
    assert len(set(addresses)) > 25  # a fresh batch, not a copy of the first


async def test_first_append_into_empty_db_matches_reset(db_session: AsyncSession) -> None:
    await seed(db_session, count=20, reset=False, rng_seed=5)
    appended = set((await db_session.scalars(select(Listing.id))).all())
    await seed(db_session, count=20, reset=True, rng_seed=5)
    reset = set((await db_session.scalars(select(Listing.id))).all())

    assert appended == reset


async def test_seeded_points_are_stored_lng_lat(db_session: AsyncSession) -> None:
    await seed(db_session, count=200, reset=True, rng_seed=42)

    # Every point sits in Nigeria: lat 4-14 N, lng 2.5-15 E. A swap would put Lagos
    # (lat 6.4, lng 3.4) at lat 3.4 -- outside the box.
    outside = await db_session.scalar(
        text(
            "SELECT count(*) FROM listings WHERE NOT ("
            "ST_Y(location::geometry) BETWEEN 4 AND 14 AND "
            "ST_X(location::geometry) BETWEEN 2.5 AND 15)"
        )
    )
    assert outside == 0


async def test_seeded_data_is_searchable(client: AsyncClient, db_session: AsyncSession) -> None:
    await seed(db_session, count=300, reset=True, rng_seed=42)

    response = await client.get(
        "/api/v1/listings/search",
        params={"lat": 6.4478, "lng": 3.4723, "radius_km": 3, "sort": "distance"},
    )

    body = response.json()
    assert response.status_code == 200
    assert body["meta"]["total"] > 0
    assert all(item["distance_km"] <= 3 for item in body["data"])
    assert any("Lekki" in item["address"] for item in body["data"])
