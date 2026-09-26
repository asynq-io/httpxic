from __future__ import annotations

import json
from collections.abc import (
    AsyncGenerator,
    AsyncIterable,
    AsyncIterator,
    Generator,
    Iterable,
    Iterator,
)
from contextlib import aclosing, closing
from typing import TYPE_CHECKING, Annotated, Any

import httpx
import httpx2
import pytest
from httpx2 import EventSource
from pydantic import BaseModel, ValidationError

from httpxic import (
    APIClient,
    Body,
    ClientT,
    Header,
    Query,
    ServerSentEvent,
    get,
    sse,
)
from tests.conftest import collect

if TYPE_CHECKING:
    import respx

pytestmark = pytest.mark.anyio


class Chunk(BaseModel):
    n: int


class StreamClient(APIClient[ClientT]):
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

    @sse("/forever", timeout=httpx2.Timeout(5.0, read=None))
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


class ChunkStream(httpx.SyncByteStream, httpx.AsyncByteStream):
    """A response body served in ``chunks`` to either client, recording its release."""

    def __init__(self, *chunks: str) -> None:
        self.chunks = [chunk.encode() for chunk in chunks]
        self.closed = False

    def __iter__(self) -> Iterator[bytes]:
        return iter(self.chunks)

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for chunk in self.chunks:
            yield chunk

    def close(self) -> None:
        self.closed = True

    async def aclose(self) -> None:
        self.closed = True


def sse_response(*chunks: str, status_code: int = 200) -> httpx.Response:
    """An unread streaming response carrying ``chunks`` as an event stream."""
    return httpx.Response(
        status_code,
        headers={"content-type": "text/event-stream"},
        stream=ChunkStream(*chunks),
    )


@pytest.fixture
def stream_client(http: httpx2.Client | httpx2.AsyncClient) -> StreamClient[Any]:
    return StreamClient(http)


async def test_events_are_validated_into_the_declared_model(
    respx_mock: respx.MockRouter, stream_client: StreamClient
) -> None:
    respx_mock.get("/events").mock(
        return_value=sse_response(
            'data: {"n": 1}\n\n',
            'data: {"n": 2}\n\n',
        )
    )

    assert await collect(stream_client.watch()) == [
        Chunk(n=1),
        Chunk(n=2),
    ]


async def test_events_split_across_chunks_are_reassembled(
    respx_mock: respx.MockRouter, stream_client: StreamClient
) -> None:
    respx_mock.get("/events").mock(
        return_value=sse_response('data: {"n', '": 1}\n', "\ndata", ': {"n": 2}\n\n')
    )

    assert await collect(stream_client.watch()) == [
        Chunk(n=1),
        Chunk(n=2),
    ]


@pytest.mark.parametrize("separator", ["\n", "\r\n", "\r"])
async def test_every_sse_line_ending_is_accepted(
    respx_mock: respx.MockRouter, stream_client: StreamClient, separator: str
) -> None:
    payload = separator.join(['data: {"n": 1}', "", 'data: {"n": 2}', "", ""])
    respx_mock.get("/events").mock(return_value=sse_response(payload))

    assert await collect(stream_client.watch()) == [
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

    events = await collect(stream_client.watch_raw())

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

    assert await collect(stream_client.watch_raw()) == [
        ServerSentEvent(event="no-data"),
        ServerSentEvent(),
    ]


async def test_malformed_field_values_are_ignored(
    respx_mock: respx.MockRouter, stream_client: StreamClient
) -> None:
    respx_mock.get("/raw").mock(
        return_value=sse_response(
            "id: with\x00null\nretry: soon\nunknown: field\ndata: payload\n\n"
        )
    )

    assert await collect(stream_client.watch_raw()) == [ServerSentEvent(data="payload")]


async def test_sse_requests_advertise_the_event_stream_media_type(
    respx_mock: respx.MockRouter, stream_client: StreamClient
) -> None:
    route = respx_mock.get("/events").mock(return_value=sse_response())

    await collect(stream_client.watch())

    headers = route.calls.last.request.headers
    assert headers["Accept"] == "text/event-stream"
    assert headers["Cache-Control"] == "no-store"


async def test_explicit_accept_header_wins_over_the_default(
    respx_mock: respx.MockRouter, stream_client: StreamClient
) -> None:
    route = respx_mock.get("/negotiated").mock(return_value=sse_response())

    await collect(stream_client.watch_negotiated(accept="text/plain"))

    assert route.calls.last.request.headers["Accept"] == "text/plain"


async def test_lowercase_accept_alias_replaces_the_default(
    respx_mock: respx.MockRouter, stream_client: StreamClient
) -> None:
    route = respx_mock.get("/negotiated").mock(return_value=sse_response())

    await collect(stream_client.watch_negotiated_lowercase(accept="text/plain"))

    assert route.calls.last.request.headers.get_list("accept") == ["text/plain"]


async def test_path_and_query_parameters_are_sent(
    respx_mock: respx.MockRouter, stream_client: StreamClient
) -> None:
    route = respx_mock.get("/topics/news").mock(return_value=sse_response())

    await collect(stream_client.watch_topic("news", since=7))

    assert route.calls.last.request.url.params["since"] == "7"


async def test_post_streams_send_their_body(
    respx_mock: respx.MockRouter, stream_client: StreamClient
) -> None:
    route = respx_mock.post("/chat").mock(
        return_value=sse_response('data: {"n": 2}\n\n')
    )

    assert await collect(stream_client.chat(Chunk(n=1))) == [Chunk(n=2)]

    request = route.calls.last.request
    assert json.loads(request.content) == {"n": 1}
    assert request.headers["Content-Type"] == "application/json"


async def test_timeout_override_is_forwarded(
    respx_mock: respx.MockRouter, stream_client: StreamClient
) -> None:
    route = respx_mock.get("/forever").mock(return_value=sse_response())

    await collect(stream_client.watch_forever())

    assert route.calls.last.request.extensions["timeout"]["read"] is None


async def test_event_data_validation_error_propagates(
    respx_mock: respx.MockRouter, stream_client: StreamClient
) -> None:
    respx_mock.get("/events").mock(
        return_value=sse_response('data: {"n": "not-an-int"}\n\n')
    )

    with pytest.raises(ValidationError):
        await collect(stream_client.watch())


async def test_error_status_raises_with_the_body_available(
    respx_mock: respx.MockRouter, stream_client: StreamClient
) -> None:
    respx_mock.get("/events").mock(
        return_value=httpx.Response(503, json={"detail": "overloaded"})
    )

    with pytest.raises(httpx2.HTTPStatusError) as excinfo:
        await collect(stream_client.watch())

    assert excinfo.value.response.json() == {"detail": "overloaded"}


async def test_error_status_is_streamed_when_not_raising(
    respx_mock: respx.MockRouter, http: httpx2.Client | httpx2.AsyncClient
) -> None:
    respx_mock.get("/raw").mock(
        return_value=sse_response("data: partial\n\n", status_code=503)
    )

    client = StreamClient(http, raise_for_status=False)
    events = await collect(client.watch_raw())

    assert events == [ServerSentEvent(data="partial")]


async def test_decode_options_apply_to_events(
    respx_mock: respx.MockRouter, http: httpx2.Client | httpx2.AsyncClient
) -> None:
    respx_mock.get("/events").mock(return_value=sse_response('data: {"n": "1"}\n\n'))

    client = StreamClient(http, decode_options={"strict": True})
    with pytest.raises(ValidationError):
        await collect(client.watch())


async def test_stream_escape_hatch_decodes_events_by_hand(
    respx_mock: respx.MockRouter, stream_client: StreamClient
) -> None:
    respx_mock.get("/raw").mock(return_value=sse_response("data: manual\n\n"))

    if isinstance(stream_client.http, httpx2.AsyncClient):
        async with stream_client.stream("GET", "/raw") as response:
            events = [event async for event in EventSource(response)]
    else:
        with stream_client.stream("GET", "/raw") as response:
            events = list(EventSource(response))

    assert events == [ServerSentEvent(data="manual")]


async def test_leaving_the_stream_early_releases_the_connection(
    respx_mock: respx.MockRouter, stream_client: StreamClient
) -> None:
    stream = ChunkStream('data: {"n": 1}\n\n', 'data: {"n": 2}\n\n')
    respx_mock.get("/events").mock(
        return_value=httpx.Response(
            200, headers={"content-type": "text/event-stream"}, stream=stream
        )
    )

    events = stream_client.watch()
    if isinstance(events, AsyncIterator):
        async with aclosing(events):
            assert await anext(events) == Chunk(n=1)
    else:
        with closing(events):
            assert next(events) == Chunk(n=1)

    assert stream.closed


@pytest.mark.parametrize(
    "annotation",
    [
        AsyncIterator[Chunk],
        AsyncIterable[Chunk],
        AsyncGenerator[Chunk, None],
        Iterator[Chunk],
        Iterable[Chunk],
        Generator[Chunk, None, None],
        Annotated[AsyncIterator[Chunk], "documented"],
    ],
)
def test_every_iterator_return_annotation_is_accepted(annotation) -> None:
    def watch(self) -> None: ...

    watch.__annotations__["return"] = annotation
    decorate: Any = sse("/events")

    assert decorate(watch).__name__ == "watch"


@pytest.mark.parametrize(
    "annotation",
    [Chunk, list[Chunk], AsyncIterator, AsyncIterator[None], httpx2.Response],
)
def test_non_iterator_return_annotation_raises(annotation) -> None:
    def watch(self) -> None: ...

    watch.__annotations__["return"] = annotation
    decorate: Any = sse("/events")

    with pytest.raises(TypeError, match="Iterator"):
        decorate(watch)


def test_missing_return_annotation_raises() -> None:
    with pytest.raises(TypeError, match="missing return annotation"):

        class BadClient(APIClient[ClientT]):
            @sse("/events")
            def watch(self): ...


def test_body_parameter_on_a_get_stream_raises() -> None:
    with pytest.raises(TypeError, match="body-supporting method"):

        class BadClient(APIClient[ClientT]):
            @sse("/events")
            def watch(self, data: Annotated[Chunk, Body()]) -> AsyncIterator[Chunk]: ...


def test_sse_and_plain_endpoints_coexist_on_one_client() -> None:
    class MixedClient(APIClient[ClientT]):
        @get("/events/latest")
        def latest(self) -> Chunk: ...

        @sse("/events")
        def watch(self) -> AsyncIterator[Chunk]: ...

    assert MixedClient.watch.__name__ == "watch"
    assert MixedClient.latest.__name__ == "latest"
