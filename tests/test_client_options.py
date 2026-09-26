from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from typing import TYPE_CHECKING

import httpx
import httpx2
import pytest
from pydantic import BaseModel, ValidationError

from httpxic import APIClient, ClientT, get
from httpxic.decorators import Endpoint
from tests.conftest import resolve

if TYPE_CHECKING:
    import respx

pytestmark = pytest.mark.anyio


class Thing(BaseModel):
    id: int


class ThingClient(APIClient[ClientT]):
    @get("/things/{thing_id}")
    def get_thing(self, thing_id: int) -> Thing:
        """Fetch one thing."""


async def read_stream(
    client: APIClient[httpx2.Client] | APIClient[httpx2.AsyncClient], url: str
) -> httpx2.Response:
    context = client.stream("GET", url)
    if isinstance(context, AbstractAsyncContextManager):
        async with context as response:
            await response.aread()
    else:
        with context as response:
            response.read()
    return response


async def test_raise_for_status_false_returns_error_response(
    respx_mock: respx.MockRouter, http: httpx2.Client | httpx2.AsyncClient
) -> None:
    respx_mock.get("/things").mock(return_value=httpx.Response(418))

    response = await resolve(
        APIClient(http, raise_for_status=False).request("GET", "/things")
    )

    assert response.status_code == 418


async def test_raise_for_status_true_raises(
    respx_mock: respx.MockRouter, http: httpx2.Client | httpx2.AsyncClient
) -> None:
    respx_mock.get("/things").mock(return_value=httpx.Response(500))

    with pytest.raises(httpx2.HTTPStatusError):
        await resolve(APIClient(http).request("GET", "/things"))


@pytest.mark.parametrize(
    "method", ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"]
)
async def test_request_sends_the_given_method(
    respx_mock: respx.MockRouter, http: httpx2.Client | httpx2.AsyncClient, method: str
) -> None:
    route = respx_mock.request(method, "/things").mock(return_value=httpx.Response(200))

    response = await resolve(APIClient(http).request(method, "/things"))

    assert route.calls.last.request.method == method
    assert response.status_code == 200


async def test_stream_error_status_raises_with_the_body_read(
    respx_mock: respx.MockRouter, http: httpx2.Client | httpx2.AsyncClient
) -> None:
    respx_mock.get("/things").mock(
        return_value=httpx.Response(503, json={"detail": "overloaded"})
    )

    with pytest.raises(httpx2.HTTPStatusError) as exc_info:
        await read_stream(APIClient(http), "/things")

    assert exc_info.value.response.json() == {"detail": "overloaded"}


async def test_stream_yields_error_response_when_not_raising(
    respx_mock: respx.MockRouter, http: httpx2.Client | httpx2.AsyncClient
) -> None:
    respx_mock.get("/things").mock(return_value=httpx.Response(503, text="busy"))

    response = await read_stream(APIClient(http, raise_for_status=False), "/things")

    assert response.status_code == 503
    assert response.text == "busy"


async def test_stream_yields_successful_response(
    respx_mock: respx.MockRouter, http: httpx2.Client | httpx2.AsyncClient
) -> None:
    respx_mock.get("/things").mock(return_value=httpx.Response(200, text="ok"))

    response = await read_stream(APIClient(http), "/things")

    assert response.text == "ok"


async def test_decode_options_are_passed_to_response_validation(
    respx_mock: respx.MockRouter, http: httpx2.Client | httpx2.AsyncClient
) -> None:
    respx_mock.get("/things/1").mock(return_value=httpx.Response(200, json={"id": "1"}))

    lax = ThingClient(http)
    strict = ThingClient(http, decode_options={"strict": True})

    assert await resolve(lax.get_thing(thing_id=1)) == Thing(id=1)
    with pytest.raises(ValidationError):
        await resolve(strict.get_thing(thing_id=1))


def test_async_def_endpoint_is_rejected_at_definition_time() -> None:
    with pytest.raises(TypeError, match=r"BadClient\.get_thing: declare endpoints"):

        class BadClient(APIClient[ClientT]):
            @get("/things/{thing_id}")
            async def get_thing(self, thing_id: int) -> Thing: ...


def test_class_attribute_access_returns_the_endpoint() -> None:
    endpoint = ThingClient.get_thing

    assert isinstance(endpoint, Endpoint)
    assert endpoint.__name__ == "get_thing"
    assert endpoint.__doc__ == "Fetch one thing."
