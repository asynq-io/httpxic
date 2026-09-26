from __future__ import annotations

import inspect
from collections.abc import AsyncIterable
from typing import TYPE_CHECKING, Any, TypeVar

import httpx2
import pytest
import respx
from pydantic import BaseModel

from httpxic import APIClient, ClientT, delete, get, patch, post, put

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Iterable, Iterator

T = TypeVar("T")


class User(BaseModel):
    id: int
    email: str


class CreateUser(BaseModel):
    email: str


class UpdateUser(BaseModel):
    email: str | None = None


class DemoClient(APIClient[ClientT]):
    @get("/users/{user_id}")
    def retrieve_user(self, user_id: int) -> User: ...

    @get("/users")
    def list_users(self) -> list[User]: ...

    @post("/users")
    def create_user(self, data: CreateUser) -> User: ...

    @put("/users/{user_id}")
    def replace_user(self, data: CreateUser, user_id: int) -> User: ...

    @patch("/users/{user_id}")
    def update_user(self, data: UpdateUser, user_id: int) -> User: ...

    @delete("/users/{user_id}")
    def delete_user(self, user_id: int) -> None: ...


@pytest.fixture(scope="session")
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(scope="session")
def base_url() -> str:
    return "https://api.example.test"


@pytest.fixture
def respx_mock(base_url: str) -> Iterator[respx.MockRouter]:
    with respx.mock(
        base_url=base_url, assert_all_called=False, using="httpcore2"
    ) as router:
        yield router


@pytest.fixture(params=["sync", "async"])
async def http(
    request: pytest.FixtureRequest, base_url: str
) -> AsyncIterator[httpx2.Client | httpx2.AsyncClient]:
    if request.param == "sync":
        with httpx2.Client(base_url=base_url) as sync_http:
            yield sync_http
    else:
        async with httpx2.AsyncClient(base_url=base_url) as async_http:
            yield async_http


@pytest.fixture
def client(http: httpx2.Client | httpx2.AsyncClient) -> DemoClient[Any]:
    return DemoClient(http)


async def resolve(value: T | Awaitable[T]) -> T:
    """Await ``value`` when an async client produced it, return it otherwise."""
    if inspect.isawaitable(value):
        return await value
    return value


async def collect(events: Iterable[T] | AsyncIterable[T]) -> list[T]:
    """Drain a sync or async event iterator into a list."""
    if isinstance(events, AsyncIterable):
        return [event async for event in events]
    return list(events)
