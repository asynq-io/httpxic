![Tests](https://github.com/asynq-io/httpxic/workflows/Tests/badge.svg)
![Build](https://github.com/asynq-io/httpxic/workflows/Publish/badge.svg)
![License](https://img.shields.io/github/license/asynq-io/httpxic)
![Python](https://img.shields.io/pypi/pyversions/httpxic)
![Format](https://img.shields.io/pypi/format/httpxic)
![PyPi](https://img.shields.io/pypi/v/httpxic)
![Mypy](https://img.shields.io/badge/mypy-checked-blue)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/charliermarsh/ruff/main/assets/badge/v2.json)](https://github.com/charliermarsh/ruff)
[![security: bandit](https://img.shields.io/badge/security-bandit-yellow.svg)](https://github.com/PyCQA/bandit)

# httpxic

Type-safe api client library built on top of httpx2 and pydantic

Declare an endpoint as an annotated method with an empty body; `httpxic`
builds the request from the signature, sends it with
[httpx2](https://pydantic.dev/docs/httpx2) and validates the response with
[pydantic](https://docs.pydantic.dev/). The same client class works
synchronously with an `httpx2.Client` and asynchronously with an
`httpx2.AsyncClient`.

## Requirements

Python **3.10+**. `httpx2` and `pydantic` v2 are installed as dependencies.

## Installation

```shell
uv add httpxic
```

## Usage

```python
from typing import Annotated

import httpx2
from pydantic import BaseModel

from httpxic import APIClient, ClientT, Query, delete, get, patch, post, put

# valid annotations are:
# Query, Path (optional), Header, Cookie, Body, Form, File
# decorators: get, post, put, patch, delete, head, options, sse


class User(BaseModel):
    id: int
    email: str


class CreateUser(BaseModel):
    email: str


class UpdateUser(BaseModel):
    email: str | None = None


class UserClient(APIClient[ClientT]):
    @get("/users/{user_id}")
    def retrieve_user(self, user_id: int) -> User: ...

    @get("/users")
    def list_users(
        self,
        offset: Annotated[int, Query()] = 0,
        limit: Annotated[int, Query()] = 100,
    ) -> list[User]: ...

    @post("/users")
    def create_user(self, data: CreateUser) -> User: ...

    @put("/users/{user_id}")
    def replace_user(self, data: CreateUser, user_id: int) -> User: ...

    @patch("/users/{user_id}")
    def update_user(self, data: UpdateUser, user_id: int) -> User: ...

    @delete("/users/{user_id}")
    def delete_user(self, user_id: int) -> None: ...


def main() -> None:
    with httpx2.Client(base_url="https://example.com") as http:
        client = UserClient(http)
        user = client.retrieve_user(1)  # User
        print(user)


async def amain() -> None:
    async with httpx2.AsyncClient(base_url="https://example.com") as http:
        client = UserClient(http)
        users = await client.list_users()  # list[User]
        print(users)
```

The `httpx2` client you pass in decides the mode: with an `httpx2.Client` every
endpoint returns its result directly, with an `httpx2.AsyncClient` it returns a
coroutine to `await`. Declare endpoints with a plain `def` either way; an
`async def` endpoint raises `TypeError` at class-definition time. Declare
subclasses as `APIClient[ClientT]` so type checkers infer the right return type
for each mode.

`APIClient` never opens or closes the `httpx2` client - you own its lifecycle.

Every endpoint method **must** have a return annotation; use `-> None` for
endpoints whose response body you discard. A missing annotation raises
`TypeError` at class-definition time.

Tip: mypy reports the `...` bodies of endpoint methods with its `empty-body`
error code; disable it for your client modules
(`disable_error_code = ["empty-body"]`).

## Parameters

Each function parameter maps to exactly one part of the request. Annotate it
with `Annotated[<type>, <marker>()]`, or rely on the defaults below.

| Marker     | Sent as                                             | Supports `alias=` |
| ---------- | --------------------------------------------------- | ----------------- |
| `Query()`  | URL query string                                     | yes               |
| `Header()` | request header                                       | yes               |
| `Cookie()` | request cookie                                       | yes               |
| `Path()`   | `{placeholder}` in the path, percent-encoded         | no                |
| `Body()`   | request body, JSON unless `Content-Type` says otherwise | no             |
| `Form()`   | one field of an `x-www-form-urlencoded` body         | no                |
| `File()`   | one part of a `multipart/form-data` body             | no                |

Only `Query()`, `Header()` and `Cookie()` accept `alias=`; the other markers
take no arguments and use the parameter name as the field name.

Without a marker the parameter is resolved implicitly:

* it becomes a **path** parameter when its name matches a `{placeholder}`;
* otherwise, on `POST`/`PUT`/`PATCH`, the first remaining parameter becomes the
  JSON **body** (a second unannotated parameter is ambiguous and raises
  `TypeError`);
* otherwise it becomes a **query** parameter.

`Path()` values are percent-encoded, so a `user_id` of `a/b` requests
`/users/a%2Fb` rather than escaping the path. Enum values are serialized to
their `.value` before encoding.

Several declaration mistakes are rejected at decoration time, i.e. as soon as
the class body is executed, with a `TypeError`:

* a `Body()`, `Form()` or `File()` parameter on a method that cannot carry a
  body (`GET`, `DELETE`, `HEAD`, `OPTIONS`);
* more than one `Body()` parameter, or `Body()` mixed with `Form()`/`File()`;
* a `{placeholder}` in the path with no matching parameter, or a `Path()`
  parameter with no matching `{placeholder}`;
* a missing return annotation, or an `async def` endpoint.

Calls are strict too: unknown keyword arguments, surplus positional arguments
and missing required parameters all raise `TypeError` instead of being ignored.

### Defaults are always transmitted

A parameter default is *sent*, it is not treated as "unset". With the client
above:

```python
async def show_defaults(client) -> None:
    await client.list_users()  # GET /users?offset=0&limit=100
    await client.list_users(offset=20)  # GET /users?offset=20&limit=100
```

The only way to omit a query parameter, header or cookie is to pass `None` for
it — `None` values are dropped from the request:

```python
from typing import Annotated

import httpx2

from httpxic import APIClient, ClientT, Query, get


class SearchClient(APIClient[ClientT]):
    @get("/users")
    def search(
        self,
        q: Annotated[str | None, Query()] = None,
    ) -> list[dict]: ...


def show_none(client: SearchClient[httpx2.Client]) -> None:
    client.search()  # GET /users
    client.search(q="ada")  # GET /users?q=ada
```

### Filters

A pydantic model passed as a `Query()` parameter is expanded into one query
parameter per field, by alias, with `None` fields dropped and lists/sets sent as
repeated parameters. On `GET`/`DELETE`/`HEAD`/`OPTIONS` an unannotated model
parameter is a query parameter already; on `POST`/`PUT`/`PATCH` it stays the
JSON body. `httpxic.filters` mirrors the query filters of
[fastapi-views](https://github.com/asynq-io/fastapi-views) (`Filter`,
`PaginationFilter`, `OffsetLimitFilter`, `CursorPaginationFilter`,
`OrderingFilter`, `SearchFilter`, `FieldsFilter`, `IncludeFilter`); all their
fields default to `None`, so the server defaults apply until you set them:

```python
from typing import Annotated

import httpx2

from httpxic import APIClient, ClientT, Query, get
from httpxic.filters import Filter


class UserFilter(Filter):
    id__in: list[int] | None = None
    name: str | None = None


class UserClient(APIClient[ClientT]):
    @get("/users")
    def list_users(
        self, filters: Annotated[UserFilter | None, Query()] = None
    ) -> list[dict]: ...


def show_filters(client: UserClient[httpx2.Client]) -> None:
    # GET /users?q=ada&sort=-id&page=2&id__in=1&id__in=2
    client.list_users(UserFilter(page=2, sort=["-id"], q="ada", id__in=[1, 2]))
```

Parameters declared next to the model win over its fields on name clashes.
`httpxic.filters.to_params(model)` returns the same mapping, e.g. for
`EndpointOptions(params=...)`.

### Forms and file uploads

Multiple `Form()` parameters accumulate into a single form-encoded body, and
multiple `File()` parameters into a single multipart body. `Form()` and `File()`
can be combined in one request: the form fields are then sent as regular parts
of the same `multipart/form-data` request. The field name on the wire is always
the parameter name.

```python
from typing import Annotated

from httpxic import APIClient, ClientT, File, Form, post


class UploadClient(APIClient[ClientT]):
    @post("/login")
    def login(
        self,
        username: Annotated[str, Form()],
        password: Annotated[str, Form()],
    ) -> dict: ...

    @post("/documents")
    def upload(
        self,
        title: Annotated[str, Form()],
        document: Annotated[bytes, File()],
        thumbnail: Annotated[bytes, File()],
    ) -> dict: ...
```

`login()` sends `username=...&password=...` as
`application/x-www-form-urlencoded`; `upload()` sends one `multipart/form-data`
request containing the `title` field plus the `document` and `thumbnail` parts.

### Body content type

The `Content-Type` header decides how the `Body()` parameter is encoded
(default `application/json`). JSON media types (`application/json` or any
`+json` suffix, e.g. `application/merge-patch+json`) are serialized as below;
any other media type sends the value untouched (`bytes`, `str` or an iterator
of bytes), and a `None` value then sends no body. Set it with the decorator's
`headers=` (see [Endpoint options](#endpoint-options)) or per call with a
`Header(alias="Content-Type")` parameter:

```python
from typing import Annotated

from httpxic import APIClient, Body, ClientT, patch, put


class BlobClient(APIClient[ClientT]):
    @put("/blobs/{name}", headers={"Content-Type": "application/octet-stream"})
    def upload(self, name: str, data: Annotated[bytes, Body()]) -> None: ...

    @patch(
        "/blobs/{name}/meta",
        headers={"Content-Type": "application/merge-patch+json"},
    )
    def update_meta(self, name: str, changes: Annotated[dict, Body()]) -> None: ...
```

### JSON body serialization

The body is serialized with pydantic using `EncodeOptions(by_alias=True)` by
default. That default does **not** include `exclude_unset`, so fields you never
touched are still emitted:

```python
async def show_full_body(client) -> None:
    # PATCH /users/1 with body {"email": null} - not a partial patch!
    await client.update_user(UpdateUser(), 1)
```

For partial-patch semantics, pass `serializer_options` to the decorator. It
accepts the same keys as `pydantic`'s serializer (`exclude_unset`,
`exclude_none`, `exclude_defaults`, `include`, `exclude`, `by_alias`,
`round_trip`, `warnings`, `serialize_as_any`, `context`) and **replaces** the
default, so re-state `by_alias=True` if you rely on aliases:

```python
import httpx2
from pydantic import BaseModel

from httpxic import APIClient, ClientT, patch


class UpdateUser(BaseModel):
    email: str | None = None


class PatchClient(APIClient[ClientT]):
    @patch("/users/{user_id}", serializer_options={"exclude_unset": True})
    def update_user(self, data: UpdateUser, user_id: int) -> dict: ...


async def show_partial_body(client: PatchClient[httpx2.AsyncClient]) -> None:
    await client.update_user(UpdateUser(), 1)  # body: {}
    await client.update_user(UpdateUser(email="a@b.c"), 1)  # {"email": "a@b.c"}
```

`serializer_options` is available on `post`, `put` and `patch`.

A `POST`/`PUT`/`PATCH` endpoint that declares no body parameter at all sends no
body.

## Responses

The return annotation drives decoding:

* `-> Model` / `-> list[Model]` / any other type — the response body is
  validated with a pydantic `TypeAdapter`;
* `-> None` — the body is ignored;
* `-> httpx2.Response` — the raw response is returned untouched, no decoding.

An empty response body for an endpoint whose return type does not admit `None`
raises `EmptyResponseError`. Annotate such endpoints `-> None` (or
`-> Model | None`) when an empty body is expected:

```python
import httpx2
from pydantic import BaseModel

from httpxic import APIClient, ClientT, delete, get


class User(BaseModel):
    id: int


class Client(APIClient[ClientT]):
    @delete("/users/{user_id}")
    def delete_user(self, user_id: int) -> None: ...

    @get("/users/{user_id}")
    def find_user(self, user_id: int) -> User | None: ...

    @get("/raw/{user_id}")
    def raw_user(self, user_id: int) -> httpx2.Response: ...
```

Validation itself can be tuned per client with `decode_options`, which is
forwarded to pydantic's `validate_json` (`strict`, `extra`, `context`,
`by_alias`, `by_name`):

```python
import httpx2

from httpxic import APIClient


def make_strict_client(http: httpx2.Client) -> APIClient[httpx2.Client]:
    return APIClient(http, decode_options={"strict": True})
```

## Server-sent events

`@sse` declares a `text/event-stream` endpoint. Declare it as a plain `def`
returning `Iterator[<event model>]` for use with an `httpx2.Client`, or
`AsyncIterator[<event model>]` for use with an `httpx2.AsyncClient`, and consume
it with `for` or `async for` respectively. Each event's `data` field is
validated into that model:

```python
from collections.abc import AsyncIterator, Iterator

import httpx2
from pydantic import BaseModel

from httpxic import APIClient, ClientT, ServerSentEvent, sse


class Token(BaseModel):
    index: int
    text: str


class Prompt(BaseModel):
    question: str


class StreamClient(APIClient[ClientT]):
    @sse("/tokens")
    def stream_tokens(self) -> Iterator[Token]: ...

    # SSE over POST, with a request body like any other endpoint
    @sse("/chat", method="POST")
    def chat(self, data: Prompt) -> AsyncIterator[Token]: ...

    # the decoded event itself, for streams that use event types or ids
    @sse("/notifications")
    def notifications(self) -> AsyncIterator[ServerSentEvent]: ...


def main(client: StreamClient[httpx2.Client]) -> None:
    for token in client.stream_tokens():
        print(token.text)


async def amain(client: StreamClient[httpx2.AsyncClient]) -> None:
    async for event in client.notifications():
        print(event.event, event.id, event.data)
```

The annotation only names the event model: at runtime an `@sse` endpoint yields
synchronously on an `httpx2.Client` and asynchronously on an
`httpx2.AsyncClient`, whichever of the two iterator types it is annotated with.
The type checker, however, reports the annotated one, so annotate it for the
client type you use.

`method=` accepts `"GET"` (the default) or `"POST"`; parameters, bodies and
`serializer_options` work exactly as they do on the other decorators. Requests
are sent with `Accept: text/event-stream` and `Cache-Control: no-store`, both
overridable with an explicit `Header(alias=...)` parameter.

Annotate the event type as `ServerSentEvent` to receive events undecoded:

| Attribute | Meaning                                                       |
| --------- | ------------------------------------------------------------- |
| `data`    | the event payload, multiple `data:` lines joined by `\n`       |
| `event`   | the event type, `"message"` unless the stream sets one         |
| `id`      | last event id seen on the stream, `""` if never set            |
| `retry`   | reconnection hint in milliseconds, `None` if this event set none |

Events are decoded by httpx2's `EventSource`: comments (`: ...`) are ignored,
a block with any field (even one without `data`) dispatches an event, and a
block the stream ends in the middle of is discarded. A response whose
`Content-Type` is not `text/event-stream`, or an event larger than 1 MiB, raises
`httpx2.SSEError`.

Arguments are checked when you call the method, but nothing is sent until you
start iterating - that is also when an error status raises
`httpx2.HTTPStatusError`. The response body is read before raising, so error
details survive on the exception. A `pydantic.ValidationError` for a malformed
event propagates out of the loop, ending the stream.

Note that events are validated as they arrive, so the client-wide read timeout
applies to the gap *between* events. Streams that may idle longer than it need
an override:

```python
from collections.abc import AsyncIterator

import httpx2

from httpxic import APIClient, ClientT, ServerSentEvent, sse


class SlowStreamClient(APIClient[ClientT]):
    @sse("/notifications", timeout=httpx2.Timeout(5.0, read=None))
    def notifications(self) -> AsyncIterator[ServerSentEvent]: ...
```

`Iterator`/`AsyncIterator` has to be imported at runtime rather than under
`if TYPE_CHECKING:`, since `httpxic` resolves the annotation to find the event
model.

## Client options

`APIClient` takes the `httpx2.Client` or `httpx2.AsyncClient` to send requests
with, configured however you like (`base_url`, `auth`, `headers`, `timeout`,
`transport`, ...), plus these keyword options:

| Option             | Default | Meaning                                                                 |
| ------------------ | ------- | ----------------------------------------------------------------------- |
| `raise_for_status` | `True`  | call `response.raise_for_status()` on every response                      |
| `decode_options`   | `{}`    | options forwarded to pydantic when validating response bodies             |

Set `raise_for_status=False` to inspect error responses yourself instead of
getting an `httpx2.HTTPStatusError`:

```python
import httpx2

from httpxic import APIClient


def make_lenient_client(http: httpx2.Client) -> APIClient[httpx2.Client]:
    return APIClient(http, raise_for_status=False)
```

### Endpoint options

Every decorator accepts request options as keyword arguments
(`httpxic.EndpointOptions`): `headers`, `params`, `timeout`, `auth`,
`follow_redirects` and `extensions`. They apply to every call of that endpoint
and are forwarded to `httpx2`; unknown names raise `TypeError` at
class-definition time. `headers` and `params` are merged under the endpoint's
`Header()` / `Query()` parameters, which win on conflicts.

`headers` and `params` may also be zero-argument callables, evaluated on every
call, e.g. to read a `ContextVar`:

```python
from collections.abc import Mapping
from contextvars import ContextVar

from httpxic import APIClient, ClientT, get

request_id: ContextVar[str] = ContextVar("request_id", default="-")


def tracing() -> Mapping[str, str]:
    return {"X-Request-Id": request_id.get()}


class ReportClient(APIClient[ClientT]):
    @get("/reports/{report_id}", timeout=30.0, headers=tracing)
    def generate_report(self, report_id: int) -> dict: ...

    @get("/legacy", follow_redirects=True, params={"format": "json"})
    def legacy(self) -> dict: ...
```

A `timeout` that is exceeded raises `httpx2.TimeoutException`. Client-wide
defaults (`base_url`, `auth`, `headers`, `timeout`, ...) belong on the
`httpx2.Client` itself.

## Exceptions

| Exception                  | Raised when                                                                  |
| -------------------------- | ---------------------------------------------------------------------------- |
| `TypeError`                | invalid endpoint declaration, or bad arguments at call time                     |
| `httpxic.HttpxicError`     | base class for every error raised by `httpxic` itself                           |
| `httpxic.EmptyResponseError` | the response body is empty but the return type does not admit `None`          |
| `httpx2.HTTPStatusError`    | error status code, unless `raise_for_status=False`                              |
| `httpx2.TimeoutException`   | a client-wide or per-endpoint `timeout` is exceeded                             |
| `pydantic.ValidationError` | the response body does not match the declared return type                       |

`EmptyResponseError` subclasses `HttpxicError`, so catching `HttpxicError`
catches every httpxic-specific runtime error. Declaration and argument mistakes
are deliberately plain `TypeError`s, since they are programming errors rather
than something to handle at runtime.

## Escape hatch

Anything the decorators do not cover can be written by hand with `request()`
and `stream()`. Both honour `raise_for_status` and follow the client mode:

| Method                        | `httpx2.Client`                      | `httpx2.AsyncClient`                          |
| ----------------------------- | ----------------------------------- | -------------------------------------------- |
| `request(method, url, **kw)`  | returns `httpx2.Response`            | returns a coroutine of `httpx2.Response`      |
| `stream(method, url, **kw)`   | context manager of `httpx2.Response` | async context manager of `httpx2.Response`    |

`stream()` yields a response whose body has not been read. Wrapped in
`httpx2.EventSource`, it decodes an event stream that `@sse` cannot describe:

```python
import httpx2
from pydantic import BaseModel

from httpxic import APIClient


class User(BaseModel):
    id: int


class ManualClient(APIClient[httpx2.Client]):
    def current_user(self, auth_token: str) -> User:
        response = self.request(
            "GET", "/users/me", headers={"Authorization": f"Bearer {auth_token}"}
        )
        return User.model_validate_json(response.content)

    def tail_log(self) -> None:
        with self.stream("GET", "/logs") as response:
            for event in httpx2.EventSource(response):
                print(event.data)


class AsyncManualClient(APIClient[httpx2.AsyncClient]):
    async def tail_log(self) -> None:
        async with self.stream("GET", "/logs") as response:
            async for event in httpx2.EventSource(response):
                print(event.data)
```

## Generating a client from OpenAPI

The `codegen` extra adds an `httpxic generate` command that turns an OpenAPI 3.x
document (a file path or an http(s) URL, JSON or YAML) into a package with
pydantic models (via [datamodel-code-generator](https://github.com/koxudaxi/datamodel-code-generator))
and one client class written in the style above. It is configured with a JSON
or YAML file:

```yaml
# examples/config.yaml
spec: https://petstore3.swagger.io/api/v3/openapi.json  # path or http(s) URL, required
output: petstore                                         # package directory, required
class_name: PetstoreClient                               # optional
filters: true                                            # optional, default true
model_options:                                           # optional
  snake_case_field: true
```

```shell
uv add 'httpxic[codegen]'
uv run httpxic generate --config examples/config.yaml
```

Relative `spec` and `output` paths are resolved against the config file's
directory; unknown keys are rejected. Besides `class_name`, `filters` and
`model_options`, the config accepts `base_model` (see below).

The run above produces [`examples/petstore/`](examples/petstore/); an excerpt
of its `client.py`:

```python
class PetstoreClient(APIClient[ClientT]):
    @post("/pet")
    def add_pet(self, *, body: Annotated[Pet, Body()]) -> Pet: ...

    @get("/pet/findByStatus")
    def find_pets_by_status(
        self, *, status: Annotated[FindPetsByStatusStatus, Query()]
    ) -> list[Pet]: ...

    @get("/pet/{pet_id}")
    def get_pet_by_id(self, pet_id: int) -> Pet: ...

    @delete("/pet/{pet_id}")
    def delete_pet(
        self, pet_id: int, *, api_key: Annotated[str | None, Header()] = None
    ) -> None: ...

    @post(
        "/pet/{pet_id}/uploadImage",
        headers={"Content-Type": "application/octet-stream"},
    )
    def upload_file(
        self,
        pet_id: int,
        *,
        body: Annotated[bytes, Body()],
        additional_metadata: Annotated[
            str | None, Query(alias="additionalMetadata")
        ] = None,
    ) -> ApiResponse: ...
```

Use it like any hand-written client, sync or async:

```python
import httpx2

from petstore import PetstoreClient
from petstore.models import FindPetsByStatusStatus, Pet

with httpx2.Client(base_url="https://petstore3.swagger.io/api/v3") as http:
    client = PetstoreClient(http)
    pet = client.add_pet(body=Pet(name="Rex", photo_urls=[]))
    available = client.find_pets_by_status(status=FindPetsByStatusStatus.available)


async def amain() -> None:
    async with httpx2.AsyncClient(
        base_url="https://petstore3.swagger.io/api/v3"
    ) as http:
        pet = await PetstoreClient(http).get_pet_by_id(10)
```

With `snake_case_field: true` model fields are snake_cased and keep the
original name as their alias (`photo_urls`, sent as `photoUrls`). The
generated `BaseModel` sets `populate_by_name=True`, so both names are accepted
at runtime; enable the [pydantic mypy plugin](https://docs.pydantic.dev/latest/integrations/mypy/)
(`plugins = ["pydantic.mypy"]`) so mypy accepts field names and sees the
`Field(None, ...)` defaults.

The output (`__init__.py`, `client.py`, `models.py`) is regenerated from
scratch on every run, so don't edit it; subclass the client instead. Method
names come from `operationId` (snake_cased, else `{method}_{path}`), and inline
schemas become named models (`{Operation}Request`, `{Operation}Response`,
`{Operation}{Param}`). The class name defaults to the spec's `info.title` plus
`Client`.

* Path parameters are positional; everything else is keyword-only, and optional
  parameters default to `None`, which is left out of the request.
* JSON request bodies become a `body` parameter; form and multipart bodies
  become one `Form()` / `File()` parameter per property; any other media type
  (e.g. `application/octet-stream`) becomes a raw `body: bytes`, with the media
  type set via the decorator's `headers={"Content-Type": ...}`. Request bodies
  are always required.
* The first `2xx` response picks the return type: its JSON schema, `None` when
  it has no content, `httpx2.Response` for other media types. `text/event-stream`
  responses become `@sse` endpoints.
* Error responses are not modelled; they raise `httpx2.HTTPStatusError`.
  Security schemes are not generated either; configure authentication on the
  client, e.g. `httpx2.Client(headers={"Authorization": "Bearer ..."})`.
* Model generation can be tuned with any
  [datamodel-code-generator option](https://datamodel-code-generator.koxudaxi.dev/)
  (as `generate()` keyword names), given under `model_options`. They override
  httpxic's defaults (pydantic v2, Python 3.10, `x: int | None = Field(None, ...)`
  fields, one shared `BaseModel` with `populate_by_name=True`). `input_file_type`
  and `output` are set by httpxic:

  ```yaml
  model_options:
    target_python_version: "3.12"
    use_standard_primitive_types: true
  ```

  From Python: `httpxic.codegen.generate("openapi.yaml", Path("client"), model_options={...})`.
* `base_model` (`package.module:Class`) makes every schema inherit from your
  own class instead of the generated `BaseModel`, which then owns the model
  config, e.g.
  `class BaseSchema(BaseModel): model_config = {"use_enum_values": True, "populate_by_name": True}`.
  Options that rename models (e.g. `class_name_prefix`) make generation fail,
  since the client could no longer find them.
* With `filters: true` (the default), query parameters shaped like
  [fastapi-views](https://github.com/asynq-io/fastapi-views) filters become one
  `{Operation}Filter` class per operation, passed as a single
  `filters: Annotated[ListUsersFilter | None, Query()] = None` parameter. An
  operation qualifies when its optional query parameters match a mixin
  (`page`+`page_size`, `cursor`+`page_size`, `offset`+`limit`, `sort`, `q`,
  `fields`, `include`) or any query parameter name contains a `__` lookup; the
  class inherits the matched mixins (all of them collapse into `Filter`, none
  into `BaseFilter`) and declares the remaining query parameters as fields.
  Other operations keep raw parameters. Set `filters: false` to always generate
  raw parameters:

  ```python
  class ListUsersFilter(Filter):
      id__in: list[int] | None = None
      status: Status | None = None
  ```

* Operations that httpxic cannot express (e.g. request bodies on `GET`,
  `deepObject` or object-valued query parameters, form fields that are not
  valid Python names, external `$ref`s) are skipped with a warning and a
  `# skipped` comment in `client.py`.
