import math
from typing import Annotated, Any, Self

from pydantic import BaseModel, ConfigDict, Field

from app.core.config import get_settings

_settings = get_settings()


class PageParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    page: Annotated[int, Field(ge=1, description="1-based page number.")] = 1
    page_size: Annotated[
        int, Field(ge=1, le=_settings.max_page_size, description="Items per page.")
    ] = 20

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size


class PageMeta(BaseModel):
    page: int
    page_size: int
    total: int
    total_pages: int


class Paginated[T](BaseModel):
    data: list[T]
    meta: PageMeta

    @classmethod
    def build(cls, items: list[T], *, total: int, page: int, page_size: int) -> Self:
        return cls(
            data=items,
            meta=PageMeta(
                page=page,
                page_size=page_size,
                total=total,
                total_pages=math.ceil(total / page_size) if total else 0,
            ),
        )


class ErrorBody(BaseModel):
    code: str
    message: str
    details: Any = None


class ErrorResponse(BaseModel):
    """The error envelope every non-2xx response uses (documented for OpenAPI)."""

    error: ErrorBody
