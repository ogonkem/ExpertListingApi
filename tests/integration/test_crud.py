import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from tests.integration.conftest import DEFAULT_AGENT_ID, ListingFactory

pytestmark = pytest.mark.integration

BASE = "/api/v1/listings"


def create_body(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "title": "Serviced 3 bedroom flat in Lekki Phase 1",
        "description": "24h power, pool and gym.",
        "price": 8_500_000,
        "listing_type": "rent",
        "bedrooms": 3,
        # Deliberately asymmetric so a lat/lng swap cannot go unnoticed.
        "location": {"lat": 6.4474, "lng": 3.4746},
        "address": "15 Admiralty Way, Lekki Phase 1",
        "city": "Lagos",
        "agent_id": str(DEFAULT_AGENT_ID),
    }
    return body | overrides


def assert_error(response: Any, status: int, code: str) -> dict[str, Any]:
    assert response.status_code == status, response.text
    body = response.json()
    assert set(body) == {"error"}
    assert body["error"]["code"] == code
    error: dict[str, Any] = body["error"]
    return error


def fields(error: dict[str, Any]) -> list[str]:
    return [d["field"] for d in error["details"]]


async def raw_row(session: AsyncSession, listing_id: str) -> Any:
    return (
        await session.execute(
            text(
                "SELECT price, ST_X(location::geometry) AS x, ST_Y(location::geometry) AS y "
                "FROM listings WHERE id = :id"
            ),
            {"id": listing_id},
        )
    ).one()


# --- POST ----------------------------------------------------------------------------


async def test_create_returns_201_body_and_location_header(client: AsyncClient) -> None:
    response = await client.post(BASE, json=create_body())

    assert response.status_code == 201, response.text
    body = response.json()
    assert response.headers["Location"] == f"http://test{BASE}/{body['id']}"
    assert body["title"] == "Serviced 3 bedroom flat in Lekki Phase 1"
    assert body["price"] == 8_500_000
    assert body["currency"] == "NGN"
    assert body["listing_type"] == "rent"
    assert body["location"] == {"lat": 6.4474, "lng": 3.4746}
    assert body["agent_id"] == str(DEFAULT_AGENT_ID)
    assert body["created_at"] == body["updated_at"]
    assert "distance_km" not in body


async def test_created_listing_is_retrievable_via_location_header(client: AsyncClient) -> None:
    created = await client.post(BASE, json=create_body())

    fetched = await client.get(created.headers["Location"])

    assert fetched.status_code == 200
    assert fetched.json() == created.json()


async def test_create_stores_kobo_and_lng_lat_order(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    response = await client.post(BASE, json=create_body(price="65000.50"))

    row = await raw_row(db_session, response.json()["id"])
    assert row.price == 6_500_050  # kobo
    assert (row.x, row.y) == pytest.approx((3.4746, 6.4474))  # x = lng, y = lat
    assert response.json()["price"] == 65000.5


async def test_create_studio_with_zero_bedrooms(client: AsyncClient) -> None:
    response = await client.post(BASE, json=create_body(bedrooms=0, listing_type="shortlet"))

    assert response.status_code == 201
    assert response.json()["bedrooms"] == 0


@pytest.mark.parametrize(
    ("overrides", "field"),
    [
        ({"price": 0}, "body.price"),
        ({"price": "1.005"}, "body.price"),
        ({"bedrooms": -1}, "body.bedrooms"),
        ({"listing_type": "lease"}, "body.listing_type"),
        ({"location": {"lat": 91, "lng": 3.4}}, "body.location.lat"),
        ({"title": "ab"}, "body.title"),
        ({"agent_id": "nope"}, "body.agent_id"),
        ({"currency": "USD"}, "body.currency"),
    ],
)
async def test_create_invalid_body_returns_422_envelope(
    client: AsyncClient, overrides: dict[str, Any], field: str
) -> None:
    response = await client.post(BASE, json=create_body(**overrides))

    error = assert_error(response, 422, "validation_error")
    assert fields(error) == [field]


async def test_create_missing_required_fields(client: AsyncClient) -> None:
    response = await client.post(BASE, json={"title": "Just a title"})

    error = assert_error(response, 422, "validation_error")
    assert set(fields(error)) == {
        "body.price",
        "body.listing_type",
        "body.bedrooms",
        "body.location",
        "body.agent_id",
    }


async def test_create_rejects_malformed_json(client: AsyncClient) -> None:
    response = await client.post(
        BASE, content=b"{not json", headers={"Content-Type": "application/json"}
    )

    assert_error(response, 422, "validation_error")


# --- GET /{id} -----------------------------------------------------------------------


async def test_get_returns_listing(client: AsyncClient, listing_factory: ListingFactory) -> None:
    listing = await listing_factory(title="Terrace duplex in Ajah", lat=6.4698, lng=3.5852)

    response = await client.get(f"{BASE}/{listing.id}")

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == str(listing.id)
    assert body["title"] == "Terrace duplex in Ajah"
    assert body["location"] == {"lat": 6.4698, "lng": 3.5852}


async def test_get_unknown_id_returns_404_envelope(client: AsyncClient) -> None:
    missing = uuid.uuid4()

    response = await client.get(f"{BASE}/{missing}")

    error = assert_error(response, 404, "not_found")
    assert error == {
        "code": "not_found",
        "message": "Listing not found",
        "details": {"id": str(missing)},
    }


@pytest.mark.parametrize("method", ["GET", "PATCH", "DELETE"])
async def test_invalid_uuid_in_path_returns_422_not_500(client: AsyncClient, method: str) -> None:
    response = await client.request(method, f"{BASE}/not-a-uuid", json={"price": 1})

    error = assert_error(response, 422, "validation_error")
    assert fields(error) == ["path.listing_id"]


# --- GET (list) ----------------------------------------------------------------------


async def test_list_empty(client: AsyncClient) -> None:
    response = await client.get(BASE)

    assert response.status_code == 200
    assert response.json() == {
        "data": [],
        "meta": {"page": 1, "page_size": 20, "total": 0, "total_pages": 0},
    }


async def test_list_is_newest_first(client: AsyncClient, listing_factory: ListingFactory) -> None:
    base = datetime(2026, 9, 1, tzinfo=UTC)
    oldest = await listing_factory(created_at=base)
    newest = await listing_factory(created_at=base + timedelta(days=2))
    middle = await listing_factory(created_at=base + timedelta(days=1))

    response = await client.get(BASE)

    ids = [item["id"] for item in response.json()["data"]]
    assert ids == [str(newest.id), str(middle.id), str(oldest.id)]


async def test_list_pagination_meta_and_pages(
    client: AsyncClient, listing_factory: ListingFactory
) -> None:
    base = datetime(2026, 9, 1, tzinfo=UTC)
    created = [await listing_factory(created_at=base + timedelta(hours=i)) for i in range(5)]
    expected_order = [str(listing.id) for listing in reversed(created)]

    pages = []
    for page in (1, 2, 3):
        response = await client.get(BASE, params={"page": page, "page_size": 2})
        assert response.status_code == 200
        body = response.json()
        assert body["meta"] == {"page": page, "page_size": 2, "total": 5, "total_pages": 3}
        pages.append([item["id"] for item in body["data"]])

    assert [len(p) for p in pages] == [2, 2, 1]
    assert [i for p in pages for i in p] == expected_order


async def test_list_page_past_the_end_is_empty_with_meta(
    client: AsyncClient, listing_factory: ListingFactory
) -> None:
    await listing_factory()

    response = await client.get(BASE, params={"page": 5})

    assert response.json() == {
        "data": [],
        "meta": {"page": 5, "page_size": 20, "total": 1, "total_pages": 1},
    }


@pytest.mark.parametrize(
    ("params", "field"),
    [
        ({"page": 0}, "query.page"),
        ({"page_size": 0}, "query.page_size"),
        ({"page_size": 101}, "query.page_size"),
        ({"page": "two"}, "query.page"),
        ({"pagesize": 10}, "query.pagesize"),
    ],
)
async def test_list_invalid_pagination_returns_422(
    client: AsyncClient, params: dict[str, Any], field: str
) -> None:
    response = await client.get(BASE, params=params)

    error = assert_error(response, 422, "validation_error")
    assert fields(error) == [field]


# --- PATCH ---------------------------------------------------------------------------


async def test_patch_updates_only_supplied_fields(
    client: AsyncClient, listing_factory: ListingFactory, db_session: AsyncSession
) -> None:
    listing = await listing_factory()
    before = (await client.get(f"{BASE}/{listing.id}")).json()

    response = await client.patch(f"{BASE}/{listing.id}", json={"price": "7800000.25"})

    assert response.status_code == 200, response.text
    after = response.json()
    assert after["price"] == 7800000.25
    assert {k: v for k, v in after.items() if k not in {"price", "updated_at"}} == {
        k: v for k, v in before.items() if k not in {"price", "updated_at"}
    }
    assert (await raw_row(db_session, str(listing.id))).price == 780_000_025


async def test_patch_moves_location(
    client: AsyncClient, listing_factory: ListingFactory, db_session: AsyncSession
) -> None:
    listing = await listing_factory()

    response = await client.patch(
        f"{BASE}/{listing.id}",
        json={"location": {"lat": 9.0579, "lng": 7.4951}, "city": "Abuja"},
    )

    assert response.status_code == 200
    assert response.json()["location"] == {"lat": 9.0579, "lng": 7.4951}
    assert response.json()["city"] == "Abuja"
    row = await raw_row(db_session, str(listing.id))
    assert (row.x, row.y) == pytest.approx((7.4951, 9.0579))


async def test_patch_null_clears_nullable_field(
    client: AsyncClient, listing_factory: ListingFactory
) -> None:
    listing = await listing_factory(description="Will be removed")

    response = await client.patch(f"{BASE}/{listing.id}", json={"description": None})

    assert response.status_code == 200
    assert response.json()["description"] is None


@pytest.mark.parametrize("field", ["title", "price", "listing_type", "bedrooms", "location"])
async def test_patch_null_on_required_field_returns_422(
    client: AsyncClient, listing_factory: ListingFactory, field: str
) -> None:
    listing = await listing_factory()

    response = await client.patch(f"{BASE}/{listing.id}", json={field: None})

    error = assert_error(response, 422, "validation_error")
    assert error["details"] == [{"field": f"body.{field}", "message": "may not be null"}]


async def test_patch_empty_body_returns_422(
    client: AsyncClient, listing_factory: ListingFactory
) -> None:
    listing = await listing_factory()

    response = await client.patch(f"{BASE}/{listing.id}", json={})

    error = assert_error(response, 422, "validation_error")
    assert error["details"] == [{"field": "body", "message": "at least one field must be provided"}]


async def test_patch_invalid_value_returns_422(
    client: AsyncClient, listing_factory: ListingFactory
) -> None:
    listing = await listing_factory()

    response = await client.patch(f"{BASE}/{listing.id}", json={"bedrooms": 51})

    error = assert_error(response, 422, "validation_error")
    assert fields(error) == ["body.bedrooms"]


async def test_patch_unknown_id_returns_404(client: AsyncClient) -> None:
    response = await client.patch(f"{BASE}/{uuid.uuid4()}", json={"price": 100})

    assert_error(response, 404, "not_found")


# --- DELETE --------------------------------------------------------------------------


async def test_delete_returns_204_and_removes_listing(
    client: AsyncClient, listing_factory: ListingFactory
) -> None:
    listing = await listing_factory()

    response = await client.delete(f"{BASE}/{listing.id}")

    assert response.status_code == 204
    assert response.content == b""
    assert_error(await client.get(f"{BASE}/{listing.id}"), 404, "not_found")


async def test_delete_unknown_id_returns_404(client: AsyncClient) -> None:
    response = await client.delete(f"{BASE}/{uuid.uuid4()}")

    assert_error(response, 404, "not_found")


async def test_delete_twice_returns_404_second_time(
    client: AsyncClient, listing_factory: ListingFactory
) -> None:
    listing = await listing_factory()

    assert (await client.delete(f"{BASE}/{listing.id}")).status_code == 204
    assert_error(await client.delete(f"{BASE}/{listing.id}"), 404, "not_found")
