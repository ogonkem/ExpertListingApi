from collections.abc import AsyncIterator

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.db.session import get_session

pytestmark = pytest.mark.integration


@pytest.fixture
async def test_db_app(app: FastAPI) -> AsyncIterator[FastAPI]:
    url = get_settings().test_database_url
    if not url:
        pytest.skip("TEST_DATABASE_URL is not set")

    engine = create_async_engine(url)
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)

    async def _get_session() -> AsyncIterator[AsyncSession]:
        async with sessionmaker() as session:
            yield session

    app.dependency_overrides[get_session] = _get_session
    yield app
    await engine.dispose()


async def test_health_against_real_db(test_db_app: FastAPI, client: AsyncClient) -> None:
    response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ok"}
