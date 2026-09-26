from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "BaseFilter",
    "CursorPaginationFilter",
    "FieldsFilter",
    "Filter",
    "IncludeFilter",
    "OffsetLimitFilter",
    "OrderingFilter",
    "PaginationFilter",
    "SearchFilter",
    "to_params",
]


def to_params(model: BaseModel) -> dict[str, Any]:
    """Serialize a model into query parameters by alias, dropping ``None`` fields.

    Sets and tuples become lists, which httpx2 sends as repeated parameters.
    """
    return model.model_dump(mode="json", by_alias=True, exclude_none=True)


class BaseFilter(BaseModel):
    """Base for filter models sent as query parameters.

    Fields default to ``None`` so the server-side defaults apply when unset.
    """

    model_config = ConfigDict(populate_by_name=True)


class PaginationFilter(BaseFilter):
    page: int | None = None
    page_size: int | None = None


class OffsetLimitFilter(BaseFilter):
    offset: int | None = None
    limit: int | None = None


class CursorPaginationFilter(BaseFilter):
    cursor: str | None = None
    page_size: int | None = None


class OrderingFilter(BaseFilter):
    sort: list[str] | None = None


class SearchFilter(BaseFilter):
    query: str | None = Field(default=None, alias="q")


class FieldsFilter(BaseFilter):
    fields: set[str] | None = None


class IncludeFilter(BaseFilter):
    include: set[str] | None = None


class Filter(
    PaginationFilter, OrderingFilter, SearchFilter, FieldsFilter, IncludeFilter
):
    """Pagination, ordering, search, fields and include parameters combined."""
