from collections.abc import AsyncIterator
from typing import Any

from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy.exc import OperationalError

from app.db.session import get_session


class FakeSession:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error

    async def execute(self, *_: Any, **__: Any) -> None:
        if self.error is not None:
            raise self.error


def override_session(app: FastAPI, session: FakeSession) -> None:
    async def _get_session() -> AsyncIterator[FakeSession]:
        yield session

    app.dependency_overrides[get_session] = _get_session


async def test_health_ok(app: FastAPI, client: AsyncClient) -> None:
    override_session(app, FakeSession())

    response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ok"}


async def test_health_db_unreachable(app: FastAPI, client: AsyncClient) -> None:
    override_session(app, FakeSession(OperationalError("SELECT 1", {}, Exception("down"))))

    response = await client.get("/health")

    assert response.status_code == 503
    assert response.json() == {"status": "degraded", "database": "unreachable"}


async def test_unknown_route_uses_error_envelope(client: AsyncClient) -> None:
    response = await client.get("/does-not-exist")

    assert response.status_code == 404
    assert response.json() == {
        "error": {"code": "not_found", "message": "Not Found", "details": None}
    }
