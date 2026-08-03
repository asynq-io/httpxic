from __future__ import annotations

import json
from collections.abc import AsyncGenerator, AsyncIterable, AsyncIterator
from contextlib import aclosing
from typing import TYPE_CHECKING, Annotated, Any

import httpx
import pytest
from pydantic import BaseModel, ValidationError

from httpxic import (
    APIClient,
    Body,
    Header,
    Query,
    ServerSentEvent,
    aiter_sse,
    get,
    sse,
)

if TYPE_CHECKING:
    import respx

pytestmark = pytest.mark.anyio


class Chunk(BaseModel):
    n: int


class StreamClient(APIClient):
    @sse("/events")
    def watch(self) -> AsyncIterator[Chunk]: ...

    @sse("/raw")
    def watch_raw(self) -> AsyncIterator[ServerSentEvent]: ...

    @sse("/topics/{topic}")
    def watch_topic(
        self,
        topic: str,
        since: Annotated[int | None, Query()] = None,
    ) -> AsyncIterator[Chunk]: ...

    @sse("/chat", method="POST")
    def chat(self, data: Chunk) -> AsyncIterator[Chunk]: ...

    @sse("/forever", timeout=httpx.Timeout(5.0, read=None))
    def watch_forever(self) -> AsyncIterator[ServerSentEvent]: ...

    @sse("/negotiated")
    def watch_negotiated(
        self,
        accept: Annotated[str, Header(alias="Accept")],
    ) -> AsyncIterator[ServerSentEvent]: ...

    @sse("/negotiated")
    def watch_negotiated_lowercase(
        self,
        accept: Annotated[str, Header(alias="accept")],
    ) -> AsyncIterator[ServerSentEvent]: ...


def sse_response(*chunks: str, status_code: int = 200) -> httpx.Response:
    """An unread streaming response carrying ``chunks`` as an event stream."""

    async def body() -> AsyncIterator[bytes]:
        for chunk in chunks:
            yield chunk.encode()

    return httpx.Response(
        status_code,
        headers={"content-type": "text/event-stream"},
        content=body(),
    )


@pytest.fixture
async def stream_client(base_url: str) -> AsyncIterator[StreamClient]:
    async with httpx.AsyncClient(base_url=base_url) as http:
        yield StreamClient(http)


async def test_events_are_validated_into_the_declared_model(
    respx_mock: respx.MockRouter, stream_client: StreamClient
) -> None:
    respx_mock.get("/events").mock(
        return_value=sse_response(
            'data: {"n": 1}\n\n',
            'data: {"n": 2}\n\n',
        )
    )

    assert [chunk async for chunk in stream_client.watch()] == [
        Chunk(n=1),
        Chunk(n=2),
    ]


async def test_events_split_across_chunks_are_reassembled(
    respx_mock: respx.MockRouter, stream_client: StreamClient
) -> None:
    respx_mock.get("/events").mock(
        return_value=sse_response('data: {"n', '": 1}\n', "\ndata", ': {"n": 2}\n\n')
    )

    assert [chunk async for chunk in stream_client.watch()] == [
        Chunk(n=1),
        Chunk(n=2),
    ]


@pytest.mark.parametrize("separator", ["\n", "\r\n", "\r"])
async def test_every_sse_line_ending_is_accepted(
    respx_mock: respx.MockRouter, stream_client: StreamClient, separator: str
) -> None:
    payload = separator.join(['data: {"n": 1}', "", 'data: {"n": 2}', "", ""])
    respx_mock.get("/events").mock(return_value=sse_response(payload))

    assert [chunk async for chunk in stream_client.watch()] == [
        Chunk(n=1),
        Chunk(n=2),
    ]


async def test_server_sent_event_return_type_yields_raw_events(
    respx_mock: respx.MockRouter, stream_client: StreamClient
) -> None:
    respx_mock.get("/raw").mock(
        return_value=sse_response(
            "event: tick\nid: 42\nretry: 3000\ndata: first\ndata: second\n\n",
            "data: plain\n\n",
        )
    )

    events = [event async for event in stream_client.watch_raw()]

    assert events == [
        ServerSentEvent(data="first\nsecond", event="tick", id="42", retry=3000),
        ServerSentEvent(data="plain", event="message", id="42"),
    ]


async def test_comments_blank_data_and_unterminated_blocks(
    respx_mock: respx.MockRouter, stream_client: StreamClient
) -> None:
    respx_mock.get("/raw").mock(
        return_value=sse_response(
            ": keep-alive comment\n\n",
            "event: no-data\n\n",
            "data\n\n",
            "data: dropped, the stream ends mid-block\n",
        )
    )

    assert [event async for event in stream_client.watch_raw()] == [
        ServerSentEvent(data="")
    ]


async def test_leading_byte_order_mark_is_stripped(
    respx_mock: respx.MockRouter, stream_client: StreamClient
) -> None:
    respx_mock.get("/events").mock(
        return_value=sse_response('\ufeffdata: {"n": 1}\n\n')
    )

    assert [chunk async for chunk in stream_client.watch()] == [Chunk(n=1)]


async def test_malformed_field_values_are_ignored(
    respx_mock: respx.MockRouter, stream_client: StreamClient
) -> None:
    respx_mock.get("/raw").mock(
        return_value=sse_response(
            "id: with\x00null\nretry: soon\nunknown: field\ndata: payload\n\n"
        )
    )

    assert [event async for event in stream_client.watch_raw()] == [
        ServerSentEvent(data="payload", id=None, retry=None)
    ]


async def test_sse_requests_advertise_the_event_stream_media_type(
    respx_mock: respx.MockRouter, stream_client: StreamClient
) -> None:
    route = respx_mock.get("/events").mock(return_value=sse_response())

    async for _ in stream_client.watch():
        pass

    headers = route.calls.last.request.headers
    assert headers["Accept"] == "text/event-stream"
    assert headers["Cache-Control"] == "no-store"


async def test_explicit_accept_header_wins_over_the_default(
    respx_mock: respx.MockRouter, stream_client: StreamClient
) -> None:
    route = respx_mock.get("/negotiated").mock(return_value=sse_response())

    async for _ in stream_client.watch_negotiated(accept="text/plain"):
        pass

    assert route.calls.last.request.headers["Accept"] == "text/plain"


async def test_lowercase_accept_alias_replaces_the_default(
    respx_mock: respx.MockRouter, stream_client: StreamClient
) -> None:
    route = respx_mock.get("/negotiated").mock(return_value=sse_response())

    async for _ in stream_client.watch_negotiated_lowercase(accept="text/plain"):
        pass

    assert route.calls.last.request.headers.get_list("accept") == ["text/plain"]


async def test_path_and_query_parameters_are_sent(
    respx_mock: respx.MockRouter, stream_client: StreamClient
) -> None:
    route = respx_mock.get("/topics/news").mock(return_value=sse_response())

    async for _ in stream_client.watch_topic("news", since=7):
        pass

    assert route.calls.last.request.url.params["since"] == "7"


async def test_post_streams_send_their_body(
    respx_mock: respx.MockRouter, stream_client: StreamClient
) -> None:
    route = respx_mock.post("/chat").mock(
        return_value=sse_response('data: {"n": 2}\n\n')
    )

    assert [chunk async for chunk in stream_client.chat(Chunk(n=1))] == [Chunk(n=2)]

    request = route.calls.last.request
    assert json.loads(request.content) == {"n": 1}
    assert request.headers["Content-Type"] == "application/json"


async def test_timeout_override_is_forwarded(
    respx_mock: respx.MockRouter, stream_client: StreamClient
) -> None:
    route = respx_mock.get("/forever").mock(return_value=sse_response())

    async for _ in stream_client.watch_forever():
        pass

    assert route.calls.last.request.extensions["timeout"]["read"] is None


async def test_event_data_validation_error_propagates(
    respx_mock: respx.MockRouter, stream_client: StreamClient
) -> None:
    respx_mock.get("/events").mock(
        return_value=sse_response('data: {"n": "not-an-int"}\n\n')
    )

    with pytest.raises(ValidationError):
        async for _ in stream_client.watch():
            pass


async def test_error_status_raises_with_the_body_available(
    respx_mock: respx.MockRouter, stream_client: StreamClient
) -> None:
    respx_mock.get("/events").mock(
        return_value=httpx.Response(503, json={"detail": "overloaded"})
    )

    with pytest.raises(httpx.HTTPStatusError) as excinfo:
        async for _ in stream_client.watch():
            pass

    assert excinfo.value.response.json() == {"detail": "overloaded"}


async def test_error_status_is_streamed_when_not_raising(
    respx_mock: respx.MockRouter, base_url: str
) -> None:
    respx_mock.get("/raw").mock(
        return_value=sse_response("data: partial\n\n", status_code=503)
    )

    async with httpx.AsyncClient(base_url=base_url) as http:
        client = StreamClient(http, raise_for_status=False)
        events = [event async for event in client.watch_raw()]

    assert events == [ServerSentEvent(data="partial")]


async def test_decode_options_apply_to_events(
    respx_mock: respx.MockRouter, base_url: str
) -> None:
    respx_mock.get("/events").mock(return_value=sse_response('data: {"n": "1"}\n\n'))

    async with httpx.AsyncClient(base_url=base_url) as http:
        client = StreamClient(http, decode_options={"strict": True})
        with pytest.raises(ValidationError):
            async for _ in client.watch():
                pass


async def test_stream_escape_hatch_decodes_events_by_hand(
    respx_mock: respx.MockRouter, stream_client: StreamClient
) -> None:
    respx_mock.get("/raw").mock(return_value=sse_response("data: manual\n\n"))

    async with stream_client.stream("GET", "/raw") as response:
        events = [event async for event in aiter_sse(response.aiter_lines())]

    assert events == [ServerSentEvent(data="manual")]


class ClosingStream(httpx.AsyncByteStream):
    """A response stream that records whether the client released it."""

    def __init__(self, *chunks: str) -> None:
        self.chunks = chunks
        self.closed = False

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for chunk in self.chunks:
            yield chunk.encode()

    async def aclose(self) -> None:
        self.closed = True


async def test_leaving_the_stream_early_releases_the_connection(
    base_url: str,
) -> None:
    stream = ClosingStream('data: {"n": 1}\n\n', 'data: {"n": 2}\n\n')
    transport = httpx.MockTransport(lambda _: httpx.Response(200, stream=stream))

    async with httpx.AsyncClient(base_url=base_url, transport=transport) as http:
        events = StreamClient(http).watch()
        async with aclosing(events):
            async for chunk in events:
                assert chunk == Chunk(n=1)
                break

    assert stream.closed


@pytest.mark.parametrize(
    "annotation",
    [
        AsyncIterator[Chunk],
        AsyncIterable[Chunk],
        AsyncGenerator[Chunk, None],
        Annotated[AsyncIterator[Chunk], "documented"],
    ],
)
def test_every_async_iterator_return_annotation_is_accepted(annotation) -> None:
    def watch(self) -> None: ...

    watch.__annotations__["return"] = annotation
    decorate: Any = sse("/events")

    assert decorate(watch).__name__ == "watch"


@pytest.mark.parametrize(
    "annotation",
    [Chunk, list[Chunk], AsyncIterator, AsyncIterator[None], httpx.Response],
)
def test_non_async_iterator_return_annotation_raises(annotation) -> None:
    def watch(self) -> None: ...

    watch.__annotations__["return"] = annotation
    decorate: Any = sse("/events")

    with pytest.raises(TypeError, match="AsyncIterator"):
        decorate(watch)


def test_missing_return_annotation_raises() -> None:
    with pytest.raises(TypeError, match="missing return annotation"):

        class BadClient(APIClient):
            @sse("/events")
            def watch(self): ...


def test_body_parameter_on_a_get_stream_raises() -> None:
    with pytest.raises(TypeError, match="body-supporting method"):

        class BadClient(APIClient):
            @sse("/events")
            def watch(self, data: Annotated[Chunk, Body()]) -> AsyncIterator[Chunk]: ...


def test_sse_and_plain_endpoints_coexist_on_one_client() -> None:
    class MixedClient(APIClient):
        @get("/events/latest")
        async def latest(self) -> Chunk: ...

        @sse("/events")
        def watch(self) -> AsyncIterator[Chunk]: ...

    assert MixedClient.watch.__name__ == "watch"
    assert MixedClient.latest.__name__ == "latest"
