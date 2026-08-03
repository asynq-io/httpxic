from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from collections.abc import AsyncIterable, AsyncIterator

__all__ = ["DEFAULT_EVENT_TYPE", "ServerSentEvent", "aiter_sse"]

DEFAULT_EVENT_TYPE: Final = "message"

_BOM: Final = "\ufeff"
_COMMENT_PREFIX: Final = ":"
_FIELD_SEPARATOR: Final = ":"
_NULL: Final = "\x00"
_DATA_SEPARATOR: Final = "\n"


@dataclass(slots=True, frozen=True)
class ServerSentEvent:
    """A single event dispatched by a ``text/event-stream`` response."""

    data: str
    event: str = DEFAULT_EVENT_TYPE
    id: str | None = None
    retry: int | None = None


@dataclass(slots=True)
class _EventDecoder:
    """Incremental decoder turning ``text/event-stream`` lines into events."""

    data: list[str] = field(default_factory=list)
    event: str = DEFAULT_EVENT_TYPE
    last_id: str | None = None
    retry: int | None = None

    def feed(self, line: str) -> ServerSentEvent | None:
        """Consume one line, returning an event if that line dispatches one."""
        if not line:
            return self._dispatch()
        if line.startswith(_COMMENT_PREFIX):
            return None
        name, _, value = line.partition(_FIELD_SEPARATOR)
        self._set(name, value.removeprefix(" "))
        return None

    def _set(self, name: str, value: str) -> None:
        if name == "data":
            self.data.append(value)
        elif name == "event":
            self.event = value
        elif name == "id":
            if _NULL not in value:
                self.last_id = value
        elif name == "retry" and value.isascii() and value.isdigit():
            self.retry = int(value)

    def _dispatch(self) -> ServerSentEvent | None:
        """Emit the buffered event, unless no ``data`` field was seen for it."""
        event = (
            ServerSentEvent(
                data=_DATA_SEPARATOR.join(self.data),
                event=self.event,
                id=self.last_id,
                retry=self.retry,
            )
            if self.data
            else None
        )
        self.data.clear()
        self.event = DEFAULT_EVENT_TYPE
        self.retry = None
        return event


async def aiter_sse(lines: AsyncIterable[str]) -> AsyncIterator[ServerSentEvent]:
    """Decode the lines of a ``text/event-stream`` body into server-sent events.

    Pair with ``httpx.Response.aiter_lines()`` to consume a stream opened by
    hand; the ``@sse`` decorator does this for declared endpoints. An event
    block that the stream ends in the middle of is discarded, as the SSE
    specification requires.
    """
    decoder = _EventDecoder()
    first = True
    async for line in lines:
        if first:
            line = line.removeprefix(_BOM)
            first = False
        event = decoder.feed(line)
        if event is not None:
            yield event
