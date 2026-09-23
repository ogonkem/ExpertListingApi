from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from httpx import AsyncClient

from app.core.config import get_settings
from app.db.models import ListingType
from app.schemas.listing import ListingOut
from tests.integration.conftest import ListingFactory

pytestmark = pytest.mark.integration

SEARCH = "/api/v1/listings/search"

LEKKI = (6.4478, 3.4723)  # Lekki Phase 1, Lagos
VI = (6.4281, 3.4219)  # Victoria Island, Lagos
IKEJA = (6.6018, 3.3515)  # Ikeja, Lagos
WUSE = (9.0765, 7.4736)  # Wuse II, Abuja

# Independent oracle: great-circle (haversine, mean Earth radius 6371.0088 km) distances
# from LEKKI, computed outside PostGIS. PostGIS measures on the WGS84 spheroid, which
# differs from a sphere by well under 0.5% at these latitudes.
EXPECTED_KM_FROM_LEKKI = {"lekki": 0.0, "vi": 5.984, "ikeja": 21.710, "wuse": 528.912}
DISTANCE_TOLERANCE = 5e-3  # relative


def near(lat_lng: tuple[float, float], radius_km: float, **params: Any) -> dict[str, Any]:
    return {"lat": lat_lng[0], "lng": lat_lng[1], "radius_km": radius_km} | params


async def search(client: AsyncClient, params: dict[str, Any]) -> dict[str, Any]:
    response = await client.get(SEARCH, params=params)
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def titles(body: dict[str, Any]) -> list[str]:
    return [item["title"] for item in body["data"]]


@pytest.fixture
async def places(listing_factory: ListingFactory) -> dict[str, ListingOut]:
    """One listing in each of the four reference locations."""
    return {
        "lekki": await listing_factory(title="lekki", lat=LEKKI[0], lng=LEKKI[1]),
        "vi": await listing_factory(title="vi", lat=VI[0], lng=VI[1]),
        "ikeja": await listing_factory(title="ikeja", lat=IKEJA[0], lng=IKEJA[1]),
        "wuse": await listing_factory(title="wuse", lat=WUSE[0], lng=WUSE[1]),
    }


# --- routing -------------------------------------------------------------------------


async def test_search_route_is_not_captured_by_listing_id(client: AsyncClient) -> None:
    response = await client.get(SEARCH)

    assert response.status_code == 200
    assert response.json() == {
        "data": [],
        "meta": {"page": 1, "page_size": 20, "total": 0, "total_pages": 0},
    }


async def test_search_without_filters_returns_everything_newest_first(
    client: AsyncClient, listing_factory: ListingFactory
) -> None:
    base = datetime(2026, 9, 1, tzinfo=UTC)
    old = await listing_factory(title="old", created_at=base)
    new = await listing_factory(title="new", created_at=base + timedelta(days=1))

    body = await search(client, {})

    assert [item["id"] for item in body["data"]] == [str(new.id), str(old.id)]
    assert all("distance_km" not in item for item in body["data"])


# --- radius --------------------------------------------------------------------------


async def test_10km_from_lekki_returns_lekki_and_vi_only(
    client: AsyncClient, places: dict[str, ListingOut]
) -> None:
    body = await search(client, near(LEKKI, 10))

    assert sorted(titles(body)) == ["lekki", "vi"]
    assert body["meta"]["total"] == 2


@pytest.mark.parametrize(
    ("radius_km", "expected"),
    [
        (0.5, {"lekki"}),
        (5.9, {"lekki"}),  # VI is ~5.98 km away
        (6.1, {"lekki", "vi"}),
        (25, {"lekki", "vi", "ikeja"}),
        (100, {"lekki", "vi", "ikeja"}),  # Abuja is ~529 km away
    ],
)
async def test_radius_boundaries(
    client: AsyncClient, places: dict[str, ListingOut], radius_km: float, expected: set[str]
) -> None:
    body = await search(client, near(LEKKI, radius_km))

    assert set(titles(body)) == expected


async def test_radius_around_abuja_finds_only_wuse(
    client: AsyncClient, places: dict[str, ListingOut]
) -> None:
    body = await search(client, near(WUSE, 5))

    assert titles(body) == ["wuse"]
    assert body["data"][0]["distance_km"] == 0


async def test_sort_by_distance_orders_nearest_first(
    client: AsyncClient, places: dict[str, ListingOut]
) -> None:
    body = await search(client, near(LEKKI, 50, sort="distance"))

    assert titles(body) == ["lekki", "vi", "ikeja"]
    distances = [item["distance_km"] for item in body["data"]]
    assert distances == sorted(distances)


async def test_distance_km_matches_independent_calculation(
    client: AsyncClient, places: dict[str, ListingOut]
) -> None:
    body = await search(client, near(LEKKI, 50, sort="distance"))

    for item in body["data"]:
        expected = EXPECTED_KM_FROM_LEKKI[item["title"]]
        assert item["distance_km"] == pytest.approx(expected, rel=DISTANCE_TOLERANCE, abs=0.01)
        assert item["distance_km"] == round(item["distance_km"], 2)  # 2 dp


async def test_distance_included_whenever_a_point_is_given(
    client: AsyncClient, places: dict[str, ListingOut]
) -> None:
    body = await search(client, near(LEKKI, 50, sort="price_asc"))

    assert all("distance_km" in item for item in body["data"])


async def test_sort_distance_breaks_ties_by_id(
    client: AsyncClient, listing_factory: ListingFactory
) -> None:
    same_spot = [await listing_factory(lat=VI[0], lng=VI[1]) for _ in range(4)]

    body = await search(client, near(LEKKI, 10, sort="distance"))

    assert [item["id"] for item in body["data"]] == sorted(str(x.id) for x in same_spot)


# --- attribute filters and sorting ---------------------------------------------------


@pytest.fixture
async def portfolio(listing_factory: ListingFactory) -> dict[str, ListingOut]:
    """A mix of types, prices (naira) and bedroom counts, all in Lekki."""
    specs: list[tuple[str, ListingType, int, int]] = [
        ("rent-studio", ListingType.RENT, 1_200_000, 0),
        ("rent-2bed", ListingType.RENT, 3_500_000, 2),
        ("rent-3bed", ListingType.RENT, 6_000_000, 3),
        ("rent-4bed", ListingType.RENT, 9_000_000, 4),
        ("sale-3bed", ListingType.SALE, 150_000_000, 3),
        ("sale-5bed", ListingType.SALE, 450_000_000, 5),
        ("shortlet-1bed", ListingType.SHORTLET, 75_000, 1),
    ]
    return {
        title: await listing_factory(title=title, listing_type=kind, price=price, bedrooms=beds)
        for title, kind, price, beds in specs
    }


@pytest.mark.parametrize(
    ("params", "expected"),
    [
        ({"type": "rent"}, {"rent-studio", "rent-2bed", "rent-3bed", "rent-4bed"}),
        ({"type": "shortlet"}, {"shortlet-1bed"}),
        ({"min_price": 100_000_000}, {"sale-3bed", "sale-5bed"}),
        ({"max_price": 1_200_000}, {"rent-studio", "shortlet-1bed"}),  # inclusive
        ({"min_bedrooms": 4}, {"rent-4bed", "sale-5bed"}),
        ({"max_bedrooms": 0}, {"rent-studio"}),
        ({"min_bedrooms": 3, "max_bedrooms": 3}, {"rent-3bed", "sale-3bed"}),
    ],
)
async def test_single_filters(
    client: AsyncClient,
    portfolio: dict[str, ListingOut],
    params: dict[str, Any],
    expected: set[str],
) -> None:
    assert set(titles(await search(client, params))) == expected


async def test_combined_type_price_and_bedroom_filters(
    client: AsyncClient, portfolio: dict[str, ListingOut]
) -> None:
    body = await search(
        client,
        {
            "type": "rent",
            "min_price": 3_000_000,
            "max_price": 8_000_000,
            "min_bedrooms": 2,
            "max_bedrooms": 3,
        },
    )

    assert set(titles(body)) == {"rent-2bed", "rent-3bed"}
    assert body["meta"]["total"] == 2


async def test_filters_combine_with_radius(
    client: AsyncClient, listing_factory: ListingFactory
) -> None:
    await listing_factory(title="lekki-rent", listing_type=ListingType.RENT, bedrooms=3)
    await listing_factory(title="lekki-sale", listing_type=ListingType.SALE, bedrooms=3)
    await listing_factory(
        title="ikeja-rent", listing_type=ListingType.RENT, bedrooms=3, lat=IKEJA[0], lng=IKEJA[1]
    )

    body = await search(client, near(LEKKI, 10, type="rent", min_bedrooms=3))

    assert titles(body) == ["lekki-rent"]


async def test_price_filter_accepts_kobo_precision(
    client: AsyncClient, listing_factory: ListingFactory
) -> None:
    await listing_factory(title="exact", price=Decimal("3500000.50"))
    await listing_factory(title="kobo-less", price=Decimal("3500000.49"))

    body = await search(client, {"min_price": "3500000.50"})

    assert titles(body) == ["exact"]


async def test_sort_price_asc_and_desc(
    client: AsyncClient, portfolio: dict[str, ListingOut]
) -> None:
    asc = await search(client, {"type": "rent", "sort": "price_asc"})
    desc = await search(client, {"type": "rent", "sort": "price_desc"})

    assert titles(asc) == ["rent-studio", "rent-2bed", "rent-3bed", "rent-4bed"]
    assert titles(desc) == list(reversed(titles(asc)))


async def test_sort_price_breaks_ties_by_id(
    client: AsyncClient, listing_factory: ListingFactory
) -> None:
    tied = [await listing_factory(price=5_000_000) for _ in range(4)]
    expected = sorted(str(x.id) for x in tied)

    for sort in ("price_asc", "price_desc"):
        body = await search(client, {"sort": sort})
        assert [item["id"] for item in body["data"]] == expected


# --- pagination under filters ----------------------------------------------------------


async def test_pagination_totals_reflect_filters(
    client: AsyncClient, listing_factory: ListingFactory
) -> None:
    for i in range(7):
        await listing_factory(listing_type=ListingType.RENT, price=1_000_000 + i)
    for _ in range(3):
        await listing_factory(listing_type=ListingType.SALE, price=90_000_000)

    pages = [
        await search(client, {"type": "rent", "sort": "price_asc", "page": p, "page_size": 3})
        for p in (1, 2, 3)
    ]

    for page_number, body in enumerate(pages, start=1):
        assert body["meta"] == {
            "page": page_number,
            "page_size": 3,
            "total": 7,
            "total_pages": 3,
        }
    prices = [item["price"] for body in pages for item in body["data"]]
    assert prices == [1_000_000 + i for i in range(7)]  # no gaps, no repeats


async def test_page_past_the_end_keeps_filtered_total(
    client: AsyncClient, places: dict[str, ListingOut]
) -> None:
    body = await search(client, near(LEKKI, 10, page=4, page_size=1))

    assert body == {"data": [], "meta": {"page": 4, "page_size": 1, "total": 2, "total_pages": 2}}


async def test_distance_pagination_is_stable(
    client: AsyncClient, places: dict[str, ListingOut]
) -> None:
    first = await search(client, near(LEKKI, 50, sort="distance", page=1, page_size=2))
    second = await search(client, near(LEKKI, 50, sort="distance", page=2, page_size=2))

    assert titles(first) + titles(second) == ["lekki", "vi", "ikeja"]
    assert first["meta"]["total"] == second["meta"]["total"] == 3


# --- validation errors -----------------------------------------------------------------

MAX_RADIUS = get_settings().max_radius_km
MAX_PAGE_SIZE = get_settings().max_page_size


@pytest.mark.parametrize(
    ("params", "field", "message"),
    [
        # lat/lng/radius_km all-or-none
        ({"lat": 6.44}, "query", "must be provided together (missing: lng, radius_km)"),
        ({"lng": 3.47}, "query", "must be provided together (missing: lat, radius_km)"),
        ({"radius_km": 5}, "query", "must be provided together (missing: lat, lng)"),
        ({"lat": 6.44, "lng": 3.47}, "query", "must be provided together (missing: radius_km)"),
        ({"lat": 6.44, "radius_km": 5}, "query", "must be provided together (missing: lng)"),
        ({"lng": 3.47, "radius_km": 5}, "query", "must be provided together (missing: lat)"),
        # sort=distance needs a point
        ({"sort": "distance"}, "query", "sort=distance requires lat, lng and radius_km"),
        # ranges
        (
            {"min_price": 5_000_000, "max_price": 1_000_000},
            "query",
            "min_price must be less than or equal to max_price",
        ),
        (
            {"min_bedrooms": 4, "max_bedrooms": 2},
            "query",
            "min_bedrooms must be less than or equal to max_bedrooms",
        ),
        # field-level
        (near(LEKKI, 0), "query.radius_km", "greater than 0"),
        (near(LEKKI, -5), "query.radius_km", "greater than 0"),
        (near(LEKKI, MAX_RADIUS + 1), "query.radius_km", f"less than or equal to {MAX_RADIUS}"),
        (near((91, 3.47), 5), "query.lat", "less than or equal to 90"),
        (near((6.44, -181), 5), "query.lng", "greater than or equal to -180"),
        ({"type": "lease"}, "query.type", "Input should be 'rent', 'sale' or 'shortlet'"),
        ({"sort": "cheapest"}, "query.sort", "Input should be"),
        ({"min_price": -1}, "query.min_price", "greater than or equal to 0"),
        ({"max_price": "1.001"}, "query.max_price", "decimal places"),
        ({"min_bedrooms": -1}, "query.min_bedrooms", "greater than or equal to 0"),
        ({"max_bedrooms": 51}, "query.max_bedrooms", "less than or equal to 50"),
        ({"page": 0}, "query.page", "greater than or equal to 1"),
        ({"page_size": MAX_PAGE_SIZE + 1}, "query.page_size", "less than or equal to"),
        ({"bedrooms": 2}, "query.bedrooms", "Extra inputs are not permitted"),
    ],
)
async def test_validation_errors(
    client: AsyncClient, params: dict[str, Any], field: str, message: str
) -> None:
    response = await client.get(SEARCH, params=params)

    assert response.status_code == 422, response.text
    error = response.json()["error"]
    assert error["code"] == "validation_error"
    assert [d["field"] for d in error["details"]] == [field]
    assert message in error["details"][0]["message"]
