from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal, TypedDict

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
    from ssl import SSLContext

    from httpx import URL, AsyncBaseTransport, Limits
    from httpx._client import UseClientDefault
    from httpx._types import (
        AuthTypes,
        CertTypes,
        CookieTypes,
        HeaderTypes,
        ProxyTypes,
        QueryParamTypes,
        RequestContent,
        RequestData,
        RequestExtensions,
        RequestFiles,
        TimeoutTypes,
    )
    from pydantic.config import ExtraValues
    from pydantic.main import IncEx


class ClientOptions(TypedDict, total=False):
    auth: AuthTypes | None
    params: QueryParamTypes | None
    headers: HeaderTypes | None
    cookies: CookieTypes | None
    verify: SSLContext | str | bool
    cert: CertTypes | None
    http1: bool
    http2: bool
    proxy: ProxyTypes | None
    mounts: Mapping[str, AsyncBaseTransport | None] | None
    timeout: TimeoutTypes
    follow_redirects: bool
    limits: Limits
    max_redirects: int
    event_hooks: Mapping[str, list[Callable[..., Any]]] | None
    base_url: URL | str
    transport: AsyncBaseTransport | None
    trust_env: bool
    default_encoding: str | Callable[[bytes], str]


class RequestOptions(TypedDict, total=False):
    content: RequestContent | None
    data: RequestData | None
    files: RequestFiles | None
    json: Any | None
    params: QueryParamTypes | None
    headers: HeaderTypes | None
    auth: AuthTypes | UseClientDefault | None
    follow_redirects: bool
    timeout: TimeoutTypes
    extensions: RequestExtensions | None


class DecodeOptions(TypedDict, total=False):
    strict: bool | None
    extra: ExtraValues | None
    context: Any | None
    by_alias: bool | None
    by_name: bool | None


class EncodeOptions(TypedDict, total=False):
    include: IncEx
    exclude: IncEx
    context: Any | None
    by_alias: bool
    exclude_unset: bool
    exclude_defaults: bool
    exclude_none: bool
    round_trip: bool
    warnings: bool | Literal["none", "warn", "error"]
    serialize_as_any: bool
