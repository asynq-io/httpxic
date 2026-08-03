from __future__ import annotations

import json
from typing import TYPE_CHECKING

import httpx
import pytest

from httpxic import APIClient, EncodeOptions, patch
from tests.conftest import CreateUser, DemoClient, UpdateUser, User

if TYPE_CHECKING:
    import respx

pytestmark = pytest.mark.anyio


async def test_post_serializes_body_and_parses_response(
    client: DemoClient, respx_mock: respx.MockRouter
) -> None:
    route = respx_mock.post("/users").mock(
        return_value=httpx.Response(201, json={"id": 1, "email": "new@x.com"})
    )

    created = await client.create_user(CreateUser(email="new@x.com"))

    assert created == User(id=1, email="new@x.com")
    sent = json.loads(route.calls.last.request.content)
    assert sent == {"email": "new@x.com"}


async def test_put_combines_path_and_body(
    client: DemoClient, respx_mock: respx.MockRouter
) -> None:
    route = respx_mock.put("/users/7").mock(
        return_value=httpx.Response(200, json={"id": 7, "email": "p@q.com"})
    )

    user = await client.replace_user(CreateUser(email="p@q.com"), user_id=7)

    assert user == User(id=7, email="p@q.com")
    assert json.loads(route.calls.last.request.content) == {"email": "p@q.com"}


async def test_patch_body_with_unset_optional_field_serializes_as_null(
    client: DemoClient, respx_mock: respx.MockRouter
) -> None:
    route = respx_mock.patch("/users/3").mock(
        return_value=httpx.Response(200, json={"id": 3, "email": "a@b.com"})
    )

    await client.update_user(UpdateUser(), user_id=3)

    sent = json.loads(route.calls.last.request.content)
    assert sent == {"email": None}


async def test_patch_with_exclude_unset_omits_unset_optional_field(
    respx_mock: respx.MockRouter, base_url: str
) -> None:
    class ExcludeUnsetClient(APIClient):
        @patch("/users/{user_id}", serializer_options=EncodeOptions(exclude_unset=True))
        async def update_user(self, data: UpdateUser, user_id: int) -> User: ...

    route = respx_mock.patch("/users/3").mock(
        return_value=httpx.Response(200, json={"id": 3, "email": "a@b.com"})
    )

    async with httpx.AsyncClient(base_url=base_url) as http:
        await ExcludeUnsetClient(http).update_user(UpdateUser(), user_id=3)

    sent = json.loads(route.calls.last.request.content)
    assert sent == {}


async def test_post_sends_json_content_type(
    client: DemoClient, respx_mock: respx.MockRouter
) -> None:
    route = respx_mock.post("/users").mock(
        return_value=httpx.Response(201, json={"id": 1, "email": "x@y.com"})
    )

    await client.create_user(CreateUser(email="x@y.com"))

    assert route.calls.last.request.headers["content-type"] == "application/json"
