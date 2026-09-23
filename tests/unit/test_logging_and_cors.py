import json
import logging
import sys
from collections.abc import AsyncIterator

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.core.config import Settings
from app.core.logging_config import JsonFormatter, RequestIdFilter
from app.core.request_id import REQUEST_ID_HEADER
from app.main import create_app


def make_record(**extra: object) -> logging.LogRecord:
    record = logging.LogRecord("app.test", logging.INFO, __file__, 1, "hello %s", ("world",), None)
    for key, value in extra.items():
        setattr(record, key, value)
    return record


def test_json_formatter_emits_one_structured_object() -> None:
    record = make_record(request_id="req-1", duration_ms=12.5)

    payload = json.loads(JsonFormatter(env="test").format(record))

    assert payload["message"] == "hello world"
    assert payload["level"] == "INFO"
    assert payload["logger"] == "app.test"
    assert payload["request_id"] == "req-1"
    assert payload["env"] == "test"
    assert payload["duration_ms"] == 12.5  # `extra=` fields are included
    assert payload["timestamp"].endswith("+00:00")
    assert "args" not in payload and "msg" not in payload  # no LogRecord internals


def test_json_formatter_drops_uvicorn_colour_duplicate() -> None:
    record = make_record(color_message="[1mhello[0m")

    assert "color_message" not in json.loads(JsonFormatter(env="test").format(record))


def test_json_formatter_includes_exception() -> None:
    try:
        raise ValueError("boom")
    except ValueError:
        record = logging.LogRecord(
            "app.test", logging.ERROR, __file__, 1, "failed", None, exc_info=sys.exc_info()
        )

    payload = json.loads(JsonFormatter(env="test").format(record))

    assert "ValueError: boom" in payload["exception"]


def test_request_id_filter_defaults_outside_a_request() -> None:
    record = make_record()

    RequestIdFilter().filter(record)

    assert record.request_id == "-"  # type: ignore[attr-defined]


async def test_access_log_records_request_with_id(
    client: AsyncClient, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO, logger="http.access"):
        await client.get("/does-not-exist?x=1", headers={REQUEST_ID_HEADER: "trace-9"})

    record = next(r for r in caplog.records if r.name == "http.access")
    assert record.getMessage() == "GET /does-not-exist 404"
    assert record.status == 404  # type: ignore[attr-defined]
    assert record.path == "/does-not-exist"  # type: ignore[attr-defined]
    assert record.query == "x=1"  # type: ignore[attr-defined]
    assert record.duration_ms >= 0  # type: ignore[attr-defined]
    assert RequestIdFilter().filter(record) and record.request_id == "trace-9"  # type: ignore[attr-defined]


# --- CORS ------------------------------------------------------------------------------


@pytest.fixture
async def cors_client() -> AsyncIterator[AsyncClient]:
    app: FastAPI = create_app(Settings(cors_origins=["https://app.expertlisting.ng"]))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


async def test_cors_preflight_allows_configured_origin(cors_client: AsyncClient) -> None:
    response = await cors_client.options(
        "/api/v1/listings",
        headers={
            "Origin": "https://app.expertlisting.ng",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "https://app.expertlisting.ng"
    assert "POST" in response.headers["access-control-allow-methods"]
    assert REQUEST_ID_HEADER in response.headers  # request-context wraps CORS


async def test_cors_exposes_location_and_request_id(cors_client: AsyncClient) -> None:
    response = await cors_client.get(
        "/does-not-exist", headers={"Origin": "https://app.expertlisting.ng"}
    )

    exposed = response.headers["access-control-expose-headers"].lower()
    assert "location" in exposed and "x-request-id" in exposed


async def test_cors_rejects_unlisted_origin(cors_client: AsyncClient) -> None:
    response = await cors_client.options(
        "/api/v1/listings",
        headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "GET"},
    )

    assert "access-control-allow-origin" not in response.headers


async def test_cors_disabled_when_no_origins_configured() -> None:
    app = create_app(Settings(cors_origins=[]))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.get("/does-not-exist", headers={"Origin": "https://anything.example"})

    assert "access-control-allow-origin" not in response.headers


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("https://a.ng, http://localhost:3000/", ["https://a.ng", "http://localhost:3000"]),
        ("*", ["*"]),
        ("", []),
    ],
)
def test_cors_origins_parse_from_comma_separated_env(
    monkeypatch: pytest.MonkeyPatch, raw: str, expected: list[str]
) -> None:
    monkeypatch.setenv("CORS_ORIGINS", raw)

    assert Settings(_env_file=None).cors_origins == expected
