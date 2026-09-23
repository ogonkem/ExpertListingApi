import asyncio
import logging
from typing import Literal

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session

logger = logging.getLogger(__name__)

router = APIRouter(tags=["health"])

DB_CHECK_TIMEOUT_SECONDS = 3


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    database: Literal["ok", "unreachable"]


@router.get(
    "/health",
    response_model=HealthResponse,
    responses={503: {"model": HealthResponse, "description": "Database unreachable"}},
)
async def health(session: AsyncSession = Depends(get_session)) -> JSONResponse:
    try:
        async with asyncio.timeout(DB_CHECK_TIMEOUT_SECONDS):
            await session.execute(text("SELECT 1"))
    except (SQLAlchemyError, OSError) as exc:  # TimeoutError is an OSError
        logger.warning("Health check DB probe failed: %r", exc)
        body = HealthResponse(status="degraded", database="unreachable")
        return JSONResponse(status_code=503, content=body.model_dump())
    return JSONResponse(content=HealthResponse(status="ok", database="ok").model_dump())
