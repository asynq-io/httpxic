from __future__ import annotations

from typing import TYPE_CHECKING

import httpx
import pytest
from pydantic import BaseModel, ValidationError

from httpxic import APIClient, delete, get

if TYPE_CHECKING:
    import respx

pytestmark = pytest.mark.anyio


class Item(BaseModel):
    id: int
    name: str


class OptionalItemClient(APIClient):
    @get("/items/{item_id}")
    async def get_item(self, item_id: int) -> Item | None: ...

    @delete("/items/{item_id}")
    async def drop_item(self, item_id: int) -> None: ...

    @get("/items")
    async def list_items(self) -> list[Item]: ...


async def test_none_return_type_returns_none(
    respx_mock: respx.MockRouter, base_url: str
) -> None:
    respx_mock.delete("/items/1").mock(return_value=httpx.Response(204))

    async with httpx.AsyncClient(base_url=base_url) as http:
        result = await OptionalItemClient(http).drop_item(item_id=1)  # type: ignore[func-returns-value]

    assert result is None


async def test_empty_body_returns_none(
    respx_mock: respx.MockRouter, base_url: str
) -> None:
    respx_mock.get("/items/1").mock(return_value=httpx.Response(204))

    async with httpx.AsyncClient(base_url=base_url) as http:
        result = await OptionalItemClient(http).get_item(item_id=1)

    assert result is None


async def test_list_response_parsed_via_type_adapter(
    respx_mock: respx.MockRouter, base_url: str
) -> None:
    respx_mock.get("/items").mock(
        return_value=httpx.Response(
            200,
            json=[{"id": 1, "name": "a"}, {"id": 2, "name": "b"}],
        )
    )

    async with httpx.AsyncClient(base_url=base_url) as http:
        items = await OptionalItemClient(http).list_items()

    assert items == [Item(id=1, name="a"), Item(id=2, name="b")]


async def test_response_validation_error_propagates(
    respx_mock: respx.MockRouter, base_url: str
) -> None:
    respx_mock.get("/items/1").mock(
        return_value=httpx.Response(200, json={"id": "not-an-int", "name": "x"})
    )

    async with httpx.AsyncClient(base_url=base_url) as http:
        with pytest.raises(ValidationError):
            await OptionalItemClient(http).get_item(item_id=1)


async def test_api_client_wraps_async_client(base_url: str) -> None:
    http = httpx.AsyncClient(base_url=base_url)
    c = OptionalItemClient(http)
    assert isinstance(c, APIClient)
    assert str(c.http.base_url).rstrip("/") == base_url.rstrip("/")
    await http.aclose()
