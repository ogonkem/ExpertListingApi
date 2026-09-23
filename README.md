# Expert Listing API

[![CI](https://github.com/ogonkem/ExpertListingApi/actions/workflows/ci.yml/badge.svg)](https://github.com/ogonkem/ExpertListingApi/actions/workflows/ci.yml)

A property listings API for the Nigerian market: CRUD plus filtered and geospatial search
("3-bed rentals under ₦8m within 5 km of Lekki Phase 1, nearest first"). FastAPI and
async SQLAlchemy on PostgreSQL 16 + PostGIS. Prices are in naira at the API and stored as
kobo. Radius and distance are computed by PostGIS on a GIST index. The same
error and pagination envelope is used everywhere. Tests run against a real PostGIS
database, and CI runs this README's quick start on every push.

---

## Quick start

Requires Docker (with Compose v2), [uv](https://docs.astral.sh/uv/) and `make`.

```bash
git clone https://github.com/ogonkem/ExpertListingApi.git && cd ExpertListingApi
cp .env.example .env
make up          # builds the api, starts PostGIS, runs migrations; waits until healthy
make seed        # 500 realistic Lagos + Abuja listings (make seed ARGS="--count 2000 --reset")
open http://localhost:8000/docs     # Swagger UI with examples (xdg-open / start on Linux / Windows)
make test        # 267 tests (unit + integration against PostGIS) with coverage
```

`make down` stops everything. `make reset` also wipes the database volume. Other targets:
`logs`, `migrate`, `lint`, `fmt`.
No `make` (e.g. on Windows)? Every target is a single command. Copy it from the [`Makefile`](Makefile).

<details><summary>Configuration (<code>.env</code>)</summary>

| Variable | Default | Notes |
|---|---|---|
| `DATABASE_URL` / `TEST_DATABASE_URL` | local compose DBs | `postgresql+asyncpg://…`. The test DB is created by `docker/initdb`. |
| `MAX_PAGE_SIZE` / `MAX_RADIUS_KM` | `100` / `100` | Upper bounds for `page_size` and `radius_km` |
| `LOG_FORMAT` / `LOG_LEVEL` | `json` / `INFO` | JSON: one object per line, including `request_id` |
| `CORS_ORIGINS` | *(empty = off)* | Comma-separated origins, or `*` |
| `APP_ENV` | `local` | Included in every log line |

</details>

## API

All routes are under `/api/v1`. Interactive docs are at `/docs`.

| Method | Path | Success | Notes |
|---|---|---|---|
| `POST` | `/listings` | 201 + `Location` | Body: `title, price (naira), listing_type, bedrooms, location{lat,lng}, agent_id`, plus optional `description, address, city` |
| `GET` | `/listings` | 200 | Newest first; `page`, `page_size` |
| `GET` | `/listings/search` | 200 | `type, min_price, max_price, min_bedrooms, max_bedrooms, lat, lng, radius_km, sort, page, page_size` |
| `GET` | `/listings/{id}` | 200 | 404 if missing, 422 if not a UUID |
| `PATCH` | `/listings/{id}` | 200 | Partial update (see [PATCH semantics](#design-decisions)) |
| `DELETE` | `/listings/{id}` | 204 | |
| `GET` | `/health` | 200 / 503 | Includes a DB `SELECT 1` probe |

`listing_type` is one of `rent` (per year), `sale` or `shortlet` (per night). `bedrooms` is
`0`–`50`, where `0` means a studio or self contain. `sort` is one of `newest` (default),
`price_asc`, `price_desc` or `distance`.

**Radius around Lekki Phase 1** (5 km). Every result includes `distance_km`:

```bash
curl "http://localhost:8000/api/v1/listings/search?lat=6.4478&lng=3.4723&radius_km=5"
```

**Combined filters**: 3–4 bedroom rentals between ₦5m and ₦15m a year, cheapest first:

```bash
curl "http://localhost:8000/api/v1/listings/search?type=rent&min_bedrooms=3&max_bedrooms=4&min_price=5000000&max_price=15000000&sort=price_asc"
```

**Distance sort**: shortlets nearest to Victoria Island:

```bash
curl "http://localhost:8000/api/v1/listings/search?type=shortlet&lat=6.4281&lng=3.4219&radius_km=10&sort=distance&page_size=1"
```

```json
{
  "data": [{
    "id": "ccc62fed-7a6e-43d0-99c5-9cd9b09d721b",
    "title": "3 Bedroom Penthouse in Victoria Island",
    "price": 270000, "currency": "NGN", "listing_type": "shortlet", "bedrooms": 3,
    "location": {"lat": 6.430653, "lng": 3.419456},
    "address": "59 Akin Adesola Street, Victoria Island", "city": "Lagos",
    "agent_id": "840b8845-0110-5f22-a78e-a3c7cde73f28",
    "created_at": "2026-07-24T14:42:00Z", "updated_at": "2026-07-24T14:42:00Z",
    "distance_km": 0.39
  }],
  "meta": {"page": 1, "page_size": 1, "total": 28, "total_pages": 28}
}
```

Errors always look like `{"error": {"code", "message", "details"}}`:

```json
{"error": {"code": "validation_error", "message": "Request validation failed",
  "details": [{"field": "query", "message": "lat, lng and radius_km must be provided together (missing: radius_km)"}]}}
```

## Design decisions

- **PostGIS `geography(Point, 4326)` + GIST, not haversine in Python.**
  Radius search uses `ST_DWithin(location, point, metres)`, which is index-assisted:
  Postgres narrows candidates with a bounding-box check on the GIST index, then checks
  exact distance. Only the rows that match get a distance computed or sorted.
  Haversine in the app would mean loading every candidate row and filtering in
  Python, which isn't possible to paginate or count correctly at scale. `geography` also gives
  metres on the WGS84 spheroid, with no projection maths. (See the [evidence](#evidence-the-radius-search-uses-the-gist-index).)
- **Money is `BIGINT` kobo; the API speaks naira.** The API accepts naira as an integer
  or a decimal with up to 2 dp, and rejects `1.005`. Values are converted to kobo at the schema
  boundary, so integer arithmetic makes rounding bugs impossible. Responses return naira as a
  JSON number. It's bounded to 15 significant digits, so it always round-trips exactly. A
  string would be awkward for clients. Currency defaults to `NGN`.
- **Router → service → repository.** Routers only parse and delegate. The service owns
  business rules (naira/kobo, not-found errors, commits). The repository owns *all* SQL
  and PostGIS, including the single `point(lat, lng)` helper that calls
  `ST_MakePoint(lng, lat)`, so the easy-to-swap argument order lives in one place. Each
  layer is testable on its own, and the SQL is easy to review.
- **Offset pagination; keyset later.** `page`/`page_size` is simple, allows jumping to
  page N, and gives an exact `total` from a count over the same filters. That count is a
  separate query rather than `COUNT(*) OVER ()`: a window count returns nothing for a page
  past the end, and it forces distance and sorting on every match. Every sort ends in
  `id`, so pages are stable. **I'd switch to keyset** (a `(created_at, id)` or
  `(price, id)` cursor) once users page deep, since `OFFSET` cost grows linearly, or
  for infinite-scroll feeds where new inserts would shift offset pages.
- **One error envelope.** Validation, domain, 404/405 and unexpected errors all return
  `{"error": {code, message, details}}`. Validation `details` are compact
  `[{field, message}]`. A 500 never leaks internals; it returns the request id, which is also in the
  `X-Request-ID` header and in every JSON log line, so support can find the logs.
- **PATCH semantics.** Only supplied fields change. An empty body is a 422.
  Required columns (`title`, `price`, `listing_type`, `bedrooms`, `location`, `agent_id`)
  may be omitted but not set to `null`. Nullable ones (`description`, `address`, `city`) can be
  cleared with `null`. `updated_at` is maintained by a DB trigger, so it's correct
  whichever client writes.
- **`agent_id` is an opaque, indexed UUID with no foreign key.** Agents are owned
  by another service, so this API stores the reference without validating it. That avoids
  coupling deploys or putting another service's data in this schema.
- **Real Postgres in tests, not SQLite.** The core behaviour is PostGIS (`ST_DWithin`,
  spheroid distances, GIST), Postgres enums, `CHECK` constraints and triggers. SQLite
  would test a different database. Each test runs inside a transaction that's rolled
  back, so tests stay isolated and fast (267 tests in about 15 s).

## Assumptions

- **No auth.** Anyone can create, edit or delete any listing (see improvements).
- **Single currency.** Everything is NGN. `currency` is stored, so multi-currency is a later
  change rather than a rewrite.
- **Agents live in another service.** `agent_id` is trusted as given.
- Prices are per year for `rent`, per night for `shortlet`, and outright for `sale`. The
  API doesn't enforce the period; it's a convention.

## Testing

```bash
make test                                   # everything, with coverage (97%)
uv run pytest tests/unit -q                 # no database needed
uv run pytest tests/integration -q          # needs `make up` (uses TEST_DATABASE_URL)
```

- **Unit** (no DB): every schema validator rule (naira precision, geo all-or-none,
  min ≤ max, PATCH null rules), the error envelope and 500 path, request-id and
  CORS middleware, JSON logging, and seed-data generation.
- **Integration** (real PostGIS): migrations run once per session. Each test gets a
  rolled-back transaction, and the service's commits become savepoints. Tests cover
  CRUD, 404/422 cases, and search against Lekki, VI, Ikeja and Wuse II coordinates.
  `distance_km` is checked against an independent haversine calculation. A test also
  asserts the raw `ST_X`/`ST_Y` values, so a lng/lat swap can't hide behind a symmetric
  read path. `alembic check` runs as a test, so models and migrations can't drift.
- **CI** (GitHub Actions): lint and types (ruff, `ruff format --check`, mypy); tests with
  coverage against a PostGIS service; and the README quick start run exactly as written
  on a clean checkout.

## What I'd improve with more time

- **Auth and ownership:** JWTs from the agents service, and only an agent (or an admin) can
  modify their own listings.
- **Keyset pagination** for deep or infinite-scroll results (see above).
- **Meilisearch** (fed from Postgres) for full-text, typo-tolerant search on titles and
  addresses ("Lekki", "Leki", "Phase 1"), with Postgres staying the source of truth.
- **Redis caching** of hot searches (popular areas, default filters), invalidated on
  writes or with a short TTL.
- **Rate limiting** per client/IP on writes and on search.
- **Image uploads** with presigned S3/Cloudinary uploads, storing only URLs and metadata.
- **Soft deletes and an audit log** (who changed a price, and when), which matters for
  disputes.
- **OpenTelemetry** traces and metrics, building on the existing request id.
- **Load testing with 1M rows**: check the GIST and composite indexes and plan choices under
  realistic data shapes, and tune the pool size.

## Evidence: the radius search uses the GIST index

This is the exact SQL the repository generates for a 10 km search around Lekki Phase 1,
`sort=distance`, `LIMIT 20`, run on about 5,000 seeded rows (40% Lagos, 20% Abuja, 40% elsewhere in Nigeria):

```
 Limit (actual time=17.425..17.436 rows=20 loops=1)
   ->  Sort (actual time=17.418..17.421 rows=20 loops=1)
         Sort Key: ((st_distance(location, '0101…'::geography(Point,4326), true) / '1000'::double precision)), id
         Sort Method: top-N heapsort  Memory: 34kB
         ->  Bitmap Heap Scan on listings (actual time=16.278..17.240 rows=206 loops=1)
               Filter: st_dwithin(location, '0101…'::geography(Point,4326), '10000'::double precision, true)
               Rows Removed by Filter: 31
               ->  Bitmap Index Scan on ix_listings_location (actual time=0.095..0.096 rows=239 loops=1)
                     Index Cond: (location && _st_expand('0101…'::geography(Point,4326), '10000'::double precision))
 Execution Time: 17.654 ms
```

The GIST index (`ix_listings_location`) returns 239 bounding-box candidates. The exact
`ST_DWithin` check keeps 206, and only those get a distance computed and a top-20 sort.
The rest of the table is never touched. The 17 ms is a cold connection's first geodesic
call; the same plan warm takes **2.0 ms**. The count query uses the same index path and
runs in **0.4 ms**.
