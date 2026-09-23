"""Seed the database with realistic Lagos and Abuja listings.

    python -m scripts.seed                   # add 500 listings
    python -m scripts.seed --count 2000 --reset
    make seed ARGS="--count 2000 --reset"    # inside the compose api container

Generation is deterministic for a given --seed, so `--reset` always reproduces the same
dataset (including ids). Without --reset, rows are appended: the current row count is
mixed into the seed so each append is a fresh batch with new ids (re-running the same
command never collides with rows it created before).
"""

import argparse
import asyncio
import logging
import random
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.db.models import ListingType
from app.repositories.listing_repo import ListingRepository
from app.schemas.listing import ListingCreate, LocationIn

logger = logging.getLogger("scripts.seed")


@dataclass(frozen=True)
class Neighbourhood:
    name: str
    city: str
    lat: float
    lng: float
    streets: tuple[str, ...]
    # Relative price level: 1.0 ~ mid-market Lagos mainland.
    tier: float
    weight: int  # relative share of generated listings


NEIGHBOURHOODS: tuple[Neighbourhood, ...] = (
    # Lagos
    Neighbourhood(
        "Lekki Phase 1",
        "Lagos",
        6.4478,
        3.4723,
        ("Admiralty Way", "Freedom Way", "Fola Osibo Road", "Durosimi Etti Drive"),
        2.6,
        16,
    ),
    Neighbourhood(
        "Ikoyi",
        "Lagos",
        6.4549,
        3.4336,
        ("Bourdillon Road", "Glover Road", "Kingsway Road", "Alexander Avenue"),
        4.2,
        10,
    ),
    Neighbourhood(
        "Victoria Island",
        "Lagos",
        6.4281,
        3.4219,
        ("Adeola Odeku Street", "Ahmadu Bello Way", "Akin Adesola Street", "Ajose Adeogun"),
        3.6,
        10,
    ),
    Neighbourhood(
        "Yaba",
        "Lagos",
        6.5095,
        3.3711,
        ("Herbert Macaulay Way", "Commercial Avenue", "Alagomeji Street", "Tejuosho Road"),
        0.9,
        8,
    ),
    Neighbourhood(
        "Ikeja",
        "Lagos",
        6.6018,
        3.3515,
        ("Allen Avenue", "Opebi Road", "Obafemi Awolowo Way", "Toyin Street"),
        1.3,
        10,
    ),
    Neighbourhood(
        "Ajah",
        "Lagos",
        6.4698,
        3.5852,
        ("Addo Road", "Badore Road", "Abraham Adesanya Road", "Thomas Estate Road"),
        0.8,
        9,
    ),
    Neighbourhood(
        "Surulere",
        "Lagos",
        6.5000,
        3.3500,
        ("Adeniran Ogunsanya Street", "Bode Thomas Street", "Ojuelegba Road", "Akerele Street"),
        0.9,
        7,
    ),
    # Abuja
    Neighbourhood(
        "Maitama",
        "Abuja",
        9.0882,
        7.4934,
        ("Aguiyi Ironsi Street", "Gana Street", "IBB Way", "Mississippi Street"),
        3.4,
        8,
    ),
    Neighbourhood(
        "Wuse II",
        "Abuja",
        9.0765,
        7.4736,
        (
            "Aminu Kano Crescent",
            "Adetokunbo Ademola Crescent",
            "Libreville Street",
            "Sokode Crescent",
        ),
        2.2,
        9,
    ),
    Neighbourhood(
        "Gwarinpa",
        "Abuja",
        9.1072,
        7.4053,
        ("1st Avenue", "3rd Avenue", "5th Avenue", "69 Road"),
        1.1,
        7,
    ),
    Neighbourhood(
        "Jabi",
        "Abuja",
        9.0674,
        7.4217,
        ("Obafemi Awolowo Way", "Jabi Lake Road", "Ebitu Ukiwe Street"),
        1.8,
        6,
    ),
)

# A small pool of agents; uuid5 keeps them stable across runs and machines.
AGENT_IDS: tuple[uuid.UUID, ...] = tuple(
    uuid.uuid5(uuid.NAMESPACE_URL, f"https://expertlisting.example/agents/{n}") for n in range(12)
)

# (label, min_bedrooms, max_bedrooms)
PROPERTY_KINDS: dict[ListingType, tuple[tuple[str, int, int], ...]] = {
    ListingType.RENT: (
        ("Self Contain", 0, 0),
        ("Mini Flat", 1, 1),
        ("Flat", 2, 3),
        ("Terrace Duplex", 3, 4),
        ("Semi-Detached Duplex", 3, 5),
        ("Bungalow", 2, 4),
    ),
    ListingType.SALE: (
        ("Flat", 2, 4),
        ("Terrace Duplex", 3, 5),
        ("Semi-Detached Duplex", 4, 5),
        ("Fully Detached Duplex", 4, 7),
        ("Penthouse", 3, 5),
        ("Bungalow", 3, 4),
    ),
    ListingType.SHORTLET: (
        ("Studio Apartment", 0, 0),
        ("Apartment", 1, 3),
        ("Penthouse", 2, 4),
    ),
}

PREFIXES: dict[ListingType, tuple[str, ...]] = {
    ListingType.RENT: ("", "", "Newly Built ", "Serviced ", "Tastefully Finished "),
    ListingType.SALE: ("", "Newly Built ", "Luxury ", "Contemporary "),
    ListingType.SHORTLET: ("Fully Furnished ", "Luxury ", "Cosy ", ""),
}

SELLING_POINTS = (
    "24-hour power supply",
    "Ample parking space",
    "Treated borehole water",
    "Gated estate with security",
    "Fitted kitchen",
    "Walk-in closet in the master bedroom",
    "Close to major roads",
    "Swimming pool and gym",
    "All rooms en suite",
    "Good road network",
)

TYPE_WEIGHTS = {ListingType.RENT: 55, ListingType.SALE: 30, ListingType.SHORTLET: 15}


def _round_to(value: float, step: int) -> Decimal:
    return Decimal(max(step, round(value / step) * step))


def price_for(kind: ListingType, bedrooms: int, tier: float, rng: random.Random) -> Decimal:
    """A plausible naira price.

    rent: per year; sale: outright; shortlet: per night.
    """
    rooms = max(bedrooms, 0.6)  # a self contain/studio is priced like ~0.6 of a bedroom
    noise = rng.uniform(0.8, 1.25)
    match kind:
        case ListingType.RENT:
            return _round_to(900_000 * rooms * tier * noise, 50_000)
        case ListingType.SALE:
            return _round_to(28_000_000 * rooms * tier * noise, 1_000_000)
        case ListingType.SHORTLET:
            return _round_to(22_000 * (rooms + 0.5) * tier * noise, 5_000)


def title_for(kind: ListingType, label: str, bedrooms: int, rng: random.Random) -> str:
    prefix = rng.choice(PREFIXES[kind])
    # "Self Contain", "Mini Flat" and studios are named without a bedroom count.
    core = label if bedrooms == 0 or label == "Mini Flat" else f"{bedrooms} Bedroom {label}"
    suffix = ""
    if "Duplex" in label and bedrooms >= 3 and rng.random() < 0.6:
        suffix = " with BQ"
    return f"{prefix}{core}{suffix}"


@dataclass(frozen=True)
class SeedListing:
    id: uuid.UUID
    data: ListingCreate
    created_at: datetime


def generate_listings(
    count: int, rng: random.Random, *, now: datetime | None = None
) -> list[SeedListing]:
    """Deterministic for a given rng state. Every row is validated by ListingCreate."""
    now = now or datetime(2026, 9, 1, tzinfo=UTC)
    kinds = list(TYPE_WEIGHTS)
    listings = []
    for _ in range(count):
        hood = rng.choices(NEIGHBOURHOODS, weights=[n.weight for n in NEIGHBOURHOODS])[0]
        kind = rng.choices(kinds, weights=[TYPE_WEIGHTS[k] for k in kinds])[0]
        label, lo, hi = rng.choice(PROPERTY_KINDS[kind])
        bedrooms = rng.randint(lo, hi)
        # ~0.9 km (1 sigma) of jitter around the neighbourhood centroid.
        lat = round(hood.lat + rng.gauss(0, 0.008), 6)
        lng = round(hood.lng + rng.gauss(0, 0.008), 6)
        points = rng.sample(SELLING_POINTS, k=3)
        data = ListingCreate(
            title=f"{title_for(kind, label, bedrooms, rng)} in {hood.name}",
            description=". ".join(points) + ".",
            price=price_for(kind, bedrooms, hood.tier, rng),
            listing_type=kind,
            bedrooms=bedrooms,
            location=LocationIn(lat=lat, lng=lng),
            address=f"{rng.randint(1, 120)} {rng.choice(hood.streets)}, {hood.name}",
            city=hood.city,
            agent_id=rng.choice(AGENT_IDS),
        )
        listings.append(
            SeedListing(
                id=uuid.UUID(int=rng.getrandbits(128), version=4),
                data=data,
                created_at=now - timedelta(minutes=rng.randint(0, 90 * 24 * 60)),
            )
        )
    return listings


def _row(item: SeedListing) -> tuple[dict[str, Any], float, float]:
    values = item.data.model_dump(exclude={"price", "location"})
    values |= {
        "id": item.id,
        "price": item.data.price_kobo,
        "created_at": item.created_at,
        "updated_at": item.created_at,
    }
    return values, item.data.location.lat, item.data.location.lng


async def seed(session: AsyncSession, *, count: int, reset: bool, rng_seed: int) -> int:
    repo = ListingRepository(session)
    if reset:
        deleted = await repo.delete_all()
        logger.info("Deleted %d existing listings", deleted)
        rng = random.Random(rng_seed)
    else:
        existing = await repo.count()
        rng = random.Random(f"{rng_seed}:{existing}") if existing else random.Random(rng_seed)
    listings = generate_listings(count, rng)
    inserted = await repo.bulk_create([_row(item) for item in listings])
    await session.commit()
    return inserted


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--count", type=int, default=500, help="listings to create (default 500)")
    parser.add_argument(
        "--reset", action="store_true", help="delete all listings first (repeatable result)"
    )
    parser.add_argument("--seed", type=int, default=42, help="random seed (default 42)")
    parser.add_argument("--database-url", help="override DATABASE_URL")
    args = parser.parse_args(argv)
    if args.count < 1:
        parser.error("--count must be at least 1")
    return args


async def main_async(args: argparse.Namespace) -> None:
    engine = create_async_engine(args.database_url or get_settings().database_url)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            inserted = await seed(session, count=args.count, reset=args.reset, rng_seed=args.seed)
        logger.info("Seeded %d listings (reset=%s, seed=%d)", inserted, args.reset, args.seed)
    finally:
        await engine.dispose()


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    asyncio.run(main_async(parse_args(argv)))


if __name__ == "__main__":
    main()
