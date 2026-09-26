from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal, TypedDict

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from httpx2._client import UseClientDefault
    from httpx2._types import (
        AuthTypes,
        HeaderTypes,
        QueryParamTypes,
        RequestContent,
        RequestData,
        RequestExtensions,
        RequestFiles,
        TimeoutTypes,
    )
    from pydantic.config import ExtraValues
    from pydantic.main import IncEx


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


class EndpointOptions(TypedDict, total=False):
    """Request options an endpoint adds to every call.

    ``headers`` and ``params`` may also be zero-argument callables, evaluated on
    every call (e.g. to read a ``ContextVar``); they are merged under the
    endpoint's ``Header()`` and ``Query()`` parameters, which win on conflicts.
    A ``Content-Type`` header
    decides how a ``Body()`` parameter is encoded: JSON media types
    (``application/json``, ``*/*+json``, the default) are JSON-encoded, any
    other media type is sent as is.
    """

    headers: Mapping[str, str] | Callable[[], Mapping[str, str]]
    params: Mapping[str, Any] | Callable[[], Mapping[str, Any]]
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
