from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import httpx
import pytest
from pydantic import BaseModel, ValidationError

from httpxic import APIClient, ClientClosedError, HttpxicError, get

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    import respx

pytestmark = pytest.mark.anyio

INSTRUMENTED_FLAG = "_is_instrumented_by_opentelemetry"


class Thing(BaseModel):
    id: int


class ThingClient(APIClient):
    @get("/things/{thing_id}")
    async def get_thing(self, thing_id: int) -> Thing: ...


@pytest.fixture
async def external_http(base_url: str) -> AsyncIterator[httpx.AsyncClient]:
    http = httpx.AsyncClient(base_url=base_url)
    try:
        yield http
    finally:
        await http.aclose()


def is_instrumented(http: httpx.AsyncClient) -> bool:
    return getattr(http, INSTRUMENTED_FLAG, False)


async def test_supplied_client_is_not_closed_on_context_exit(
    external_http: httpx.AsyncClient,
) -> None:
    async with APIClient(external_http):
        pass

    assert external_http.is_closed is False


async def test_supplied_client_stays_usable_after_aclose(
    respx_mock: respx.MockRouter, external_http: httpx.AsyncClient
) -> None:
    respx_mock.get("/things").mock(return_value=httpx.Response(200))

    client = APIClient(external_http)
    await client.aclose()

    assert external_http.is_closed is False
    response = await external_http.get("/things")
    assert response.status_code == 200


async def test_already_opened_supplied_client_can_be_wrapped(
    respx_mock: respx.MockRouter, base_url: str
) -> None:
    respx_mock.get("/things").mock(return_value=httpx.Response(200))

    async with httpx.AsyncClient(base_url=base_url) as http:
        async with APIClient(http) as client:
            response = await client.get("/things")

        assert response.status_code == 200
        assert http.is_closed is False


async def test_owned_client_is_closed_on_aclose(base_url: str) -> None:
    client = APIClient(base_url=base_url)

    await client.aclose()

    assert client.http.is_closed is True


async def test_owned_client_is_closed_on_context_exit(base_url: str) -> None:
    client = APIClient(base_url=base_url)

    async with client:
        assert client.http.is_closed is False

    assert client.http.is_closed is True


async def test_aclose_is_idempotent(base_url: str) -> None:
    client = APIClient(base_url=base_url)

    await client.aclose()
    await client.aclose()

    assert client.http.is_closed is True


async def test_closing_inside_the_context_manager_is_safe(base_url: str) -> None:
    async with APIClient(base_url=base_url) as client:
        await client.aclose()

    assert client.http.is_closed is True


async def test_reusing_a_closed_owned_client_raises_client_closed_error(
    base_url: str,
) -> None:
    client = APIClient(base_url=base_url)

    async with client:
        pass

    with pytest.raises(ClientClosedError, match="already been closed") as exc_info:
        async with client:
            pass

    assert exc_info.value.client_name == "APIClient"
    assert isinstance(exc_info.value, HttpxicError)


async def test_entering_with_a_closed_supplied_client_raises_client_closed_error(
    external_http: httpx.AsyncClient,
) -> None:
    client = APIClient(external_http)
    await external_http.aclose()

    with pytest.raises(ClientClosedError, match="already been closed"):
        async with client:
            pass


async def test_context_manager_is_reusable_with_a_supplied_client(
    external_http: httpx.AsyncClient,
) -> None:
    client = APIClient(external_http)

    async with client:
        pass
    async with client:
        pass

    assert external_http.is_closed is False


async def test_client_closed_error_is_still_caught_as_runtime_error(
    base_url: str,
) -> None:
    client = APIClient(base_url=base_url)
    await client.aclose()

    with pytest.raises(RuntimeError) as exc_info:
        async with client:
            pass

    assert type(exc_info.value) is ClientClosedError


async def test_http_client_and_client_options_are_mutually_exclusive(
    external_http: httpx.AsyncClient, base_url: str
) -> None:
    with pytest.raises(ValueError, match="Use only either"):
        APIClient(external_http, base_url=base_url)


async def test_raise_for_status_false_returns_error_response(
    respx_mock: respx.MockRouter, base_url: str
) -> None:
    respx_mock.get("/things").mock(return_value=httpx.Response(418))

    async with APIClient(base_url=base_url, raise_for_status=False) as client:
        response = await client.get("/things")

    assert response.status_code == 418


async def test_raise_for_status_true_raises(
    respx_mock: respx.MockRouter, base_url: str
) -> None:
    respx_mock.get("/things").mock(return_value=httpx.Response(500))

    async with APIClient(base_url=base_url) as client:
        with pytest.raises(httpx.HTTPStatusError):
            await client.get("/things")


async def test_decode_options_are_passed_to_response_validation(
    respx_mock: respx.MockRouter, external_http: httpx.AsyncClient
) -> None:
    respx_mock.get("/things/1").mock(return_value=httpx.Response(200, json={"id": "1"}))

    lax = ThingClient(external_http)
    strict = ThingClient(external_http, decode_options={"strict": True})

    assert await lax.get_thing(thing_id=1) == Thing(id=1)
    with pytest.raises(ValidationError):
        await strict.get_thing(thing_id=1)


@pytest.mark.parametrize(
    "verb", ["get", "post", "put", "patch", "delete", "head", "options"]
)
async def test_convenience_methods_send_matching_verb(
    respx_mock: respx.MockRouter, base_url: str, verb: str
) -> None:
    route = respx_mock.request(verb.upper(), "/things").mock(
        return_value=httpx.Response(200)
    )

    async with APIClient(base_url=base_url) as client:
        response = await getattr(client, verb)("/things")

    assert route.called
    assert route.calls.last.request.method == verb.upper()
    assert response.status_code == 200


async def test_owned_client_is_instrumented(base_url: str) -> None:
    pytest.importorskip("opentelemetry.instrumentation.httpx")

    client = APIClient(base_url=base_url)

    assert is_instrumented(client.http) is True

    await client.aclose()


async def test_instrument_false_leaves_client_untouched(base_url: str) -> None:
    pytest.importorskip("opentelemetry.instrumentation.httpx")

    async with APIClient(base_url=base_url, instrument=False) as client:
        assert is_instrumented(client.http) is False


async def test_supplied_client_is_not_instrumented_by_default(
    external_http: httpx.AsyncClient,
) -> None:
    pytest.importorskip("opentelemetry.instrumentation.httpx")

    async with APIClient(external_http):
        assert is_instrumented(external_http) is False


async def test_instrument_true_instruments_a_supplied_client(
    external_http: httpx.AsyncClient,
) -> None:
    pytest.importorskip("opentelemetry.instrumentation.httpx")

    async with APIClient(external_http, instrument=True):
        assert is_instrumented(external_http) is True


async def test_shared_client_is_instrumented_only_once(
    external_http: httpx.AsyncClient, caplog: pytest.LogCaptureFixture
) -> None:
    pytest.importorskip("opentelemetry.instrumentation.httpx")

    with caplog.at_level(logging.WARNING):
        clients = [APIClient(external_http, instrument=True) for _ in range(3)]

    assert "already instrumented" not in caplog.text
    assert is_instrumented(external_http) is True

    for client in clients:
        await client.aclose()
