from __future__ import annotations

import json
from typing import TYPE_CHECKING, Annotated

import httpx
import pytest

from httpxic import (
    APIClient,
    Body,
    Header,
    Path,
    Query,
    delete,
    get,
    head,
    options,
    post,
)
from httpxic.decorators import _adapter

if TYPE_CHECKING:
    import respx

pytestmark = pytest.mark.anyio


class TimeClient(APIClient):
    @get("/events")
    async def list_events(self) -> list[dict]: ...

    @post("/events")
    async def create_event(self, data: dict) -> dict: ...

    @head("/ping")
    async def ping(self) -> None: ...

    @options("/ping")
    async def ping_options(self) -> None: ...


async def test_plain_dict_body_is_serialized(
    respx_mock: respx.MockRouter, base_url: str
) -> None:
    route = respx_mock.post("/events").mock(
        return_value=httpx.Response(200, json={"ok": True})
    )

    async with httpx.AsyncClient(base_url=base_url) as http:
        c = TimeClient(http)
        result = await c.create_event({"type": "foo"})

    assert result == {"ok": True}
    assert json.loads(route.calls.last.request.content) == {"type": "foo"}


async def test_head_and_options_methods(
    respx_mock: respx.MockRouter, base_url: str
) -> None:
    head_route = respx_mock.head("/ping").mock(return_value=httpx.Response(200))
    opts_route = respx_mock.options("/ping").mock(return_value=httpx.Response(204))

    async with httpx.AsyncClient(base_url=base_url) as http:
        c = TimeClient(http)
        await c.ping()
        await c.ping_options()

    assert head_route.called
    assert opts_route.called
    assert head_route.calls.last.request.method == "HEAD"
    assert opts_route.calls.last.request.method == "OPTIONS"


async def test_type_adapter_is_cached() -> None:
    assert _adapter(int) is _adapter(int)
    assert _adapter(list[int]) is _adapter(list[int])
    assert _adapter(dict) is _adapter(dict)


class MarkerClient(APIClient):
    @get("/items/{item_id}")
    async def get_item(
        self,
        item_id: Annotated[int, Path()],
        fields: Annotated[str | None, Query()] = None,
        x_request_id: Annotated[str | None, Header(alias="X-Request-ID")] = None,
    ) -> dict: ...

    @post("/items")
    async def create_item(
        self,
        data: Annotated[dict, Body()],
        x_request_id: Annotated[str | None, Header(alias="X-Request-ID")] = None,
    ) -> dict: ...

    @delete("/items/{item_id}")
    async def remove_item(
        self,
        item_id: Annotated[int, Path()],
    ) -> None: ...


async def test_query_marker_sends_to_query_params(
    respx_mock: respx.MockRouter, base_url: str
) -> None:
    route = respx_mock.get("/items/1").mock(return_value=httpx.Response(200, json={}))

    async with httpx.AsyncClient(base_url=base_url) as http:
        await MarkerClient(http).get_item(item_id=1, fields="id,name")

    assert route.calls.last.request.url.params["fields"] == "id,name"


async def test_query_marker_default_used_when_omitted(
    respx_mock: respx.MockRouter, base_url: str
) -> None:
    route = respx_mock.get("/items/1").mock(return_value=httpx.Response(200, json={}))

    async with httpx.AsyncClient(base_url=base_url) as http:
        await MarkerClient(http).get_item(item_id=1)

    assert "fields" not in route.calls.last.request.url.params


async def test_header_marker_sends_with_alias(
    respx_mock: respx.MockRouter, base_url: str
) -> None:
    route = respx_mock.get("/items/1").mock(return_value=httpx.Response(200, json={}))

    async with httpx.AsyncClient(base_url=base_url) as http:
        await MarkerClient(http).get_item(item_id=1, x_request_id="abc-123")

    assert route.calls.last.request.headers["X-Request-ID"] == "abc-123"


async def test_body_marker_allows_keyword_body(
    respx_mock: respx.MockRouter, base_url: str
) -> None:
    route = respx_mock.post("/items").mock(
        return_value=httpx.Response(201, json={"id": 1})
    )

    async with httpx.AsyncClient(base_url=base_url) as http:
        await MarkerClient(http).create_item(data={"name": "widget"})

    assert json.loads(route.calls.last.request.content) == {"name": "widget"}


async def test_path_marker_required_raises_on_missing(
    respx_mock: respx.MockRouter, base_url: str
) -> None:
    respx_mock.delete("/items/1").mock(return_value=httpx.Response(204))

    async with httpx.AsyncClient(base_url=base_url) as http:
        with pytest.raises(TypeError):
            await MarkerClient(http).remove_item()


def test_unused_path_placeholder_raises_at_definition_time() -> None:
    with pytest.raises(TypeError, match=r"\{user_id\}"):

        class BadClient(APIClient):
            @get("/users/{user_id}")
            async def list_users(self) -> list[dict]: ...


def test_multiple_implicit_body_params_raises_at_definition_time() -> None:
    with pytest.raises(TypeError, match="ambiguous"):

        class BadClient(APIClient):
            @post("/items")
            async def create(self, data: dict, extra: dict) -> dict: ...
