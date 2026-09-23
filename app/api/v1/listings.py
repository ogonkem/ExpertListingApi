import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, Path, Query, Request, Response, status
from fastapi.openapi.models import Example
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.schemas.common import ErrorResponse, PageParams, Paginated
from app.schemas.listing import ListingCreate, ListingOut, ListingUpdate, SearchParams
from app.services.listing_service import ListingService

router = APIRouter(prefix="/listings", tags=["listings"])


def get_listing_service(session: Annotated[AsyncSession, Depends(get_session)]) -> ListingService:
    return ListingService(session)


Service = Annotated[ListingService, Depends(get_listing_service)]
ListingId = Annotated[uuid.UUID, Path(description="Listing UUID.")]

NOT_FOUND: dict[int | str, dict[str, Any]] = {
    404: {"model": ErrorResponse, "description": "Listing not found"}
}
INVALID: dict[int | str, dict[str, Any]] = {
    422: {"model": ErrorResponse, "description": "Validation failed"}
}

AGENT = "7d3f0f3e-4b8f-4c1e-9d6a-2f3c1b5a9e01"

CREATE_EXAMPLES: dict[str, Example] = {
    "rent_lekki": Example(
        summary="3 bedroom flat for rent in Lekki Phase 1",
        value={
            "title": "Serviced 3 bedroom flat in Lekki Phase 1",
            "description": "24h power, pool and gym. Close to Admiralty Way.",
            "price": 8_500_000,
            "listing_type": "rent",
            "bedrooms": 3,
            "location": {"lat": 6.4474, "lng": 3.4746},
            "address": "15 Admiralty Way, Lekki Phase 1",
            "city": "Lagos",
            "agent_id": AGENT,
        },
    ),
    "sale_ikoyi": Example(
        summary="5 bedroom detached duplex for sale in Ikoyi",
        value={
            "title": "5 bedroom detached duplex with BQ in Old Ikoyi",
            "price": 950_000_000,
            "listing_type": "sale",
            "bedrooms": 5,
            "location": {"lat": 6.4549, "lng": 3.4336},
            "address": "Glover Road, Ikoyi",
            "city": "Lagos",
            "agent_id": AGENT,
        },
    ),
    "shortlet_vi": Example(
        summary="Studio shortlet in Victoria Island (0 bedrooms)",
        value={
            "title": "Studio apartment shortlet on Adeola Odeku",
            "price": 65_000.50,
            "listing_type": "shortlet",
            "bedrooms": 0,
            "location": {"lat": 6.4281, "lng": 3.4219},
            "address": "Adeola Odeku Street, Victoria Island",
            "city": "Lagos",
            "agent_id": AGENT,
        },
    ),
}

UPDATE_EXAMPLES: dict[str, Example] = {
    "price_drop": Example(summary="Reduce the price", value={"price": 7_800_000}),
    "move": Example(
        summary="Correct the pin and address",
        value={
            "location": {"lat": 6.4411, "lng": 3.4789},
            "address": "Plot 5, Freedom Way, Lekki Phase 1",
        },
    ),
    "clear_description": Example(
        summary="Clear an optional field with null", value={"description": None}
    ),
}


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=ListingOut,
    summary="Create a listing",
    description=(
        "Creates a property listing. `price` is in naira (up to 2 decimal places) and is "
        "stored as kobo. The response's `Location` header points at the new resource."
    ),
    responses=INVALID,
)
async def create_listing(
    request: Request,
    response: Response,
    service: Service,
    data: Annotated[ListingCreate, Body(openapi_examples=CREATE_EXAMPLES)],
) -> ListingOut:
    listing = await service.create(data)
    response.headers["Location"] = str(request.url_for("get_listing", listing_id=listing.id))
    return listing


@router.get(
    "",
    response_model=Paginated[ListingOut],
    summary="List listings",
    description="Returns listings newest first, paginated.",
    responses=INVALID,
)
async def list_listings(
    service: Service, params: Annotated[PageParams, Query()]
) -> Paginated[ListingOut]:
    return await service.list_page(params)


# Registered before /{listing_id}: routes match in order, and "search" would otherwise be
# captured as a (malformed) listing id and rejected with a 422.
@router.get(
    "/search",
    response_model=Paginated[ListingOut],
    summary="Search listings",
    description=(
        "Filter by `type`, price range (naira), bedroom range and, optionally, a radius "
        "around a point. `lat`, `lng` and `radius_km` go together; when given, each result "
        "includes `distance_km` and `sort=distance` becomes available. Results are "
        "paginated and ordering is stable (ties broken by id).\n\n"
        "Examples:\n"
        "- Rentals within 10 km of Lekki Phase 1, nearest first: "
        "`?type=rent&lat=6.4478&lng=3.4723&radius_km=10&sort=distance`\n"
        "- 3+ bedroom homes for sale under ₦500m: "
        "`?type=sale&min_bedrooms=3&max_price=500000000&sort=price_asc`"
    ),
    responses=INVALID,
)
async def search_listings(
    service: Service, params: Annotated[SearchParams, Query()]
) -> Paginated[ListingOut]:
    return await service.search(params)


@router.get(
    "/{listing_id}",
    response_model=ListingOut,
    name="get_listing",
    summary="Get a listing",
    responses=NOT_FOUND | INVALID,
)
async def get_listing(listing_id: ListingId, service: Service) -> ListingOut:
    return await service.get(listing_id)


@router.patch(
    "/{listing_id}",
    response_model=ListingOut,
    summary="Update a listing",
    description=(
        "Partial update: only supplied fields change. Required fields may be omitted but "
        "not set to null; `description`, `address` and `city` can be cleared with null."
    ),
    responses=NOT_FOUND | INVALID,
)
async def update_listing(
    listing_id: ListingId,
    service: Service,
    data: Annotated[ListingUpdate, Body(openapi_examples=UPDATE_EXAMPLES)],
) -> ListingOut:
    return await service.update(listing_id, data)


@router.delete(
    "/{listing_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a listing",
    responses=NOT_FOUND | INVALID,
)
async def delete_listing(listing_id: ListingId, service: Service) -> Response:
    await service.delete(listing_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
