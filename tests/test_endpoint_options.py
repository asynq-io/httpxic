from __future__ import annotations

import json
from contextvars import ContextVar
from typing import TYPE_CHECKING, Annotated, Any
from urllib.parse import parse_qs

import pytest

from httpxic import APIClient, Body, ClientT, Header, Query, get, patch, post, put
from tests.conftest import resolve

if TYPE_CHECKING:
    from collections.abc import Mapping

    import httpx2
    import respx

request_id: ContextVar[str] = ContextVar("request_id", default="none")


def tracing_headers() -> Mapping[str, str]:
    return {"X-Request-Id": request_id.get()}


def tenant_params() -> Mapping[str, Any]:
    return {"tenant": request_id.get()}


class OptionsClient(APIClient[ClientT]):
    @get("/static", headers={"X-Api-Version": "2"}, params={"lang": "en"})
    def static(
        self,
        lang: Annotated[str | None, Query()] = None,
        version: Annotated[str | None, Header(alias="x-api-version")] = None,
    ) -> None: ...

    @get("/traced", headers=tracing_headers, params=tenant_params)
    def traced(self) -> None: ...

    @get("/redirect", follow_redirects=True)
    def redirect(self) -> None: ...

    @post("/blobs", headers={"Content-Type": "application/octet-stream"})
    def upload(self, body: Annotated[bytes, Body()]) -> dict[str, Any]: ...

    @put("/notes", headers={"Content-Type": "text/plain"})
    def put_note(
        self,
        body: Annotated[str | None, Body()] = None,
        content_type: Annotated[str | None, Header(alias="content-type")] = None,
    ) -> None: ...

    @patch("/patches", headers={"content-type": "application/merge-patch+json"})
    def merge_patch(self, body: Annotated[dict[str, Any], Body()]) -> None: ...


@pytest.fixture
def options_client(http: httpx2.Client | httpx2.AsyncClient) -> OptionsClient[Any]:
    return OptionsClient(http)


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("kwargs", "query", "version"),
    [
        ({}, {"lang": ["en"]}, "2"),
        ({"lang": "pl", "version": "3"}, {"lang": ["pl"]}, "3"),
    ],
)
async def test_static_options_are_overridden_by_parameters(
    options_client: OptionsClient[Any],
    respx_mock: respx.MockRouter,
    kwargs: dict[str, str],
    query: dict[str, list[str]],
    version: str,
) -> None:
    route = respx_mock.get("/static").respond(204)

    await resolve(options_client.static(**kwargs))

    request = route.calls.last.request
    assert parse_qs(request.url.query.decode()) == query
    assert request.headers["X-Api-Version"] == version


@pytest.mark.anyio
@pytest.mark.parametrize("value", ["req-1", "req-2"])
async def test_callable_options_are_evaluated_on_every_call(
    options_client: OptionsClient[Any], respx_mock: respx.MockRouter, value: str
) -> None:
    route = respx_mock.get("/traced").respond(204)
    token = request_id.set(value)
    try:
        await resolve(options_client.traced())
    finally:
        request_id.reset(token)

    request = route.calls.last.request
    assert request.headers["X-Request-Id"] == value
    assert parse_qs(request.url.query.decode()) == {"tenant": [value]}


@pytest.mark.anyio
async def test_other_options_are_passed_to_httpx(
    options_client: OptionsClient[Any], respx_mock: respx.MockRouter, base_url: str
) -> None:
    respx_mock.get("/redirect").respond(302, headers={"Location": "/final"})
    final = respx_mock.get("/final").respond(204)

    await resolve(options_client.redirect())

    assert final.calls.last.request.url == f"{base_url}/final"


@pytest.mark.anyio
async def test_non_json_content_type_sends_the_body_raw(
    options_client: OptionsClient[Any], respx_mock: respx.MockRouter
) -> None:
    route = respx_mock.post("/blobs").respond(json={"size": 3})

    result = await resolve(options_client.upload(b"\x00\x01\x02"))

    request = route.calls.last.request
    assert request.content == b"\x00\x01\x02"
    assert request.headers["Content-Type"] == "application/octet-stream"
    assert result == {"size": 3}


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("kwargs", "content", "content_type"),
    [
        ({"body": "hello"}, b"hello", "text/plain"),
        ({}, b"", "text/plain"),
        ({"body": "a", "content_type": "text/markdown"}, b"a", "text/markdown"),
    ],
)
async def test_optional_raw_body_and_content_type_parameter(
    options_client: OptionsClient[Any],
    respx_mock: respx.MockRouter,
    kwargs: dict[str, Any],
    content: bytes,
    content_type: str,
) -> None:
    route = respx_mock.put("/notes").respond(204)

    await resolve(options_client.put_note(**kwargs))

    request = route.calls.last.request
    assert request.content == content
    assert request.headers["Content-Type"] == content_type


@pytest.mark.anyio
async def test_json_suffix_content_type_is_json_encoded(
    options_client: OptionsClient[Any], respx_mock: respx.MockRouter
) -> None:
    route = respx_mock.patch("/patches").respond(204)

    await resolve(options_client.merge_patch({"name": None}))

    request = route.calls.last.request
    assert json.loads(request.content) == {"name": None}
    assert request.headers["Content-Type"] == "application/merge-patch+json"


def test_unknown_request_option_is_rejected() -> None:
    with pytest.raises(TypeError, match="unknown request option"):

        class BadClient(APIClient[ClientT]):
            @get("/x", content_type="text/plain")
            def f(self) -> None: ...
