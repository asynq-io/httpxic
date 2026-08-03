from __future__ import annotations

from typing import TYPE_CHECKING

import httpx
import pytest

from tests.conftest import DemoClient, User

if TYPE_CHECKING:
    import respx

pytestmark = pytest.mark.anyio


async def test_retrieve_user_parses_path_and_response(
    client: DemoClient, respx_mock: respx.MockRouter
) -> None:
    route = respx_mock.get("/users/42").mock(
        return_value=httpx.Response(200, json={"id": 42, "email": "a@b.com"})
    )

    user = await client.retrieve_user(user_id=42)

    assert route.called
    assert user == User(id=42, email="a@b.com")


async def test_path_parameter_is_required(
    client: DemoClient, respx_mock: respx.MockRouter
) -> None:
    respx_mock.get("/users/1").mock(
        return_value=httpx.Response(200, json={"id": 1, "email": "x@y.com"})
    )

    with pytest.raises(TypeError):
        await client.retrieve_user()  # type: ignore[call-arg]


async def test_base_url_is_applied(
    client: DemoClient, respx_mock: respx.MockRouter, base_url: str
) -> None:
    route = respx_mock.get("/users/1").mock(
        return_value=httpx.Response(200, json={"id": 1, "email": "x@y.com"})
    )

    await client.retrieve_user(user_id=1)

    assert str(route.calls.last.request.url) == f"{base_url}/users/1"


async def test_raise_for_status_on_error(
    client: DemoClient, respx_mock: respx.MockRouter
) -> None:
    respx_mock.get("/users/404").mock(return_value=httpx.Response(404))

    with pytest.raises(httpx.HTTPStatusError):
        await client.retrieve_user(user_id=404)
