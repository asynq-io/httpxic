from __future__ import annotations

import collections.abc
import enum
import functools
import inspect
import re
import typing
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Concatenate, Literal, TypeVar, cast
from urllib.parse import quote

import httpx
from pydantic import TypeAdapter
from typing_extensions import ParamSpec

from .events import ServerSentEvent, aiter_sse
from .exceptions import EmptyResponseError
from .params import Body, Param, Path, Query
from .types import EncodeOptions, RequestOptions

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable, Mapping

    from httpx._types import TimeoutTypes

    from .client import APIClient
    from .types import DecodeOptions

__all__ = ["delete", "get", "head", "options", "patch", "post", "put", "sse"]

P = ParamSpec("P")
R = TypeVar("R")
SelfT = TypeVar("SelfT", bound="APIClient")

_PATH_PATTERN = re.compile(r"\{(\w+)\}")
_BODY_METHODS = frozenset({"PATCH", "POST", "PUT"})
_ALIAS_KINDS = frozenset({"cookie", "header", "query"})
_BODY_KINDS = frozenset({"body", "file", "form"})
_SUPPORTED_KINDS = _ALIAS_KINDS | _BODY_KINDS | {"path"}
_VAR_KINDS = frozenset(
    {inspect.Parameter.VAR_KEYWORD, inspect.Parameter.VAR_POSITIONAL}
)
_COOKIE_HEADER = "Cookie"
_COOKIE_SEPARATOR = "; "
# RFC 6265: control characters, whitespace, double quote, comma, semicolon and
# backslash are not allowed in cookie names or values.
_COOKIE_FORBIDDEN = re.compile(r'[\x00-\x20\x7f",;\\]')
_ADAPTER_CACHE_SIZE = 1024
_NO_HEADERS: Mapping[str, str] = {}
_SSE_HEADERS: Mapping[str, str] = {
    "Accept": "text/event-stream",
    "Cache-Control": "no-store",
}
_ASYNC_ITERATOR_ORIGINS = frozenset(
    {
        collections.abc.AsyncGenerator,
        collections.abc.AsyncIterable,
        collections.abc.AsyncIterator,
    }
)


@functools.lru_cache(maxsize=_ADAPTER_CACHE_SIZE)
def _adapter(tp: Any) -> TypeAdapter[Any]:
    """Reuse the adapter for a type; endpoints keep the one they were built with."""
    return TypeAdapter(tp)


@dataclass(slots=True)
class _ResolvedParam:
    marker: Param | Body
    name: str

    @property
    def kind(self) -> str:
        return self.marker.kind

    @property
    def alias(self) -> str | None:
        """Explicit alias of the marker, or ``None`` when it cannot be aliased."""
        return self.marker.alias if isinstance(self.marker, Param) else None

    @property
    def field_name(self) -> str:
        """Name used on the wire: the alias when set, the parameter name otherwise."""
        return self.alias or self.name


@dataclass(slots=True)
class _EndpointConfig:
    qualname: str
    signature: inspect.Signature
    path: str
    params: list[_ResolvedParam]
    has_json_body: bool
    body_adapter: TypeAdapter[Any] | None
    serializer_options: EncodeOptions
    response_adapter: TypeAdapter[Any] | None
    response_allows_none: bool
    raw_response: bool
    default_headers: Mapping[str, str]


def _empty_alias_buckets() -> dict[str, dict[str, Any]]:
    return {kind: {} for kind in _ALIAS_KINDS}


def _empty_multipart_buckets() -> dict[str, dict[str, Any]]:
    return {"file": {}, "form": {}}


@dataclass(slots=True)
class _CollectedValues:
    path: dict[str, str] = field(default_factory=dict)
    alias: dict[str, dict[str, Any]] = field(default_factory=_empty_alias_buckets)
    multipart: dict[str, dict[str, Any]] = field(
        default_factory=_empty_multipart_buckets
    )
    body: Any = None


def _strip_annotated(tp: Any) -> Any:
    if typing.get_origin(tp) is typing.Annotated:
        return typing.get_args(tp)[0]
    return tp


def _marker_from_annotation(tp: Any) -> Param | Body | None:
    if typing.get_origin(tp) is typing.Annotated:
        for meta in typing.get_args(tp)[1:]:
            if isinstance(meta, (Param, Body)):
                return meta
    elif typing.get_origin(tp) is typing.Union:
        for arg in typing.get_args(tp):
            marker = _marker_from_annotation(arg)
            if marker is not None:
                return marker
    return None


def _allows_none(tp: Any) -> bool:
    return tp is None or tp is type(None) or type(None) in typing.get_args(tp)


def _event_type(func: Any, return_type: Any) -> Any:
    """Unwrap the event type declared by an ``-> AsyncIterator[Event]`` annotation."""
    args = typing.get_args(return_type) or (None,)
    event_type = (
        _strip_annotated(args[0])
        if typing.get_origin(return_type) in _ASYNC_ITERATOR_ORIGINS
        else None
    )
    if event_type is None or event_type is type(None):
        msg = (
            f"{func.__qualname__}: @sse endpoints must be annotated "
            f"'-> AsyncIterator[<event model>]', where the event model is either "
            f"ServerSentEvent or a type the event data is validated against"
        )
        raise TypeError(msg)
    return event_type


def _return_type(func: Any, hints: dict[str, Any]) -> Any:
    if "return" not in hints:
        msg = (
            f"{func.__qualname__}: missing return annotation — annotate the return type "
            f"explicitly (use '-> None' for endpoints that discard the response body)"
        )
        raise TypeError(msg)
    return _strip_annotated(hints["return"])


def _implicit_marker(
    func: Any,
    name: str,
    path_names: frozenset[str],
    *,
    has_body: bool,
    body_found: bool,
) -> Param | Body:
    if name in path_names:
        return Path()
    if not has_body:
        return Query()
    if body_found:
        msg = (
            f"{func.__qualname__}: unannotated parameter {name!r} is ambiguous on a "
            f"POST/PUT/PATCH endpoint — annotate it explicitly with Body(), Query(), etc."
        )
        raise TypeError(msg)
    return Body()


def _categorize_params(
    func: Any,
    signature: inspect.Signature,
    path_names: frozenset[str],
    hints: dict[str, Any],
    *,
    has_body: bool,
) -> list[_ResolvedParam]:
    params: list[_ResolvedParam] = []
    body_found = False

    for name, param in signature.parameters.items():
        if name == "self" or param.kind in _VAR_KINDS:
            continue
        marker = _marker_from_annotation(hints.get(name))
        if marker is None:
            marker = _implicit_marker(
                func, name, path_names, has_body=has_body, body_found=body_found
            )
        body_found = body_found or marker.kind in _BODY_KINDS
        params.append(_ResolvedParam(marker, name))

    return params


def _body_adapter(
    params: list[_ResolvedParam], hints: dict[str, Any]
) -> TypeAdapter[Any] | None:
    for p in params:
        if p.kind == "body":
            tp = _strip_annotated(hints.get(p.name))
            return _adapter(tp) if tp is not None else None
    return None


def _validate_params(
    func: Any,
    params: list[_ResolvedParam],
    path_names: frozenset[str],
    *,
    has_body: bool,
) -> None:
    unsupported = [p for p in params if p.kind not in _SUPPORTED_KINDS]
    if unsupported:
        kinds = ", ".join(sorted({repr(p.kind) for p in unsupported}))
        supported = ", ".join(sorted(_SUPPORTED_KINDS))
        msg = (
            f"{func.__qualname__}: unsupported marker kind(s) {kinds} — "
            f"supported kinds are {supported}"
        )
        raise TypeError(msg)

    unused = path_names - {p.name for p in params if p.kind == "path"}
    if unused:
        missing = ", ".join(f"{{{n}}}" for n in sorted(unused))
        msg = f"{func.__qualname__}: path placeholder(s) {missing} have no corresponding parameter"
        raise TypeError(msg)

    stray = [p.name for p in params if p.kind == "path" and p.name not in path_names]
    if stray:
        names = ", ".join(repr(n) for n in stray)
        msg = f"{func.__qualname__}: path parameter(s) {names} have no matching placeholder in the URL"
        raise TypeError(msg)

    body_params = [p for p in params if p.kind in _BODY_KINDS]
    if body_params and not has_body:
        kinds = ", ".join(sorted({p.kind for p in body_params}))
        msg = (
            f"{func.__qualname__}: {kinds} parameters require a body-supporting method"
        )
        raise TypeError(msg)

    json_params = [p for p in body_params if p.kind == "body"]
    if len(json_params) > 1:
        names = ", ".join(repr(p.name) for p in json_params)
        msg = f"{func.__qualname__}: multiple body parameters ({names}) are ambiguous — only one is allowed"
        raise TypeError(msg)
    if json_params and len(body_params) > 1:
        msg = f"{func.__qualname__}: Body() cannot be combined with Form()/File() parameters"
        raise TypeError(msg)


def _normalize_value(value: Any) -> Any:
    """Unwrap enums (also inside lists/tuples) so they serialize by value."""
    if isinstance(value, enum.Enum):
        return value.value
    if isinstance(value, (list, tuple)):
        return [_normalize_value(item) for item in value]
    return value


def _quote_path_value(value: Any) -> str:
    quoted = quote(str(_normalize_value(value)), safe="")
    if quoted in {".", ".."}:
        # httpx resolves dot-segments per RFC 3986, so a literal "." or ".."
        # would escape its path segment; encode the dots to keep it in place.
        return quoted.replace(".", "%2E")
    return quoted


def _collect_values(
    params: list[_ResolvedParam], arguments: dict[str, Any]
) -> _CollectedValues:
    collected = _CollectedValues()
    for p in params:
        value = arguments[p.name]
        if p.kind == "path":
            collected.path[p.name] = _quote_path_value(value)
        elif p.kind in _ALIAS_KINDS:
            if value is not None:
                collected.alias[p.kind][p.field_name] = _normalize_value(value)
        elif p.kind == "body":
            collected.body = value
        elif value is not None:
            collected.multipart[p.kind][p.field_name] = value
    return collected


def _build_url(path: str, path_values: dict[str, str]) -> str:
    if not path_values:
        return path
    return _PATH_PATTERN.sub(lambda match: path_values[match.group(1)], path)


def _apply_body(
    config: _EndpointConfig, collected: _CollectedValues, options: RequestOptions
) -> None:
    if collected.multipart["form"]:
        options["data"] = collected.multipart["form"]
    if collected.multipart["file"]:
        options["files"] = collected.multipart["file"]
    if not config.has_json_body:
        return
    if config.body_adapter is not None:
        options["content"] = config.body_adapter.dump_json(
            collected.body, **config.serializer_options
        )
    else:
        options["json"] = collected.body


def _cookie_pair(name: str, value: Any) -> str:
    """Render one ``name=value`` cookie pair, rejecting RFC 6265 violations."""
    for part in (name, str(value)):
        if _COOKIE_FORBIDDEN.search(part):
            msg = (
                f"cookie {name!r}: {part!r} contains a character forbidden in "
                f"the Cookie header (control characters, whitespace, and "
                f"'\",;\\' are not allowed)"
            )
            raise ValueError(msg)
    return f"{name}={value}"


def _merge_cookie_header(headers: dict[str, Any], cookies: dict[str, Any]) -> None:
    """Fold ``Cookie()`` values into a single RFC 6265 ``Cookie`` request header.

    A ``Header(alias="Cookie")`` declared on the same endpoint keeps its casing
    and stays in front; the ``Cookie()`` pairs are appended after it, so neither
    side is dropped.
    """
    key = next(
        (name for name in headers if name.lower() == _COOKIE_HEADER.lower()),
        _COOKIE_HEADER,
    )
    pairs = [_cookie_pair(name, value) for name, value in cookies.items()]
    explicit = headers.get(key)
    if explicit:
        pairs.insert(0, str(explicit))
    headers[key] = _COOKIE_SEPARATOR.join(pairs)


def _build_headers(
    config: _EndpointConfig, collected: _CollectedValues
) -> dict[str, Any]:
    headers: dict[str, Any] = dict(config.default_headers)
    if config.has_json_body:
        headers["Content-Type"] = "application/json"
    for name, value in collected.alias["header"].items():
        default = next((k for k in headers if k.lower() == name.lower()), None)
        if default is not None:
            del headers[default]
        headers[name] = value
    if collected.alias["cookie"]:
        _merge_cookie_header(headers, collected.alias["cookie"])
    return headers


def _prepare_request(
    config: _EndpointConfig, arguments: dict[str, Any]
) -> tuple[str, RequestOptions]:
    collected = _collect_values(config.params, arguments)
    options: RequestOptions = {}
    if collected.alias["query"]:
        options["params"] = collected.alias["query"]
    headers = _build_headers(config, collected)
    if headers:
        options["headers"] = headers
    _apply_body(config, collected, options)
    return _build_url(config.path, collected.path), options


def _parse_response(
    config: _EndpointConfig, response: httpx.Response, decode_options: DecodeOptions
) -> Any:
    if config.raw_response:
        return response
    if not response.content:
        if config.response_allows_none:
            return None
        raise EmptyResponseError(config.qualname, response.status_code)
    if config.response_adapter is None:
        return None
    return config.response_adapter.validate_json(response.content, **decode_options)


def _parse_event(
    config: _EndpointConfig,
    event: ServerSentEvent,
    decode_options: DecodeOptions,
) -> Any:
    """Validate one event's ``data`` field, or pass the event through untouched."""
    if config.response_adapter is None:
        return event
    return config.response_adapter.validate_json(event.data, **decode_options)


def _serializer_options(overrides: EncodeOptions | None) -> EncodeOptions:
    if overrides is None:
        return EncodeOptions(by_alias=True)
    return overrides.copy()


def _build_config(
    func: Any,
    method: str,
    path: str,
    *,
    serializer_options: EncodeOptions | None,
    stream: bool = False,
) -> _EndpointConfig:
    has_body = method in _BODY_METHODS
    path_names = frozenset(_PATH_PATTERN.findall(path))
    signature = inspect.signature(func)
    hints = typing.get_type_hints(func, include_extras=True)
    return_type = _return_type(func, hints)
    if stream:
        return_type = _event_type(func, return_type)
    params = _categorize_params(func, signature, path_names, hints, has_body=has_body)
    _validate_params(func, params, path_names, has_body=has_body)
    raw_response = return_type is (ServerSentEvent if stream else httpx.Response)
    return _EndpointConfig(
        qualname=func.__qualname__,
        signature=signature,
        path=path,
        params=params,
        has_json_body=any(p.kind == "body" for p in params),
        body_adapter=_body_adapter(params, hints),
        serializer_options=_serializer_options(serializer_options),
        response_adapter=None
        if raw_response or return_type is type(None)
        else _adapter(return_type),
        response_allows_none=_allows_none(return_type),
        raw_response=raw_response,
        default_headers=_SSE_HEADERS if stream else _NO_HEADERS,
    )


def _make_decorator(
    method: str,
    path: str,
    *,
    timeout: TimeoutTypes | None = None,
    serializer_options: EncodeOptions | None = None,
) -> Callable[
    [Callable[Concatenate[SelfT, P], Awaitable[R]]],
    Callable[Concatenate[SelfT, P], Awaitable[R]],
]:
    def wrap(
        func: Callable[Concatenate[SelfT, P], Awaitable[R]],
    ) -> Callable[Concatenate[SelfT, P], Awaitable[R]]:
        config = _build_config(
            func,
            method,
            path,
            serializer_options=serializer_options,
        )

        @functools.wraps(func)
        async def wrapper(self: SelfT, /, *args: P.args, **kwargs: P.kwargs) -> R:
            bound = config.signature.bind(self, *args, **kwargs)
            bound.apply_defaults()
            url, options = _prepare_request(config, bound.arguments)
            if timeout is not None:
                options["timeout"] = timeout
            response = await self.request(method, url, **options)
            return cast("R", _parse_response(config, response, self.decode_options))

        return wrapper

    return wrap


def get(
    path: str,
    *,
    timeout: TimeoutTypes | None = None,
) -> Callable[
    [Callable[Concatenate[SelfT, P], Awaitable[R]]],
    Callable[Concatenate[SelfT, P], Awaitable[R]],
]:
    """Declare the decorated method as a ``GET`` request to ``path``."""
    return _make_decorator("GET", path, timeout=timeout)


def post(
    path: str,
    *,
    timeout: TimeoutTypes | None = None,
    serializer_options: EncodeOptions | None = None,
) -> Callable[
    [Callable[Concatenate[SelfT, P], Awaitable[R]]],
    Callable[Concatenate[SelfT, P], Awaitable[R]],
]:
    """Declare the decorated method as a ``POST`` request to ``path``."""
    return _make_decorator(
        "POST", path, timeout=timeout, serializer_options=serializer_options
    )


def put(
    path: str,
    *,
    timeout: TimeoutTypes | None = None,
    serializer_options: EncodeOptions | None = None,
) -> Callable[
    [Callable[Concatenate[SelfT, P], Awaitable[R]]],
    Callable[Concatenate[SelfT, P], Awaitable[R]],
]:
    """Declare the decorated method as a ``PUT`` request to ``path``."""
    return _make_decorator(
        "PUT", path, timeout=timeout, serializer_options=serializer_options
    )


def patch(
    path: str,
    *,
    timeout: TimeoutTypes | None = None,
    serializer_options: EncodeOptions | None = None,
) -> Callable[
    [Callable[Concatenate[SelfT, P], Awaitable[R]]],
    Callable[Concatenate[SelfT, P], Awaitable[R]],
]:
    """Declare the decorated method as a ``PATCH`` request to ``path``."""
    return _make_decorator(
        "PATCH", path, timeout=timeout, serializer_options=serializer_options
    )


def delete(
    path: str,
    *,
    timeout: TimeoutTypes | None = None,
) -> Callable[
    [Callable[Concatenate[SelfT, P], Awaitable[R]]],
    Callable[Concatenate[SelfT, P], Awaitable[R]],
]:
    """Declare the decorated method as a ``DELETE`` request to ``path``."""
    return _make_decorator("DELETE", path, timeout=timeout)


def head(
    path: str,
    *,
    timeout: TimeoutTypes | None = None,
) -> Callable[
    [Callable[Concatenate[SelfT, P], Awaitable[R]]],
    Callable[Concatenate[SelfT, P], Awaitable[R]],
]:
    """Declare the decorated method as a ``HEAD`` request to ``path``."""
    return _make_decorator("HEAD", path, timeout=timeout)


def options(
    path: str,
    *,
    timeout: TimeoutTypes | None = None,
) -> Callable[
    [Callable[Concatenate[SelfT, P], Awaitable[R]]],
    Callable[Concatenate[SelfT, P], Awaitable[R]],
]:
    """Declare the decorated method as an ``OPTIONS`` request to ``path``."""
    return _make_decorator("OPTIONS", path, timeout=timeout)


def sse(
    path: str,
    *,
    method: Literal["GET", "POST"] = "GET",
    timeout: TimeoutTypes | None = None,
    serializer_options: EncodeOptions | None = None,
) -> Callable[
    [Callable[Concatenate[SelfT, P], AsyncIterator[R]]],
    Callable[Concatenate[SelfT, P], AsyncIterator[R]],
]:
    """Declare the decorated method as a server-sent events stream from ``path``.

    The method becomes an async generator, so declare it as a plain ``def``
    returning ``AsyncIterator[Event]`` and iterate it with ``async for``. Every
    dispatched event is validated against ``Event`` from its ``data`` field -
    unless ``Event`` is ``ServerSentEvent``, in which case the decoded event is
    yielded as it arrived, giving access to its type, id and retry hint.

    Note that the read timeout of an idle stream is the client's; pass e.g.
    ``timeout=httpx.Timeout(5.0, read=None)`` for streams that may pause longer
    than that.
    """

    def wrap(
        func: Callable[Concatenate[SelfT, P], AsyncIterator[R]],
    ) -> Callable[Concatenate[SelfT, P], AsyncIterator[R]]:
        config = _build_config(
            func,
            method,
            path,
            serializer_options=serializer_options,
            stream=True,
        )

        @functools.wraps(func)
        async def wrapper(
            self: SelfT, /, *args: P.args, **kwargs: P.kwargs
        ) -> AsyncIterator[R]:
            bound = config.signature.bind(self, *args, **kwargs)
            bound.apply_defaults()
            url, options = _prepare_request(config, bound.arguments)
            if timeout is not None:
                options["timeout"] = timeout
            async with self.stream(method, url, **options) as response:
                async for event in aiter_sse(response.aiter_lines()):
                    yield cast("R", _parse_event(config, event, self.decode_options))

        return wrapper

    return wrap
