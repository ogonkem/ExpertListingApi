import math
import random
from collections import Counter

import pytest

from app.db.models import ListingType
from scripts.seed import AGENT_IDS, NEIGHBOURHOODS, SeedListing, generate_listings, parse_args


@pytest.fixture(scope="module")
def listings() -> list[SeedListing]:
    return generate_listings(1000, random.Random(42))


def test_is_deterministic_for_a_seed() -> None:
    first = generate_listings(50, random.Random(7))
    second = generate_listings(50, random.Random(7))

    assert [(x.id, x.data, x.created_at) for x in first] == [
        (x.id, x.data, x.created_at) for x in second
    ]
    assert first[0].id != generate_listings(1, random.Random(8))[0].id


def test_ids_are_unique(listings: list[SeedListing]) -> None:
    assert len({x.id for x in listings}) == len(listings)


def test_covers_every_neighbourhood_type_and_a_small_agent_pool(
    listings: list[SeedListing],
) -> None:
    addresses = " ".join(x.data.address or "" for x in listings)

    assert all(hood.name in addresses for hood in NEIGHBOURHOODS)
    assert {x.data.listing_type for x in listings} == set(ListingType)
    assert {x.data.city for x in listings} == {"Lagos", "Abuja"}
    assert {x.data.agent_id for x in listings} <= set(AGENT_IDS)
    assert len(AGENT_IDS) <= 20


def test_points_are_jittered_near_their_neighbourhood(
    listings: list[SeedListing],
) -> None:
    by_name = {hood.name: hood for hood in NEIGHBOURHOODS}
    for item in listings:
        assert item.data.address is not None
        hood = by_name[item.data.address.rsplit(", ", 1)[1]]
        # ~0.9 km sigma of jitter; 6 sigma is still well inside the district.
        assert math.isclose(item.data.location.lat, hood.lat, abs_tol=0.05)
        assert math.isclose(item.data.location.lng, hood.lng, abs_tol=0.05)
    assert len({(x.data.location.lat, x.data.location.lng) for x in listings}) == len(listings)


@pytest.mark.parametrize(
    ("kind", "low", "high"),
    [
        (ListingType.RENT, 200_000, 60_000_000),  # per year
        (ListingType.SALE, 20_000_000, 2_000_000_000),
        (ListingType.SHORTLET, 10_000, 1_000_000),  # per night
    ],
)
def test_prices_are_plausible_per_type(
    listings: list[SeedListing],
    kind: ListingType,
    low: int,
    high: int,
) -> None:
    prices = [x.data.price for x in listings if x.data.listing_type is kind]

    assert prices
    assert all(low <= p <= high for p in prices)
    assert all(p == p.to_integral_value() for p in prices)  # whole naira


def test_titles_follow_nigerian_listing_style(
    listings: list[SeedListing],
) -> None:
    titles = [x.data.title for x in listings]

    assert any("Bedroom Terrace Duplex with BQ" in t for t in titles)
    assert any(t.startswith(("Self Contain", "Serviced Self Contain")) for t in titles)
    for item in listings:
        if item.data.bedrooms > 1:
            assert f"{item.data.bedrooms} Bedroom" in item.data.title


def test_bedroom_mix_includes_studios(listings: list[SeedListing]) -> None:
    counts = Counter(x.data.bedrooms for x in listings)

    assert counts[0] > 0
    assert max(counts) <= 7


def test_created_at_spread_over_ninety_days(listings: list[SeedListing]) -> None:
    spread = max(x.created_at for x in listings) - min(x.created_at for x in listings)

    assert spread.days >= 80


def test_parse_args_defaults_and_validation() -> None:
    args = parse_args([])
    assert (args.count, args.reset, args.seed) == (500, False, 42)

    assert parse_args(["--count", "20", "--reset"]).reset is True
    with pytest.raises(SystemExit):
        parse_args(["--count", "0"])
