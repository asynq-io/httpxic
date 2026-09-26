from __future__ import annotations

from typing import TYPE_CHECKING

import httpx
import pytest

from tests.conftest import resolve

if TYPE_CHECKING:
    import respx

    from tests.conftest import DemoClient

pytestmark = pytest.mark.anyio


async def test_no_params_when_omitted(
    client: DemoClient, respx_mock: respx.MockRouter
) -> None:
    route = respx_mock.get("/users").mock(return_value=httpx.Response(200, json=[]))

    await resolve(client.list_users())

    url = route.calls.last.request.url
    assert not url.params
