from __future__ import annotations

from typing import TYPE_CHECKING

import httpx
import pytest
import respx
from pydantic import BaseModel

from httpxic import APIClient, delete, get, patch, post, put

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator


class User(BaseModel):
    id: int
    email: str


class CreateUser(BaseModel):
    email: str


class UpdateUser(BaseModel):
    email: str | None = None


class DemoClient(APIClient):
    @get("/users/{user_id}")
    async def retrieve_user(self, user_id: int) -> User: ...

    @get("/users")
    async def list_users(self) -> list[User]: ...

    @post("/users")
    async def create_user(self, data: CreateUser) -> User: ...

    @put("/users/{user_id}")
    async def replace_user(self, data: CreateUser, user_id: int) -> User: ...

    @patch("/users/{user_id}")
    async def update_user(self, data: UpdateUser, user_id: int) -> User: ...

    @delete("/users/{user_id}")
    async def delete_user(self, user_id: int) -> None: ...


@pytest.fixture(scope="session")
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(scope="session")
def base_url() -> str:
    return "https://api.example.test"


@pytest.fixture
def respx_mock(base_url: str) -> Iterator[respx.MockRouter]:
    with respx.mock(base_url=base_url, assert_all_called=False) as router:
        yield router


@pytest.fixture
async def client(base_url: str) -> AsyncIterator[DemoClient]:
    async with httpx.AsyncClient(base_url=base_url) as http:
        yield DemoClient(http)
