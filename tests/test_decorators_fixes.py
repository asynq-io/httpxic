from __future__ import annotations

import enum
import json
import warnings
from typing import TYPE_CHECKING, Annotated, Any, ClassVar, Union

import httpx
import httpx2
import pytest
from pydantic import BaseModel

from httpxic import (
    APIClient,
    Body,
    ClientT,
    Cookie,
    EmptyResponseError,
    File,
    Form,
    Header,
    HttpxicError,
    Path,
    Query,
    get,
    post,
)
from httpxic.decorators import (
    _adapter,
    _marker_from_annotation,
    _quote_path_value,
    _ResolvedParam,
    _serializer_options,
)
from tests.conftest import resolve

if TYPE_CHECKING:
    import respx

pytestmark = pytest.mark.anyio


class Item(BaseModel):
    id: int
    name: str


class Color(enum.Enum):
    RED = "red"
    BLUE = "blue"


class Weird(Query):
    kind: ClassVar[str] = "weird"


class FixClient(APIClient[ClientT]):
    @post("/form")
    def submit_form(
        self,
        name: Annotated[str, Form()],
        age: Annotated[int, Form()],
    ) -> None: ...

    @post("/upload")
    def upload(
        self,
        meta: Annotated[str, Form()],
        payload: Annotated[bytes, File()],
    ) -> None: ...

    @post("/form-optional")
    def submit_optional_form(
        self,
        name: Annotated[str, Form()],
        nick: Annotated[str | None, Form()] = None,
    ) -> None: ...

    @post("/upload-optional")
    def upload_optional_file(
        self,
        meta: Annotated[str, Form()],
        payload: Annotated[bytes | None, File()] = None,
    ) -> None: ...

    @post("/trigger")
    def trigger(self, job_id: Annotated[int, Query()]) -> None: ...

    @get("/req")
    def req_query(self, q: Annotated[str, Query()]) -> dict: ...

    @get("/enum-query")
    def enum_query(self, c: Annotated[Color, Query()]) -> dict: ...

    @get("/enum-query-list")
    def enum_query_list(self, c: Annotated[list[Color], Query()]) -> dict: ...

    @get("/enum-header")
    def enum_header(self, c: Annotated[Color, Header(alias="X-Color")]) -> dict: ...

    @get("/p/{uid}")
    def path_enum(self, uid: Color) -> dict: ...

    @get("/p/{uid}")
    def path_str(self, uid: str) -> dict: ...

    @get("/unknown/{uid}")
    def unknown(self, uid: int) -> dict: ...

    @get("/kwonly/{uid}")
    def kwonly(self, uid: int, *, a: Annotated[int, Query()] = 0) -> dict: ...

    @get("/var/{uid}")
    def with_var_kwargs(self, uid: int, **extra: Any) -> dict: ...

    @get("/raw")
    def raw(self) -> httpx2.Response: ...

    @get("/item")
    def strict_item(self) -> Item: ...

    @get("/maybe")
    def maybe_item(self) -> Item | None: ...

    @get("/slow", timeout=2.5)
    def slow(self) -> dict: ...

    @get("/slow-obj", timeout=httpx2.Timeout(1.0))
    def slow_obj(self) -> dict: ...

    @get("/implicit")
    def implicit_query(self, q: str = "default") -> dict: ...

    @get("/doc-meta")
    def doc_meta_query(self, q: Annotated[str, "docs"] = "x") -> dict: ...

    @get("/cookie")
    def with_cookie(self, sid: Annotated[str, Cookie(alias="session")]) -> dict: ...

    @get("/cookies")
    def with_cookies(
        self,
        sid: Annotated[str, Cookie(alias="session")],
        theme: Annotated[str, Cookie()],
        trace: Annotated[str | None, Header(alias="X-Trace")] = None,
        opt: Annotated[str | None, Cookie(alias="opt")] = None,
    ) -> dict: ...

    @get("/cookie-header")
    def cookie_plus_cookie_header(
        self,
        sid: Annotated[str, Cookie(alias="session")],
        raw: Annotated[str, Header(alias="cookie")],
    ) -> dict: ...

    @get("/bad-cookie-name")
    def with_bad_cookie_name(
        self, v: Annotated[str, Cookie(alias="bad name")]
    ) -> dict: ...

    @post("/override-content-type")
    def override_content_type(
        self,
        data: Annotated[dict, Body()],
        content_type: Annotated[str, Header(alias="content-type")],
    ) -> None: ...

    @post("/cookie-body")
    def cookie_with_json_body(
        self,
        data: Annotated[dict, Body()],
        sid: Annotated[str, Cookie(alias="session")],
    ) -> None: ...

    @post("/untyped")
    def untyped_body(self, data) -> None: ...

    @get("/ignored")
    def ignored_body(self) -> None: ...


@pytest.fixture
def fix_client(http: httpx2.Client | httpx2.AsyncClient) -> FixClient[Any]:
    return FixClient(http)


# --- issue 1: explicit Body() plus an unannotated param is ambiguous ---------


def test_explicit_body_plus_unannotated_param_raises() -> None:
    with pytest.raises(TypeError, match="ambiguous"):

        class BadClient(APIClient[ClientT]):
            @post("/x")
            def f(self, data: Annotated[dict, Body()], flag: str = "on") -> None: ...


def test_two_explicit_body_params_raise() -> None:
    with pytest.raises(TypeError, match="ambiguous"):

        class BadClient(APIClient[ClientT]):
            @post("/x")
            def f(
                self,
                data: Annotated[dict, Body()],
                other: Annotated[dict, Body()],
            ) -> None: ...


def test_body_combined_with_form_raises() -> None:
    with pytest.raises(TypeError, match="cannot be combined"):

        class BadClient(APIClient[ClientT]):
            @post("/x")
            def f(
                self,
                data: Annotated[dict, Body()],
                field: Annotated[str, Form()],
            ) -> None: ...


# --- issue 2: multiple form fields must not collapse ------------------------


async def test_multiple_form_fields_are_all_sent(
    fix_client: FixClient, respx_mock: respx.MockRouter
) -> None:
    route = respx_mock.post("/form").mock(return_value=httpx.Response(204))

    await resolve(fix_client.submit_form(name="x", age=3))

    request = route.calls.last.request
    assert request.headers["content-type"] == "application/x-www-form-urlencoded"
    assert request.content == b"name=x&age=3"


# --- issue 3: Form and File must combine into one multipart body ------------


async def test_form_and_file_are_sent_together(
    fix_client: FixClient, respx_mock: respx.MockRouter
) -> None:
    route = respx_mock.post("/upload").mock(return_value=httpx.Response(204))

    await resolve(fix_client.upload(meta="m", payload=b"data"))

    request = route.calls.last.request
    assert request.headers["content-type"].startswith("multipart/form-data")
    assert b'name="meta"' in request.content
    assert b"m" in request.content
    assert b'name="payload"' in request.content
    assert b"data" in request.content


async def test_none_form_field_is_skipped(
    fix_client: FixClient, respx_mock: respx.MockRouter
) -> None:
    route = respx_mock.post("/form-optional").mock(return_value=httpx.Response(204))

    await resolve(fix_client.submit_optional_form(name="x"))

    assert route.calls.last.request.content == b"name=x"


async def test_none_file_field_is_skipped(
    fix_client: FixClient, respx_mock: respx.MockRouter
) -> None:
    route = respx_mock.post("/upload-optional").mock(return_value=httpx.Response(204))

    await resolve(fix_client.upload_optional_file(meta="m"))

    request = route.calls.last.request
    assert request.headers["content-type"] == "application/x-www-form-urlencoded"
    assert request.content == b"meta=m"


# --- issue 4: body markers on a bodyless method are rejected ----------------


def test_body_marker_on_get_raises_at_definition_time() -> None:
    with pytest.raises(TypeError, match=r"BadClient\.search: body .*body-supporting"):

        class BadClient(APIClient[ClientT]):
            @get("/search")
            def search(self, data: Annotated[dict, Body()]) -> dict: ...


def test_form_marker_on_get_raises_at_definition_time() -> None:
    with pytest.raises(TypeError, match=r"BadClient\.search: form .*body-supporting"):

        class BadClient(APIClient[ClientT]):
            @get("/search")
            def search(self, data: Annotated[str, Form()]) -> dict: ...


def test_file_marker_on_get_raises_at_definition_time() -> None:
    with pytest.raises(TypeError, match=r"BadClient\.search: file .*body-supporting"):

        class BadClient(APIClient[ClientT]):
            @get("/search")
            def search(self, data: Annotated[bytes, File()]) -> dict: ...


def test_unsupported_marker_kind_raises_at_definition_time() -> None:
    with pytest.raises(
        TypeError,
        match=r"BadClient\.search: unsupported marker kind\(s\) 'weird' — "
        r"supported kinds are body, cookie, file, form, header, path, query",
    ):

        class BadClient(APIClient[ClientT]):
            @get("/search")
            def search(self, q: Annotated[str, Weird()]) -> dict: ...


def test_path_marker_without_placeholder_raises_at_definition_time() -> None:
    with pytest.raises(
        TypeError,
        match=r"BadClient\.search: path parameter\(s\) 'uid' have no matching "
        r"placeholder in the URL",
    ):

        class BadClient(APIClient[ClientT]):
            @get("/static")
            def search(self, uid: Annotated[int, Path()]) -> dict: ...


# --- issue 5: required non-path params must raise ---------------------------


async def test_missing_required_query_param_raises(
    fix_client: FixClient, respx_mock: respx.MockRouter
) -> None:
    respx_mock.get("/req").mock(return_value=httpx.Response(200, json={}))

    with pytest.raises(TypeError, match="q"):
        await resolve(fix_client.req_query())  # type: ignore[call-arg]


# --- issue 6: unknown/surplus/duplicate arguments must raise ----------------


async def test_unknown_keyword_argument_raises(fix_client: FixClient) -> None:
    with pytest.raises(TypeError, match="typoo"):
        await resolve(fix_client.unknown(uid=1, typoo=2))  # type: ignore[call-arg]


async def test_surplus_positional_argument_raises(fix_client: FixClient) -> None:
    with pytest.raises(TypeError, match="too many positional arguments"):
        await resolve(fix_client.unknown(1, 99))  # type: ignore[call-arg]


async def test_positional_cannot_fill_keyword_only_param(
    fix_client: FixClient,
) -> None:
    with pytest.raises(TypeError, match="too many positional arguments"):
        await resolve(fix_client.kwonly(1, 2))  # type: ignore[misc]


async def test_duplicate_argument_raises(fix_client: FixClient) -> None:
    with pytest.raises(TypeError, match="multiple values"):
        await resolve(fix_client.unknown(1, uid=2))  # type: ignore[misc]


async def test_keyword_only_param_is_sent(
    fix_client: FixClient, respx_mock: respx.MockRouter
) -> None:
    route = respx_mock.get("/kwonly/1").mock(return_value=httpx.Response(200, json={}))

    await resolve(fix_client.kwonly(1, a=5))

    assert route.calls.last.request.url.params["a"] == "5"


async def test_var_kwargs_are_excluded_from_request(
    fix_client: FixClient, respx_mock: respx.MockRouter
) -> None:
    route = respx_mock.get("/var/1").mock(return_value=httpx.Response(200, json={}))

    await resolve(fix_client.with_var_kwargs(uid=1, anything="ignored"))

    assert not route.calls.last.request.url.params


# --- issue 7: path values are serialized and percent-encoded ----------------


async def test_enum_path_value_uses_enum_value(
    fix_client: FixClient, respx_mock: respx.MockRouter
) -> None:
    route = respx_mock.route(method="GET").mock(
        return_value=httpx.Response(200, json={})
    )

    await resolve(fix_client.path_enum(uid=Color.RED))

    assert route.calls.last.request.url.raw_path == b"/p/red"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("../admin?x=1", b"/p/..%2Fadmin%3Fx%3D1"),
        ("a b", b"/p/a%20b"),
        ("x#y", b"/p/x%23y"),
        ("..", b"/p/%2E%2E"),
        (".", b"/p/%2E"),
    ],
)
async def test_path_values_are_percent_encoded(
    fix_client: FixClient,
    respx_mock: respx.MockRouter,
    value: str,
    expected: bytes,
) -> None:
    route = respx_mock.route(method="GET").mock(
        return_value=httpx.Response(200, json={})
    )

    await resolve(fix_client.path_str(uid=value))

    assert route.calls.last.request.url.raw_path == expected


def test_quote_path_value_unwraps_enum() -> None:
    assert _quote_path_value(Color.BLUE) == "blue"
    assert _quote_path_value(7) == "7"
    assert _quote_path_value("../x") == "..%2Fx"


async def test_enum_query_value_serializes_to_its_value(
    fix_client: FixClient, respx_mock: respx.MockRouter
) -> None:
    route = respx_mock.get("/enum-query").mock(
        return_value=httpx.Response(200, json={})
    )

    await resolve(fix_client.enum_query(c=Color.RED))

    assert route.calls.last.request.url.params["c"] == "red"


async def test_enum_list_query_values_serialize_to_their_values(
    fix_client: FixClient, respx_mock: respx.MockRouter
) -> None:
    route = respx_mock.get("/enum-query-list").mock(
        return_value=httpx.Response(200, json={})
    )

    await resolve(fix_client.enum_query_list(c=[Color.RED, Color.BLUE]))

    assert route.calls.last.request.url.params.get_list("c") == ["red", "blue"]


async def test_enum_header_value_serializes_to_its_value(
    fix_client: FixClient, respx_mock: respx.MockRouter
) -> None:
    route = respx_mock.get("/enum-header").mock(
        return_value=httpx.Response(200, json={})
    )

    await resolve(fix_client.enum_header(c=Color.BLUE))

    assert route.calls.last.request.headers["X-Color"] == "blue"


def test_quote_path_value_encodes_dot_segments() -> None:
    assert _quote_path_value(".") == "%2E"
    assert _quote_path_value("..") == "%2E%2E"
    assert _quote_path_value(".hidden") == ".hidden"


# --- issue 8: bodyless POST/PUT/PATCH ---------------------------------------


async def test_post_without_body_param_sends_no_body(
    fix_client: FixClient, respx_mock: respx.MockRouter
) -> None:
    route = respx_mock.post("/trigger").mock(return_value=httpx.Response(204))

    await resolve(fix_client.trigger(job_id=1))

    request = route.calls.last.request
    assert request.content == b""
    assert "content-type" not in request.headers
    assert request.url.params["job_id"] == "1"


# --- issue 12: empty responses and return annotations -----------------------


async def test_empty_body_raises_for_non_optional_return(
    fix_client: FixClient, respx_mock: respx.MockRouter
) -> None:
    respx_mock.get("/item").mock(return_value=httpx.Response(204))

    with pytest.raises(EmptyResponseError) as exc_info:
        await resolve(fix_client.strict_item())

    error = exc_info.value
    assert isinstance(error, HttpxicError)
    assert error.status_code == 204
    assert error.endpoint == "FixClient.strict_item"
    assert "FixClient.strict_item" in str(error)
    assert "204" in str(error)


async def test_empty_body_returns_none_for_optional_return(
    fix_client: FixClient, respx_mock: respx.MockRouter
) -> None:
    respx_mock.get("/maybe").mock(return_value=httpx.Response(204))

    assert await resolve(fix_client.maybe_item()) is None


def test_missing_return_annotation_raises_at_definition_time() -> None:
    with pytest.raises(TypeError, match="missing return annotation"):

        class BadClient(APIClient[ClientT]):
            @get("/x")
            def f(self): ...


# --- issue 14: timeout is delegated to httpx2 -------------------------------


async def test_timeout_is_passed_to_httpx(
    fix_client: FixClient, respx_mock: respx.MockRouter
) -> None:
    route = respx_mock.get("/slow").mock(return_value=httpx.Response(200, json={}))

    await resolve(fix_client.slow())

    assert route.calls.last.request.extensions["timeout"] == {
        "connect": 2.5,
        "pool": 2.5,
        "read": 2.5,
        "write": 2.5,
    }


async def test_timeout_object_is_passed_to_httpx(
    fix_client: FixClient, respx_mock: respx.MockRouter
) -> None:
    route = respx_mock.get("/slow-obj").mock(return_value=httpx.Response(200, json={}))

    await resolve(fix_client.slow_obj())

    assert route.calls.last.request.extensions["timeout"] == {
        "connect": 1.0,
        "pool": 1.0,
        "read": 1.0,
        "write": 1.0,
    }


# --- nits -------------------------------------------------------------------


async def test_raw_response_return_type_bypasses_adapter(
    fix_client: FixClient, respx_mock: respx.MockRouter
) -> None:
    respx_mock.get("/raw").mock(return_value=httpx.Response(200, text="not json"))

    response = await resolve(fix_client.raw())

    assert isinstance(response, httpx2.Response)
    assert response.text == "not json"


def test_resolved_param_alias_is_none_for_body_markers() -> None:
    body_param = _ResolvedParam(Body(), "data")
    assert body_param.alias is None
    assert body_param.field_name == "data"

    header_param = _ResolvedParam(Header(alias="X-Trace"), "x_trace")
    assert header_param.alias == "X-Trace"
    assert header_param.field_name == "X-Trace"

    plain_param = _ResolvedParam(Query(), "q")
    assert plain_param.alias is None
    assert plain_param.field_name == "q"


def test_adapter_cache_is_bounded() -> None:
    assert _adapter.cache_info().maxsize is not None


def test_serializer_options_are_not_shared() -> None:
    first = _serializer_options(None)
    second = _serializer_options(None)
    assert first == second == {"by_alias": True}
    assert first is not second

    overrides = {"exclude_none": True}
    built = _serializer_options(overrides)  # type: ignore[arg-type]
    assert built is not overrides
    assert built == {"exclude_none": True}


async def test_unannotated_param_defaults_to_query(
    fix_client: FixClient, respx_mock: respx.MockRouter
) -> None:
    route = respx_mock.get("/implicit").mock(return_value=httpx.Response(200, json={}))

    await resolve(fix_client.implicit_query())

    assert route.calls.last.request.url.params["q"] == "default"


async def test_annotated_without_marker_metadata_defaults_to_query(
    fix_client: FixClient, respx_mock: respx.MockRouter
) -> None:
    route = respx_mock.get("/doc-meta").mock(return_value=httpx.Response(200, json={}))

    await resolve(fix_client.doc_meta_query())

    assert route.calls.last.request.url.params["q"] == "x"


# the legacy `typing.Union` spelling is deliberate: on Python < 3.14 the `X | Y`
# syntax produces `types.UnionType`, which never reaches the `typing.Union` branch
def test_marker_from_annotation_union_without_marker_returns_none() -> None:
    assert _marker_from_annotation(Union[int, None]) is None  # noqa: UP007


def test_marker_from_annotation_finds_marker_in_later_union_member() -> None:
    marker = Query()

    annotation = Union[int, Annotated[str, marker]]  # noqa: UP007
    assert _marker_from_annotation(annotation) is marker


async def test_cookie_marker_sends_with_alias(
    fix_client: FixClient, respx_mock: respx.MockRouter
) -> None:
    route = respx_mock.get("/cookie").mock(return_value=httpx.Response(200, json={}))

    await resolve(fix_client.with_cookie(sid="abc"))

    assert route.calls.last.request.headers["cookie"] == "session=abc"


async def test_cookie_marker_does_not_warn_about_per_request_cookies(
    fix_client: FixClient, respx_mock: respx.MockRouter
) -> None:
    route = respx_mock.get("/cookie").mock(return_value=httpx.Response(200, json={}))

    with warnings.catch_warnings():
        warnings.simplefilter("error", DeprecationWarning)
        await resolve(fix_client.with_cookie(sid="abc"))

    assert "cookie" in route.calls.last.request.headers


async def test_multiple_cookies_are_joined_and_none_is_skipped(
    fix_client: FixClient, respx_mock: respx.MockRouter
) -> None:
    route = respx_mock.get("/cookies").mock(return_value=httpx.Response(200, json={}))

    await resolve(fix_client.with_cookies(sid="abc", theme="dark", trace="t-1"))

    request = route.calls.last.request
    assert request.headers["cookie"] == "session=abc; theme=dark"
    assert request.headers["X-Trace"] == "t-1"


async def test_explicit_cookie_header_is_preserved_before_cookie_markers(
    fix_client: FixClient, respx_mock: respx.MockRouter
) -> None:
    route = respx_mock.get("/cookie-header").mock(
        return_value=httpx.Response(200, json={})
    )

    await resolve(fix_client.cookie_plus_cookie_header(sid="abc", raw="consent=yes"))

    assert route.calls.last.request.headers["cookie"] == "consent=yes; session=abc"


@pytest.mark.parametrize(
    "value",
    [
        "x; evil=1",
        "a b",
        '"quoted"',
        "back\\slash",
        "comma,separated",
        "ctrl\x00char",
        "\x7f",
    ],
)
async def test_forbidden_cookie_value_raises(fix_client: FixClient, value: str) -> None:
    with pytest.raises(ValueError, match="forbidden"):
        await resolve(fix_client.with_cookie(sid=value))


async def test_forbidden_cookie_name_raises(fix_client: FixClient) -> None:
    with pytest.raises(ValueError, match="forbidden"):
        await resolve(fix_client.with_bad_cookie_name(v="ok"))


async def test_header_alias_overrides_default_header_case_insensitively(
    fix_client: FixClient, respx_mock: respx.MockRouter
) -> None:
    route = respx_mock.post("/override-content-type").mock(
        return_value=httpx.Response(204)
    )

    await resolve(
        fix_client.override_content_type(
            data={"a": 1}, content_type="application/vnd.custom+json"
        )
    )

    headers = route.calls.last.request.headers
    assert headers.get_list("content-type") == ["application/vnd.custom+json"]


async def test_cookie_header_survives_alongside_a_json_body(
    fix_client: FixClient, respx_mock: respx.MockRouter
) -> None:
    route = respx_mock.post("/cookie-body").mock(return_value=httpx.Response(204))

    await resolve(fix_client.cookie_with_json_body(data={"a": 1}, sid="abc"))

    request = route.calls.last.request
    assert request.headers["cookie"] == "session=abc"
    assert request.headers["content-type"] == "application/json"
    assert json.loads(request.content) == {"a": 1}


async def test_untyped_body_param_is_sent_as_json(
    fix_client: FixClient, respx_mock: respx.MockRouter
) -> None:
    route = respx_mock.post("/untyped").mock(return_value=httpx.Response(204))

    await resolve(fix_client.untyped_body({"a": 1}))

    request = route.calls.last.request
    assert request.headers["content-type"] == "application/json"
    assert json.loads(request.content) == {"a": 1}


async def test_none_return_type_discards_non_empty_body(
    fix_client: FixClient, respx_mock: respx.MockRouter
) -> None:
    respx_mock.get("/ignored").mock(return_value=httpx.Response(200, json={"a": 1}))

    assert await resolve(fix_client.ignored_body()) is None


async def test_header_alias_still_applied_alongside_json_body(
    respx_mock: respx.MockRouter, http: httpx2.Client | httpx2.AsyncClient
) -> None:
    class HeaderBodyClient(APIClient[ClientT]):
        @post("/items")
        def create(
            self,
            data: Annotated[dict, Body()],
            trace: Annotated[str | None, Header(alias="X-Trace")] = None,
        ) -> dict: ...

    route = respx_mock.post("/items").mock(return_value=httpx.Response(200, json={}))

    await resolve(HeaderBodyClient(http).create(data={"a": 1}, trace="t-1"))

    request = route.calls.last.request
    assert request.headers["X-Trace"] == "t-1"
    assert request.headers["content-type"] == "application/json"
    assert json.loads(request.content) == {"a": 1}
