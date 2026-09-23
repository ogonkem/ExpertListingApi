# ExpertListingApi

Take-home for Expert Listing (Nigerian proptech). A Property Listings API with CRUD,
filtered + geospatial search, pagination, validation, tests and a strong README.

## Stack (do not deviate without asking)
- Python 3.12, FastAPI, Pydantic v2, pydantic-settings
- SQLAlchemy 2.0 async + asyncpg, GeoAlchemy2, Alembic
- PostgreSQL 16 + PostGIS (image: postgis/postgis:16-3.4)
- pytest, pytest-asyncio, httpx (AsyncClient + ASGITransport)
- uv for dependency management, ruff for lint/format, mypy (non-strict)
- Docker Compose for api + db; GitHub Actions CI

## Layout
app/main.py (app factory, handlers, routers)
app/core/config.py
app/db/session.py, app/db/models.py
app/schemas/listing.py
app/repositories/listing_repo.py   # ALL SQL/PostGIS lives here
app/services/listing_service.py    # business rules, raises domain errors
app/api/v1/listings.py             # thin routers only
app/errors.py                      # domain exceptions + handlers
alembic/, tests/unit, tests/integration, scripts/seed.py

## Domain rules
- price stored as BIGINT in kobo; API accepts/returns price in naira as integer
  or decimal — convert at the schema boundary. currency defaults to "NGN".
- listing_type enum: rent | sale | shortlet
- bedrooms >= 0 (0 = studio/self-contain)
- location: geography(Point, 4326) with GIST index. API exposes lat/lng floats only.
- agent_id: opaque UUID, indexed, no agents table (documented assumption)
- Composite index on (listing_type, price, bedrooms)

## Conventions
- Error envelope everywhere: {"error": {"code", "message", "details"}}
- List envelope: {"data": [...], "meta": {"page","page_size","total","total_pages"}}
- Routes under /api/v1. Type hints everywhere. No business logic in routers.
- Never use floats for money. Never do distance math in Python — use PostGIS.
- After each task: run `uv run ruff check . --fix`, `uv run ruff format .`,
  and `uv run pytest -q`; fix failures before reporting done.
- Keep commits small, conventional-commit style (feat:, test:, docs:, chore:).
