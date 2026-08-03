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

Type-safe api client library built on top of httpx and pydantic

Declare an endpoint as an annotated `async def` with an empty body; `httpxic`
builds the request from the signature, sends it with
[httpx](https://www.python-httpx.org/) and validates the response with
[pydantic](https://docs.pydantic.dev/).

## Requirements

Python **3.10+**. `httpx` and `pydantic` v2 are installed as dependencies.

## Installation

```shell
uv add httpxic
```

To also instrument the underlying `httpx` client with OpenTelemetry, install the
`opentelemetry` extra:

```shell
uv add "httpxic[opentelemetry]"
```

The extra pulls in `opentelemetry-instrumentation-httpx`. It is optional: when it
is not installed, instrumentation is silently skipped, so the default is safe
either way.

## Usage

```python
from typing import Annotated

import httpx
from pydantic import BaseModel

from httpxic import APIClient, Query, delete, get, patch, post, put

# valid annotations are:
# Query, Path (optional), Header, Cookie, Body, Form, File


class User(BaseModel):
    id: int
    email: str


class CreateUser(BaseModel):
    email: str


class UpdateUser(BaseModel):
    email: str | None = None


class UserClient(APIClient):
    @get("/users/{user_id}")
    async def retrieve_user(self, user_id: int) -> User: ...

    @get("/users")
    async def list_users(
        self,
        offset: Annotated[int, Query()] = 0,
        limit: Annotated[int, Query()] = 100,
    ) -> list[User]: ...

    @post("/users")
    async def create_user(self, data: CreateUser) -> User: ...

    @put("/users/{user_id}")
    async def replace_user(self, data: CreateUser, user_id: int) -> User: ...

    @patch("/users/{user_id}")
    async def update_user(self, data: UpdateUser, user_id: int) -> User: ...

    @delete("/users/{user_id}")
    async def delete_user(self, user_id: int) -> None: ...

    # custom method
    async def get_current_user(self, auth_token: str) -> User:
        response = await self.get(
            "/users/me",
            headers={"Authorization": f"Bearer {auth_token}"},
        )
        return User.model_validate_json(response.content)


async def main() -> None:
    # httpxic creates the client from the given options - and closes it again
    async with UserClient(base_url="https://example.com") as client:
        users = await client.list_users()
        print(users)

    # or bring your own httpx.AsyncClient - then you own its lifecycle
    async with httpx.AsyncClient(base_url="https://example.com") as http:
        own_client = UserClient(http_client=http)
        user = await own_client.retrieve_user(1)
        print(user)
        await own_client.aclose()  # leaves `http` open
```

Every endpoint method **must** have a return annotation; use `-> None` for
endpoints whose response body you discard. A missing annotation raises
`TypeError` at class-definition time.

## Parameters

Each function parameter maps to exactly one part of the request. Annotate it
with `Annotated[<type>, <marker>()]`, or rely on the defaults below.

| Marker     | Sent as                                             | Supports `alias=` |
| ---------- | --------------------------------------------------- | ----------------- |
| `Query()`  | URL query string                                     | yes               |
| `Header()` | request header                                       | yes               |
| `Cookie()` | request cookie                                       | yes               |
| `Path()`   | `{placeholder}` in the path, percent-encoded         | no                |
| `Body()`   | JSON request body                                    | no                |
| `Form()`   | one field of an `x-www-form-urlencoded` body         | no                |
| `File()`   | one part of a `multipart/form-data` body             | no                |

Only `Query()`, `Header()` and `Cookie()` accept `alias=`; the other markers take
no arguments and use the parameter name as the field name.

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
* a `{placeholder}` in the path with no matching parameter;
* a missing return annotation.

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

from httpxic import APIClient, Query, get


class SearchClient(APIClient):
    @get("/users")
    async def search(
        self,
        q: Annotated[str | None, Query()] = None,
    ) -> list[dict]: ...


async def show_none(client: SearchClient) -> None:
    await client.search()  # GET /users
    await client.search(q="ada")  # GET /users?q=ada
```

### Forms and file uploads

Multiple `Form()` parameters accumulate into a single form-encoded body, and
multiple `File()` parameters into a single multipart body. `Form()` and `File()`
can be combined in one request: the form fields are then sent as regular parts
of the same `multipart/form-data` request. The field name on the wire is always
the parameter name.

```python
from typing import Annotated

from httpxic import APIClient, File, Form, post


class UploadClient(APIClient):
    @post("/login")
    async def login(
        self,
        username: Annotated[str, Form()],
        password: Annotated[str, Form()],
    ) -> dict: ...

    @post("/documents")
    async def upload(
        self,
        title: Annotated[str, Form()],
        document: Annotated[bytes, File()],
        thumbnail: Annotated[bytes, File()],
    ) -> dict: ...
```

`login()` sends `username=...&password=...` as
`application/x-www-form-urlencoded`; `upload()` sends one `multipart/form-data`
request containing the `title` field plus the `document` and `thumbnail` parts.

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
from pydantic import BaseModel

from httpxic import APIClient, patch


class UpdateUser(BaseModel):
    email: str | None = None


class PatchClient(APIClient):
    @patch("/users/{user_id}", serializer_options={"exclude_unset": True})
    async def update_user(self, data: UpdateUser, user_id: int) -> dict: ...


async def show_partial_body(client: PatchClient) -> None:
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
* `-> httpx.Response` — the raw response is returned untouched, no decoding.

An empty response body for an endpoint whose return type does not admit `None`
raises `EmptyResponseError`. Annotate such endpoints `-> None` (or
`-> Model | None`) when an empty body is expected:

```python
import httpx
from pydantic import BaseModel

from httpxic import APIClient, delete, get


class User(BaseModel):
    id: int


class Client(APIClient):
    @delete("/users/{user_id}")
    async def delete_user(self, user_id: int) -> None: ...

    @get("/users/{user_id}")
    async def find_user(self, user_id: int) -> User | None: ...

    @get("/raw/{user_id}")
    async def raw_user(self, user_id: int) -> httpx.Response: ...
```

Validation itself can be tuned per client with `decode_options`, which is
forwarded to pydantic's `validate_json` (`strict`, `extra`, `context`,
`by_alias`, `by_name`):

```python
from httpxic import APIClient


def make_strict_client() -> APIClient:
    return APIClient(
        base_url="https://example.com",
        decode_options={"strict": True},
    )
```

## Server-sent events

`@sse` declares a `text/event-stream` endpoint. The decorated method becomes an
async generator, so declare it as a plain `def` returning
`AsyncIterator[<event model>]` and consume it with `async for`. Each event's
`data` field is validated into that model:

```python
from collections.abc import AsyncIterator

from pydantic import BaseModel

from httpxic import APIClient, ServerSentEvent, sse


class Token(BaseModel):
    index: int
    text: str


class Prompt(BaseModel):
    question: str


class StreamClient(APIClient):
    @sse("/tokens")
    def stream_tokens(self) -> AsyncIterator[Token]: ...

    # SSE over POST, with a request body like any other endpoint
    @sse("/chat", method="POST")
    def chat(self, data: Prompt) -> AsyncIterator[Token]: ...

    # the decoded event itself, for streams that use event types or ids
    @sse("/notifications")
    def notifications(self) -> AsyncIterator[ServerSentEvent]: ...


async def main(client: StreamClient) -> None:
    async for token in client.stream_tokens():
        print(token.text)

    async for event in client.notifications():
        print(event.event, event.id, event.data)
```

`method=` accepts `"GET"` (the default) or `"POST"`; parameters, bodies and
`serializer_options` work exactly as they do on the other decorators. Requests
are sent with `Accept: text/event-stream` and `Cache-Control: no-store`, both
overridable with an explicit `Header(alias=...)` parameter.

Annotate the return type with `ServerSentEvent` to receive events undecoded:

| Attribute | Meaning                                                       |
| --------- | ------------------------------------------------------------- |
| `data`    | the event payload, multiple `data:` lines joined by `\n`       |
| `event`   | the event type, `"message"` unless the stream sets one         |
| `id`      | last event id seen on the stream, `None` if never set          |
| `retry`   | reconnection hint in milliseconds, `None` if this event set none |

Parsing follows the SSE specification: comments (`: ...`) are ignored, event
blocks without a `data` field dispatch nothing, and a block the stream ends in
the middle of is discarded.

Because the method is a generator, nothing is sent until you start iterating -
that is also when an error status raises `httpx.HTTPStatusError`. The response
body is read before raising, so error details survive on the exception. A
`pydantic.ValidationError` for a malformed event propagates out of the
`async for`, ending the stream.

Note that events are validated as they arrive, so the client-wide read timeout
applies to the gap *between* events. Streams that may idle longer than it need
an override:

```python
from collections.abc import AsyncIterator

import httpx

from httpxic import APIClient, ServerSentEvent, sse


class SlowStreamClient(APIClient):
    @sse("/notifications", timeout=httpx.Timeout(5.0, read=None))
    def notifications(self) -> AsyncIterator[ServerSentEvent]: ...
```

`AsyncIterator` has to be imported at runtime rather than under
`if TYPE_CHECKING:`, since `httpxic` resolves the annotation to find the event
model.

## Client options

`APIClient` takes either an existing `http_client` **or** keyword
`client_options` forwarded to `httpx.AsyncClient` (`base_url`, `auth`,
`headers`, `timeout`, `follow_redirects`, `verify`, `transport`, ...). Passing
both raises `ValueError`.

| Option             | Default | Meaning                                                                 |
| ------------------ | ------- | ----------------------------------------------------------------------- |
| `raise_for_status` | `True`  | call `response.raise_for_status()` on every response                      |
| `instrument`       | `None`  | apply OpenTelemetry `httpx` instrumentation, if the extra is installed    |
| `decode_options`   | `{}`    | options forwarded to pydantic when validating response bodies             |

Set `raise_for_status=False` to inspect error responses yourself instead of
getting an `httpx.HTTPStatusError`:

```python
from httpxic import APIClient


def make_lenient_client() -> APIClient:
    return APIClient(base_url="https://example.com", raise_for_status=False)
```

The default `instrument=None` instruments only a client `httpxic` created
itself and never touches a caller-supplied `http_client`; pass an explicit
`True`/`False` to force either behavior. A client is instrumented only if it is
not already instrumented, so sharing one client between several `APIClient`
instances with `instrument=True` instruments it just once.

### Client ownership

Ownership of the underlying `httpx.AsyncClient` follows who created it. When
`httpxic` builds the client from `client_options`, the `APIClient` owns it: it
is closed by `aclose()` and on `async with` exit, and such an instance is
single-use — re-entering it after it has been closed raises `RuntimeError`.
When you pass your own client in as `http_client`, you keep its lifecycle:
`httpxic` neither opens nor closes it, so you can share one `httpx.AsyncClient`
across several `APIClient`s and use `async with` on each as a no-op wrapper.

### Timeouts

Pass `timeout=` to any endpoint decorator to override the client timeout for
that endpoint only. The value is forwarded to `httpx`, so exceeding it raises
`httpx.TimeoutException`:

```python
from httpxic import APIClient, get


class SlowClient(APIClient):
    @get("/reports/{report_id}", timeout=30.0)
    async def generate_report(self, report_id: int) -> dict: ...
```

## Exceptions

| Exception                  | Raised when                                                                  |
| -------------------------- | ---------------------------------------------------------------------------- |
| `TypeError`                | invalid endpoint declaration, or bad arguments at call time                     |
| `httpxic.HttpxicError`     | base class for every error raised by `httpxic` itself                           |
| `httpxic.EmptyResponseError` | the response body is empty but the return type does not admit `None`          |
| `httpx.HTTPStatusError`    | error status code, unless `raise_for_status=False`                              |
| `httpx.TimeoutException`   | a client-wide or per-endpoint `timeout` is exceeded                             |
| `pydantic.ValidationError` | the response body does not match the declared return type                       |

`EmptyResponseError` subclasses `HttpxicError`, so catching `HttpxicError`
catches every httpxic-specific runtime error. Declaration and argument mistakes
are deliberately plain `TypeError`s, since they are programming errors rather
than something to handle at runtime.

## Escape hatch

`APIClient` exposes `request()`, `get()`, `post()`, `put()`, `patch()` and
`delete()` directly, so anything the decorators do not cover can be written by
hand against `self.http` or those helpers — see `get_current_user` in the usage
example above. They return the raw `httpx.Response` and still honour
`raise_for_status`.

`stream()` is the streaming counterpart: an async context manager yielding an
`httpx.Response` whose body has not been read. Combined with `aiter_sse()`, it
decodes an event stream that `@sse` cannot describe:

```python
from httpxic import APIClient, aiter_sse


class ManualClient(APIClient):
    async def tail_log(self) -> None:
        async with self.stream("GET", "/logs") as response:
            async for event in aiter_sse(response.aiter_lines()):
                print(event.data)
```
