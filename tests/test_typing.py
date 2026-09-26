from __future__ import annotations

from collections.abc import AsyncIterator, Coroutine, Iterator
from contextlib import AbstractAsyncContextManager, AbstractContextManager
from typing import Any

import httpx2
from pydantic import BaseModel
from typing_extensions import assert_type

from httpxic import APIClient, ClientT, get, sse
from httpxic.decorators import Endpoint


class Item(BaseModel):
    id: int


class ItemClient(APIClient[ClientT]):
    @get("/items/{item_id}")
    def get_item(self, item_id: int) -> Item: ...  # type: ignore[empty-body]

    @sse("/items")
    def watch_sync(self) -> Iterator[Item]: ...  # type: ignore[empty-body]

    @sse("/items")
    def watch_async(self) -> AsyncIterator[Item]: ...  # type: ignore[empty-body]


def check_sync(client: ItemClient[httpx2.Client]) -> None:
    assert_type(client.get_item(1), Item)
    assert_type(client.watch_sync(), Iterator[Item])
    assert_type(client.request("GET", "/"), httpx2.Response)
    assert_type(client.stream("GET", "/"), AbstractContextManager[httpx2.Response])


def check_async(client: ItemClient[httpx2.AsyncClient]) -> None:
    _ = assert_type(client.get_item(1), Coroutine[Any, Any, Item])
    assert_type(client.watch_async(), AsyncIterator[Item])
    _ = assert_type(client.request("GET", "/"), Coroutine[Any, Any, httpx2.Response])
    assert_type(client.stream("GET", "/"), AbstractAsyncContextManager[httpx2.Response])


def test_class_attribute_is_the_endpoint() -> None:
    endpoint: Endpoint[..., Item] = ItemClient.get_item

    assert isinstance(endpoint, Endpoint)
