from __future__ import annotations

from contextlib import AsyncExitStack, asynccontextmanager
from typing import TYPE_CHECKING

from httpx import URL, AsyncClient
from typing_extensions import Self, Unpack

from .exceptions import ClientClosedError

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator
    from types import TracebackType

    from httpx import Response
    from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

    from .types import ClientOptions, DecodeOptions, RequestOptions
else:
    try:
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
    except ImportError:  # pragma: no cover - the `opentelemetry` extra is optional
        HTTPXClientInstrumentor = None

_INSTRUMENTED_FLAG = "_is_instrumented_by_opentelemetry"


class APIClient:
    """Base class for declarative, type-safe HTTP API clients.

    Either wraps a caller-supplied ``httpx.AsyncClient`` or builds its own from
    ``client_options``; passing both is an error.

    Ownership follows who created the underlying client. A self-created client
    is owned by this instance: ``async with`` opens it, and ``aclose()`` or
    ``async with`` exit closes it - such an instance is single-use, and
    re-entering it after it has been closed raises ``ClientClosedError``. A
    caller-supplied client stays owned by the caller: it is neither opened nor
    closed here, and the context manager is just a no-op wrapper around it.

    ``instrument`` controls OpenTelemetry instrumentation of the underlying
    client. The default ``None`` instruments only a client this instance
    created itself, leaving a caller-supplied client untouched; explicit
    ``True``/``False`` forces the behavior either way. A client is never
    instrumented twice.
    """

    def __init__(
        self,
        http_client: AsyncClient | None = None,
        *,
        raise_for_status: bool = True,
        instrument: bool | None = None,
        decode_options: DecodeOptions | None = None,
        **client_options: Unpack[ClientOptions],
    ) -> None:
        if http_client is not None and client_options:
            msg = "Use only either 'http_client' or 'client_options'"
            raise ValueError(msg)

        self._owns_http = http_client is None
        self.http = (
            AsyncClient(**client_options) if http_client is None else http_client
        )
        self.raise_for_status = raise_for_status
        self.decode_options: DecodeOptions = decode_options or {}
        self._stack = AsyncExitStack()
        if self._owns_http:
            self._stack.push_async_exit(self.http)
        if instrument is None:
            instrument = self._owns_http
        if (
            instrument
            and HTTPXClientInstrumentor is not None
            and not getattr(self.http, _INSTRUMENTED_FLAG, False)
        ):
            HTTPXClientInstrumentor.instrument_client(self.http)

    async def request(
        self, method: str, url: str | URL, **kwargs: Unpack[RequestOptions]
    ) -> Response:
        """Send a request and, unless disabled, raise on error status codes."""
        response = await self.http.request(method, url, **kwargs)
        if self.raise_for_status:
            response.raise_for_status()
        return response

    @asynccontextmanager
    async def stream(
        self, method: str, url: str | URL, **kwargs: Unpack[RequestOptions]
    ) -> AsyncGenerator[Response, None]:
        """Open a response without reading its body, for streaming consumption.

        Unless disabled, an error status code is raised as usual - the body is
        read first, so it is still available on the raised exception.
        """
        async with self.http.stream(method, url, **kwargs) as response:
            if self.raise_for_status and not response.is_success:
                await response.aread()
                response.raise_for_status()
            yield response

    async def get(self, url: str | URL, **kwargs: Unpack[RequestOptions]) -> Response:
        """Send a ``GET`` request."""
        return await self.request("GET", url, **kwargs)

    async def post(self, url: str | URL, **kwargs: Unpack[RequestOptions]) -> Response:
        """Send a ``POST`` request."""
        return await self.request("POST", url, **kwargs)

    async def put(self, url: str | URL, **kwargs: Unpack[RequestOptions]) -> Response:
        """Send a ``PUT`` request."""
        return await self.request("PUT", url, **kwargs)

    async def patch(self, url: str | URL, **kwargs: Unpack[RequestOptions]) -> Response:
        """Send a ``PATCH`` request."""
        return await self.request("PATCH", url, **kwargs)

    async def delete(
        self, url: str | URL, **kwargs: Unpack[RequestOptions]
    ) -> Response:
        """Send a ``DELETE`` request."""
        return await self.request("DELETE", url, **kwargs)

    async def head(self, url: str | URL, **kwargs: Unpack[RequestOptions]) -> Response:
        """Send a ``HEAD`` request."""
        return await self.request("HEAD", url, **kwargs)

    async def options(
        self, url: str | URL, **kwargs: Unpack[RequestOptions]
    ) -> Response:
        """Send an ``OPTIONS`` request."""
        return await self.request("OPTIONS", url, **kwargs)

    async def aclose(self) -> None:
        """Release this instance, closing the underlying client if it is owned."""
        await self._stack.aclose()

    async def __aenter__(self) -> Self:
        """Enter the client scope, opening the underlying client if it is owned."""
        if self.http.is_closed:
            raise ClientClosedError(type(self).__name__)
        if self._owns_http:
            await self.http.__aenter__()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        """Leave the client scope, closing the underlying client if it is owned."""
        await self._stack.__aexit__(exc_type, exc_val, exc_tb)
