import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import get_settings
from app.db.models import ListingType
from app.db.session import get_session
from app.repositories.listing_repo import ListingRepository
from app.schemas.listing import ListingOut, naira_to_kobo
from app.services.listing_service import to_out

ROOT = Path(__file__).resolve().parents[2]

# Lekki Phase 1, Lagos
LEKKI = (6.4474, 3.4746)
DEFAULT_AGENT_ID = uuid.UUID("7d3f0f3e-4b8f-4c1e-9d6a-2f3c1b5a9e01")


def alembic_config(url: str) -> Config:
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "alembic"))
    cfg.set_main_option("sqlalchemy.url", url)
    cfg.attributes["configure_logger"] = False
    return cfg


@pytest.fixture(scope="session")
def test_database_url() -> str:
    url = get_settings().test_database_url
    if not url:
        pytest.skip("TEST_DATABASE_URL is not set")
    return url


@pytest.fixture(scope="session")
def migrated_database(test_database_url: str) -> str:
    # Once per session. Sync fixture: alembic's env.py calls asyncio.run(), which needs
    # no running event loop.
    command.upgrade(alembic_config(test_database_url), "head")
    return test_database_url


@pytest.fixture(scope="session")
async def engine(migrated_database: str) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(migrated_database)
    yield engine
    await engine.dispose()


@pytest.fixture
async def db_session(engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    """A session inside an outer transaction that is rolled back after the test.

    join_transaction_mode="create_savepoint" turns the code under test's commit() into
    a SAVEPOINT release, so services can commit normally and nothing persists.
    """
    async with engine.connect() as conn:
        outer = await conn.begin()
        session = AsyncSession(
            bind=conn, expire_on_commit=False, join_transaction_mode="create_savepoint"
        )
        try:
            yield session
        finally:
            await session.close()
            await outer.rollback()


@pytest.fixture
async def committing_sessionmaker(
    engine: AsyncEngine,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """Real commits, for tests that need separate transactions (e.g. now() differing).

    Cleans up by truncating afterwards.
    """
    yield async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.execute(text("TRUNCATE listings"))


@pytest.fixture
def db_app(app: FastAPI, db_session: AsyncSession) -> FastAPI:
    # The app shares the test's session, so rows made by listing_factory are visible to
    # requests and everything is rolled back together.
    async def _get_session() -> AsyncIterator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_session] = _get_session
    return app


@pytest.fixture
async def client(db_app: FastAPI) -> AsyncIterator[AsyncClient]:
    """Overrides the root `client` so integration requests hit the test database."""
    async with AsyncClient(transport=ASGITransport(app=db_app), base_url="http://test") as ac:
        yield ac


ListingFactory = Callable[..., Awaitable[ListingOut]]


@pytest.fixture
def listing_factory(db_session: AsyncSession) -> ListingFactory:
    """Insert a listing straight through the repository and return its API shape.

    Accepts any column override plus `price` in naira, `lat`/`lng`, and `created_at`
    (useful because now() is constant within the test transaction).
    """
    repo = ListingRepository(db_session)
    counter = 0

    async def create(
        *,
        price: Decimal | int = 3_500_000,
        lat: float = LEKKI[0],
        lng: float = LEKKI[1],
        created_at: datetime | None = None,
        **overrides: Any,
    ) -> ListingOut:
        nonlocal counter
        counter += 1
        values: dict[str, Any] = {
            "title": f"2 bedroom flat in Lekki Phase 1 #{counter}",
            "description": "Serviced, 24h power",
            "listing_type": ListingType.RENT,
            "bedrooms": 2,
            "address": "Admiralty Way, Lekki Phase 1",
            "city": "Lagos",
            "agent_id": DEFAULT_AGENT_ID,
            "price": naira_to_kobo(Decimal(price)),
        } | overrides
        if created_at is not None:
            values["created_at"] = values["updated_at"] = created_at
        record = await repo.create(values, lat=lat, lng=lng)
        return to_out(record)

    return create
