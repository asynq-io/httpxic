from __future__ import annotations

from typing import TYPE_CHECKING

import httpx
import pytest
from pydantic import BaseModel, ValidationError

from httpxic import APIClient, ClientT, delete, get
from tests.conftest import resolve

if TYPE_CHECKING:
    import httpx2
    import respx

pytestmark = pytest.mark.anyio


class Item(BaseModel):
    id: int
    name: str


class OptionalItemClient(APIClient[ClientT]):
    @get("/items/{item_id}")
    def get_item(self, item_id: int) -> Item | None: ...

    @delete("/items/{item_id}")
    def drop_item(self, item_id: int) -> None: ...

    @get("/items")
    def list_items(self) -> list[Item]: ...


async def test_none_return_type_returns_none(
    respx_mock: respx.MockRouter, http: httpx2.Client | httpx2.AsyncClient
) -> None:
    respx_mock.delete("/items/1").mock(return_value=httpx.Response(204))

    result = await resolve(OptionalItemClient(http).drop_item(item_id=1))  # type: ignore[func-returns-value]

    assert result is None


async def test_empty_body_returns_none(
    respx_mock: respx.MockRouter, http: httpx2.Client | httpx2.AsyncClient
) -> None:
    respx_mock.get("/items/1").mock(return_value=httpx.Response(204))

    result = await resolve(OptionalItemClient(http).get_item(item_id=1))

    assert result is None


async def test_list_response_parsed_via_type_adapter(
    respx_mock: respx.MockRouter, http: httpx2.Client | httpx2.AsyncClient
) -> None:
    respx_mock.get("/items").mock(
        return_value=httpx.Response(
            200,
            json=[{"id": 1, "name": "a"}, {"id": 2, "name": "b"}],
        )
    )

    items = await resolve(OptionalItemClient(http).list_items())

    assert items == [Item(id=1, name="a"), Item(id=2, name="b")]


async def test_response_validation_error_propagates(
    respx_mock: respx.MockRouter, http: httpx2.Client | httpx2.AsyncClient
) -> None:
    respx_mock.get("/items/1").mock(
        return_value=httpx.Response(200, json={"id": "not-an-int", "name": "x"})
    )

    with pytest.raises(ValidationError):
        await resolve(OptionalItemClient(http).get_item(item_id=1))


async def test_api_client_wraps_the_given_client(
    http: httpx2.Client | httpx2.AsyncClient,
) -> None:
    c = OptionalItemClient(http)

    assert isinstance(c, APIClient)
    assert c.http is http
