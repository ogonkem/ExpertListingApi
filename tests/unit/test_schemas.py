import json
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
from pydantic import ValidationError

from app.core.config import get_settings
from app.db.models import Listing, ListingType
from app.schemas.common import Paginated
from app.schemas.listing import (
    NON_NULLABLE_FIELDS,
    ListingCreate,
    ListingOut,
    ListingUpdate,
    LocationIn,
    SearchParams,
    SortOrder,
    kobo_to_naira,
    naira_to_kobo,
)

settings = get_settings()
AGENT_ID = uuid.UUID("7d3f0f3e-4b8f-4c1e-9d6a-2f3c1b5a9e01")


def create_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "title": "2 bedroom flat in Lekki Phase 1",
        "description": "Serviced, 24h power",
        "price": 3_500_000,
        "listing_type": "rent",
        "bedrooms": 2,
        "location": {"lat": 6.4474, "lng": 3.4746},
        "address": "12 Admiralty Way",
        "city": "Lagos",
        "agent_id": str(AGENT_ID),
    }
    return payload | overrides


def error_messages(exc: pytest.ExceptionInfo[ValidationError]) -> list[str]:
    return [err["msg"] for err in exc.value.errors()]


def error_locs(exc: pytest.ExceptionInfo[ValidationError]) -> list[tuple[Any, ...]]:
    return [tuple(err["loc"]) for err in exc.value.errors()]


# --- money helpers -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("naira", "kobo"),
    [
        (Decimal("0.01"), 1),
        (Decimal("1"), 100),
        (Decimal("3500000.50"), 350_000_050),
        (Decimal("9999999999999.99"), 999_999_999_999_999),
    ],
)
def test_naira_kobo_round_trip_is_exact(naira: Decimal, kobo: int) -> None:
    assert naira_to_kobo(naira) == kobo
    assert kobo_to_naira(kobo) == naira


# --- LocationIn ----------------------------------------------------------------------


@pytest.mark.parametrize(("lat", "lng"), [(-90, -180), (90, 180), (0, 0), (6.4474, 3.4746)])
def test_location_accepts_bounds(lat: float, lng: float) -> None:
    assert LocationIn(lat=lat, lng=lng).lat == lat


@pytest.mark.parametrize(
    ("lat", "lng", "field"),
    [(-90.0001, 0, "lat"), (90.0001, 0, "lat"), (0, -180.0001, "lng"), (0, 180.0001, "lng")],
)
def test_location_rejects_out_of_range(lat: float, lng: float, field: str) -> None:
    with pytest.raises(ValidationError) as exc:
        LocationIn(lat=lat, lng=lng)
    assert error_locs(exc) == [(field,)]


@pytest.mark.parametrize("value", [float("nan"), float("inf")])
def test_location_rejects_non_finite(value: float) -> None:
    with pytest.raises(ValidationError):
        LocationIn(lat=value, lng=0)


def test_location_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        LocationIn.model_validate({"lat": 1, "lng": 2, "alt": 3})


# --- ListingCreate -------------------------------------------------------------------


def test_create_valid_payload() -> None:
    listing = ListingCreate.model_validate(create_payload())

    assert listing.listing_type is ListingType.RENT
    assert listing.agent_id == AGENT_ID
    assert listing.price_kobo == 350_000_000


def test_create_optional_fields_default_to_none() -> None:
    payload = create_payload()
    for key in ("description", "address", "city"):
        del payload[key]

    listing = ListingCreate.model_validate(payload)

    assert (listing.description, listing.address, listing.city) == (None, None, None)


def test_create_title_is_stripped() -> None:
    assert ListingCreate.model_validate(create_payload(title="  Duplex  ")).title == "Duplex"


@pytest.mark.parametrize("title", ["ab", "   ab   ", "x" * 201, ""])
def test_create_rejects_bad_title_length(title: str) -> None:
    with pytest.raises(ValidationError) as exc:
        ListingCreate.model_validate(create_payload(title=title))
    assert error_locs(exc) == [("title",)]


def test_create_accepts_title_at_limits() -> None:
    assert ListingCreate.model_validate(create_payload(title="abc")).title == "abc"
    assert len(ListingCreate.model_validate(create_payload(title="x" * 200)).title) == 200


@pytest.mark.parametrize(
    ("price", "kobo"),
    [
        (3_500_000, 350_000_000),  # JSON integer
        ("3500000.50", 350_000_050),  # decimal string
        (1500.5, 150_050),  # JSON float with <= 2 dp
        (0.1, 10),  # classic float-representation trap
        ("0.01", 1),  # smallest positive amount
    ],
)
def test_create_price_accepts_naira_with_up_to_two_dp(price: Any, kobo: int) -> None:
    assert ListingCreate.model_validate(create_payload(price=price)).price_kobo == kobo


@pytest.mark.parametrize(
    "price",
    [
        0,
        -1,
        "-0.01",
        "1.005",  # three decimal places
        1.005,
        "12345678901234",  # 14 integer digits: exceeds max_digits
        "NaN",
        "Infinity",
        "abc",
    ],
)
def test_create_price_rejects_invalid(price: Any) -> None:
    with pytest.raises(ValidationError) as exc:
        ListingCreate.model_validate(create_payload(price=price))
    assert error_locs(exc) == [("price",)]


@pytest.mark.parametrize("listing_type", ["rent", "sale", "shortlet"])
def test_create_accepts_each_listing_type(listing_type: str) -> None:
    listing = ListingCreate.model_validate(create_payload(listing_type=listing_type))
    assert listing.listing_type == listing_type


@pytest.mark.parametrize("listing_type", ["lease", "RENT", ""])
def test_create_rejects_unknown_listing_type(listing_type: str) -> None:
    with pytest.raises(ValidationError) as exc:
        ListingCreate.model_validate(create_payload(listing_type=listing_type))
    assert error_locs(exc) == [("listing_type",)]


@pytest.mark.parametrize("bedrooms", [0, 1, 50])
def test_create_accepts_bedrooms_in_range(bedrooms: int) -> None:
    assert ListingCreate.model_validate(create_payload(bedrooms=bedrooms)).bedrooms == bedrooms


@pytest.mark.parametrize("bedrooms", [-1, 51, 2.5])
def test_create_rejects_bedrooms_out_of_range(bedrooms: Any) -> None:
    with pytest.raises(ValidationError) as exc:
        ListingCreate.model_validate(create_payload(bedrooms=bedrooms))
    assert error_locs(exc) == [("bedrooms",)]


def test_create_validates_nested_location() -> None:
    with pytest.raises(ValidationError) as exc:
        ListingCreate.model_validate(create_payload(location={"lat": 95, "lng": 3.4}))
    assert error_locs(exc) == [("location", "lat")]


@pytest.mark.parametrize(
    "field", ["title", "price", "listing_type", "bedrooms", "location", "agent_id"]
)
def test_create_requires_field(field: str) -> None:
    payload = create_payload()
    del payload[field]

    with pytest.raises(ValidationError) as exc:
        ListingCreate.model_validate(payload)
    assert error_locs(exc) == [(field,)]


def test_create_rejects_invalid_agent_id() -> None:
    with pytest.raises(ValidationError) as exc:
        ListingCreate.model_validate(create_payload(agent_id="not-a-uuid"))
    assert error_locs(exc) == [("agent_id",)]


@pytest.mark.parametrize(
    ("field", "value"),
    [("description", "x" * 5001), ("address", "x" * 301), ("city", "x" * 101), ("city", "  ")],
)
def test_create_rejects_bad_optional_text(field: str, value: str) -> None:
    with pytest.raises(ValidationError) as exc:
        ListingCreate.model_validate(create_payload(**{field: value}))
    assert error_locs(exc) == [(field,)]


def test_create_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError) as exc:
        ListingCreate.model_validate(create_payload(currency="USD"))
    assert error_locs(exc) == [("currency",)]


# --- ListingUpdate -------------------------------------------------------------------


def test_update_rejects_empty_body() -> None:
    with pytest.raises(ValidationError) as exc:
        ListingUpdate.model_validate({})
    assert error_messages(exc) == ["Value error, at least one field must be provided"]


def test_update_accepts_single_field_and_reports_only_supplied_changes() -> None:
    update = ListingUpdate.model_validate({"price": "4000000.25"})

    assert update.changes() == {"price": 400_000_025}


@pytest.mark.parametrize("field", NON_NULLABLE_FIELDS)
def test_update_rejects_explicit_null_on_required_column(field: str) -> None:
    with pytest.raises(ValidationError) as exc:
        ListingUpdate.model_validate({field: None})
    assert error_locs(exc) == [(field,)]
    assert error_messages(exc) == ["Value error, may not be null"]


@pytest.mark.parametrize("field", ["description", "address", "city"])
def test_update_allows_null_to_clear_nullable_column(field: str) -> None:
    assert ListingUpdate.model_validate({field: None}).changes() == {field: None}


def test_update_applies_same_constraints_as_create() -> None:
    with pytest.raises(ValidationError) as exc:
        ListingUpdate.model_validate({"title": "ab", "price": 0, "bedrooms": 51})
    assert sorted(error_locs(exc)) == [("bedrooms",), ("price",), ("title",)]


def test_update_keeps_location_out_of_column_changes() -> None:
    update = ListingUpdate.model_validate({"location": {"lat": 9.05, "lng": 7.49}, "bedrooms": 3})

    assert update.changes() == {"bedrooms": 3}
    assert update.location == LocationIn(lat=9.05, lng=7.49)


def test_update_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        ListingUpdate.model_validate({"colour": "blue"})


# --- ListingOut ----------------------------------------------------------------------


def make_listing(price_kobo: int = 350_000_000) -> Listing:
    now = datetime(2026, 9, 23, 12, 0, tzinfo=UTC)
    return Listing(
        id=uuid.uuid4(),
        title="2 bedroom flat",
        description=None,
        price=price_kobo,
        currency="NGN",
        listing_type=ListingType.RENT,
        bedrooms=2,
        address=None,
        city="Lagos",
        agent_id=AGENT_ID,
        created_at=now,
        updated_at=now,
    )


def test_out_converts_kobo_to_naira_json_number() -> None:
    whole = json.loads(ListingOut.from_model(make_listing(), lat=6.4, lng=3.4).model_dump_json())
    fractional = json.loads(
        ListingOut.from_model(make_listing(350_000_050), lat=6.4, lng=3.4).model_dump_json()
    )

    assert whole["price"] == 3_500_000
    assert isinstance(whole["price"], int)
    assert fractional["price"] == 3500000.5


def test_out_exposes_location_as_lat_lng() -> None:
    body = ListingOut.from_model(make_listing(), lat=6.4474, lng=3.4746).model_dump(mode="json")

    assert body["location"] == {"lat": 6.4474, "lng": 3.4746}


def test_out_omits_distance_when_absent() -> None:
    body = ListingOut.from_model(make_listing(), lat=6.4, lng=3.4).model_dump(mode="json")

    assert "distance_km" not in body
    assert {"id", "created_at", "updated_at", "currency"} <= body.keys()


def test_out_rounds_distance_to_two_dp() -> None:
    out = ListingOut.from_model(make_listing(), lat=6.4, lng=3.4, distance_km=1.23456)

    assert out.model_dump(mode="json")["distance_km"] == 1.23


# --- SearchParams --------------------------------------------------------------------


def test_search_defaults() -> None:
    params = SearchParams()

    assert (params.sort, params.page, params.page_size) == (SortOrder.NEWEST, 1, 20)
    assert (params.lat, params.lng, params.radius_km) == (None, None, None)
    assert params.offset == 0


def test_search_accepts_type_alias_and_field_name() -> None:
    assert SearchParams.model_validate({"type": "sale"}).listing_type is ListingType.SALE
    assert SearchParams.model_validate({"listing_type": "sale"}).listing_type is ListingType.SALE


def test_search_rejects_unknown_type() -> None:
    with pytest.raises(ValidationError):
        SearchParams.model_validate({"type": "lease"})


def test_search_accepts_full_geo_filter() -> None:
    params = SearchParams(lat=6.45, lng=3.47, radius_km=5)

    assert (params.lat, params.lng, params.radius_km) == (6.45, 3.47, 5)


@pytest.mark.parametrize(
    "geo",
    [
        {"lat": 6.45},
        {"lng": 3.47},
        {"radius_km": 5},
        {"lat": 6.45, "lng": 3.47},
        {"lat": 6.45, "radius_km": 5},
        {"lng": 3.47, "radius_km": 5},
    ],
)
def test_search_rejects_partial_geo_filter(geo: dict[str, float]) -> None:
    with pytest.raises(ValidationError) as exc:
        SearchParams.model_validate(geo)
    assert "must be provided together" in error_messages(exc)[0]


def test_search_sort_distance_requires_point() -> None:
    with pytest.raises(ValidationError) as exc:
        SearchParams(sort=SortOrder.DISTANCE)
    assert error_messages(exc) == ["Value error, sort=distance requires lat, lng and radius_km"]


def test_search_sort_distance_with_point_is_valid() -> None:
    params = SearchParams(sort=SortOrder.DISTANCE, lat=6.45, lng=3.47, radius_km=5)

    assert params.sort is SortOrder.DISTANCE


@pytest.mark.parametrize("sort", ["price_asc", "price_desc", "newest", "distance"])
def test_search_accepts_each_sort(sort: str) -> None:
    params = SearchParams.model_validate({"sort": sort, "lat": 1, "lng": 1, "radius_km": 1})
    assert params.sort == sort


def test_search_rejects_unknown_sort() -> None:
    with pytest.raises(ValidationError):
        SearchParams.model_validate({"sort": "cheapest"})


@pytest.mark.parametrize("radius_km", [0, -1, settings.max_radius_km + 0.1])
def test_search_rejects_radius_out_of_range(radius_km: float) -> None:
    with pytest.raises(ValidationError) as exc:
        SearchParams(lat=6.45, lng=3.47, radius_km=radius_km)
    assert ("radius_km",) in error_locs(exc)


def test_search_accepts_max_radius() -> None:
    assert SearchParams(lat=6.45, lng=3.47, radius_km=settings.max_radius_km).radius_km == 100


@pytest.mark.parametrize(("lat", "lng"), [(91, 3.47), (6.45, 181)])
def test_search_rejects_out_of_range_point(lat: float, lng: float) -> None:
    with pytest.raises(ValidationError):
        SearchParams(lat=lat, lng=lng, radius_km=5)


def test_search_rejects_min_price_above_max_price() -> None:
    with pytest.raises(ValidationError) as exc:
        SearchParams(min_price=Decimal(500), max_price=Decimal(100))
    assert error_messages(exc) == ["Value error, min_price must be less than or equal to max_price"]


def test_search_rejects_min_bedrooms_above_max_bedrooms() -> None:
    with pytest.raises(ValidationError) as exc:
        SearchParams(min_bedrooms=4, max_bedrooms=2)
    assert error_messages(exc) == [
        "Value error, min_bedrooms must be less than or equal to max_bedrooms"
    ]


def test_search_accepts_equal_min_and_max() -> None:
    params = SearchParams(
        min_price=Decimal(100), max_price=Decimal(100), min_bedrooms=2, max_bedrooms=2
    )
    assert params.min_price_kobo == params.max_price_kobo == 10_000


@pytest.mark.parametrize(
    "overrides",
    [
        {"min_price": -1},
        {"max_price": "1.001"},
        {"min_bedrooms": -1},
        {"max_bedrooms": 51},
    ],
)
def test_search_rejects_bad_filter_values(overrides: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        SearchParams.model_validate(overrides)


def test_search_converts_price_filters_to_kobo() -> None:
    params = SearchParams.model_validate({"min_price": "1000.50", "max_price": 2000})

    assert (params.min_price_kobo, params.max_price_kobo) == (100_050, 200_000)


@pytest.mark.parametrize(
    "overrides",
    [{"page": 0}, {"page_size": 0}, {"page_size": settings.max_page_size + 1}],
)
def test_search_rejects_bad_pagination(overrides: dict[str, int]) -> None:
    with pytest.raises(ValidationError):
        SearchParams.model_validate(overrides)


def test_search_pagination_limits_and_offset() -> None:
    params = SearchParams(page=3, page_size=settings.max_page_size)

    assert params.offset == 2 * settings.max_page_size


def test_search_rejects_unknown_params() -> None:
    with pytest.raises(ValidationError):
        SearchParams.model_validate({"min_bedroom": 2})


# --- Paginated -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("total", "page_size", "total_pages"),
    [(0, 20, 0), (1, 20, 1), (40, 20, 2), (41, 20, 3)],
)
def test_paginated_total_pages(total: int, page_size: int, total_pages: int) -> None:
    page = Paginated[int].build([], total=total, page=1, page_size=page_size)

    assert page.meta.total_pages == total_pages


def test_paginated_envelope_shape() -> None:
    out = ListingOut.from_model(make_listing(), lat=6.4, lng=3.4)
    body = Paginated[ListingOut].build([out], total=1, page=1, page_size=20).model_dump(mode="json")

    assert set(body) == {"data", "meta"}
    assert body["meta"] == {"page": 1, "page_size": 20, "total": 1, "total_pages": 1}
    assert body["data"][0]["price"] == 3_500_000
