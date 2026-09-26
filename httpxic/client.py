from __future__ import annotations

from contextlib import asynccontextmanager, contextmanager
from typing import TYPE_CHECKING, Any, Generic, TypeVar, overload

import httpx2
from typing_extensions import TypeIs, Unpack

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Coroutine, Generator
    from contextlib import AbstractAsyncContextManager, AbstractContextManager

    from .types import DecodeOptions, RequestOptions

__all__ = ["APIClient", "ClientT"]

ClientT = TypeVar("ClientT", httpx2.Client, httpx2.AsyncClient)


class APIClient(Generic[ClientT]):
    """Base class for declarative, type-safe HTTP API clients.

    Wraps a caller-owned ``httpx2.Client`` or ``httpx2.AsyncClient``; its type
    decides whether endpoints are called synchronously or return coroutines.
    The client is neither opened nor closed here.
    """

    def __init__(
        self,
        http_client: ClientT,
        *,
        raise_for_status: bool = True,
        decode_options: DecodeOptions | None = None,
    ) -> None:
        self.http: ClientT = http_client
        self.is_async = isinstance(http_client, httpx2.AsyncClient)
        self.raise_for_status = raise_for_status
        self.decode_options: DecodeOptions = decode_options or {}

    @overload
    def request(
        self: APIClient[httpx2.Client],
        method: str,
        url: str | httpx2.URL,
        **kwargs: Unpack[RequestOptions],
    ) -> httpx2.Response: ...

    @overload
    def request(
        self: APIClient[httpx2.AsyncClient],
        method: str,
        url: str | httpx2.URL,
        **kwargs: Unpack[RequestOptions],
    ) -> Coroutine[Any, Any, httpx2.Response]: ...

    def request(
        self, method: str, url: str | httpx2.URL, **kwargs: Unpack[RequestOptions]
    ) -> httpx2.Response | Coroutine[Any, Any, httpx2.Response]:
        """Send a request and, unless disabled, raise on error status codes."""
        if self._is_async(self.http):
            return self._arequest(self.http, method, url, **kwargs)
        return self._check(self.http.request(method, url, **kwargs))

    @overload
    def stream(
        self: APIClient[httpx2.Client],
        method: str,
        url: str | httpx2.URL,
        **kwargs: Unpack[RequestOptions],
    ) -> AbstractContextManager[httpx2.Response]: ...

    @overload
    def stream(
        self: APIClient[httpx2.AsyncClient],
        method: str,
        url: str | httpx2.URL,
        **kwargs: Unpack[RequestOptions],
    ) -> AbstractAsyncContextManager[httpx2.Response]: ...

    def stream(
        self, method: str, url: str | httpx2.URL, **kwargs: Unpack[RequestOptions]
    ) -> (
        AbstractContextManager[httpx2.Response]
        | AbstractAsyncContextManager[httpx2.Response]
    ):
        """Open a response without reading its body, for streaming consumption.

        Unless disabled, an error status code is raised as usual - the body is
        read first, so it is still available on the raised exception.
        """
        if self._is_async(self.http):
            return self._astream(self.http, method, url, **kwargs)
        return self._stream(self.http, method, url, **kwargs)

    def _is_async(
        self, _http: httpx2.Client | httpx2.AsyncClient
    ) -> TypeIs[httpx2.AsyncClient]:
        return self.is_async

    def _check(self, response: httpx2.Response) -> httpx2.Response:
        if self.raise_for_status:
            response.raise_for_status()
        return response

    async def _arequest(
        self,
        http: httpx2.AsyncClient,
        method: str,
        url: str | httpx2.URL,
        **kwargs: Unpack[RequestOptions],
    ) -> httpx2.Response:
        response = await http.request(method, url, **kwargs)
        return self._check(response)

    @contextmanager
    def _stream(
        self,
        http: httpx2.Client,
        method: str,
        url: str | httpx2.URL,
        **kwargs: Unpack[RequestOptions],
    ) -> Generator[httpx2.Response, None, None]:
        with http.stream(method, url, **kwargs) as response:
            if self.raise_for_status and not response.is_success:
                response.read()
                response.raise_for_status()
            yield response

    @asynccontextmanager
    async def _astream(
        self,
        http: httpx2.AsyncClient,
        method: str,
        url: str | httpx2.URL,
        **kwargs: Unpack[RequestOptions],
    ) -> AsyncGenerator[httpx2.Response, None]:
        async with http.stream(method, url, **kwargs) as response:
            if self.raise_for_status and not response.is_success:
                await response.aread()
                response.raise_for_status()
            yield response
