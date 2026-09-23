import logging
from collections.abc import AsyncIterator

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import BaseModel, Field

from app.core.request_id import REQUEST_ID_HEADER
from app.errors import ConflictError, DomainValidationError, NotFoundError


class Payload(BaseModel):
    name: str = Field(min_length=3)
    count: int


@pytest.fixture
def error_app(app: FastAPI) -> FastAPI:
    @app.get("/_test/not-found")
    async def not_found() -> None:
        raise NotFoundError("Listing not found", details={"id": "abc"})

    @app.get("/_test/conflict")
    async def conflict() -> None:
        raise ConflictError("Already exists")

    @app.get("/_test/domain-invalid")
    async def domain_invalid() -> None:
        raise DomainValidationError("Bad combination")

    @app.post("/_test/body")
    async def body(payload: Payload) -> Payload:
        return payload

    @app.get("/_test/boom")
    async def boom() -> None:
        raise RuntimeError("secret internal detail")

    return app


@pytest.fixture
async def error_client(error_app: FastAPI) -> AsyncIterator[AsyncClient]:
    # raise_app_exceptions=False: let the 500 handler's response through instead of
    # re-raising the original exception into the test.
    transport = ASGITransport(app=error_app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.mark.parametrize(
    ("path", "status", "code", "message", "details"),
    [
        ("/_test/not-found", 404, "not_found", "Listing not found", {"id": "abc"}),
        ("/_test/conflict", 409, "conflict", "Already exists", None),
        ("/_test/domain-invalid", 422, "validation_error", "Bad combination", None),
    ],
)
async def test_domain_errors_use_envelope(
    error_client: AsyncClient,
    path: str,
    status: int,
    code: str,
    message: str,
    details: object,
) -> None:
    response = await error_client.get(path)

    assert response.status_code == status
    assert response.json() == {"error": {"code": code, "message": message, "details": details}}


async def test_request_validation_error_is_compact(error_client: AsyncClient) -> None:
    response = await error_client.post("/_test/body", json={"name": "ab", "count": "x"})

    assert response.status_code == 422
    assert response.json() == {
        "error": {
            "code": "validation_error",
            "message": "Request validation failed",
            "details": [
                {"field": "body.name", "message": "String should have at least 3 characters"},
                {
                    "field": "body.count",
                    "message": (
                        "Input should be a valid integer, unable to parse string as an integer"
                    ),
                },
            ],
        }
    }


async def test_unhandled_error_returns_generic_500_with_request_id(
    error_client: AsyncClient, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.ERROR, logger="app.errors"):
        response = await error_client.get("/_test/boom", headers={REQUEST_ID_HEADER: "req-123"})

    assert response.status_code == 500
    assert response.headers[REQUEST_ID_HEADER] == "req-123"
    assert response.json() == {
        "error": {
            "code": "internal_error",
            "message": "An unexpected error occurred",
            "details": {"request_id": "req-123"},
        }
    }
    assert "secret internal detail" not in response.text
    record = next(r for r in caplog.records if r.name == "app.errors")
    assert "request_id=req-123" in record.getMessage()
    assert record.exc_info is not None


async def test_request_id_is_echoed(client: AsyncClient) -> None:
    response = await client.get("/does-not-exist", headers={REQUEST_ID_HEADER: "abc-123_x.y"})

    assert response.headers[REQUEST_ID_HEADER] == "abc-123_x.y"


async def test_request_id_is_generated_when_missing(client: AsyncClient) -> None:
    response = await client.get("/does-not-exist")

    assert len(response.headers[REQUEST_ID_HEADER]) == 32


@pytest.mark.parametrize("bad", ["has spaces", "x" * 129, "semi;colon", "new\tline"])
async def test_unsafe_request_id_is_replaced(client: AsyncClient, bad: str) -> None:
    response = await client.get("/does-not-exist", headers={REQUEST_ID_HEADER: bad})

    assert response.headers[REQUEST_ID_HEADER] != bad
    assert len(response.headers[REQUEST_ID_HEADER]) == 32
