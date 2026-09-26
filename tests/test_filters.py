from __future__ import annotations

import enum
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Annotated, Any
from urllib.parse import parse_qs

import pytest
from pydantic import BaseModel, PrivateAttr

from httpxic import APIClient, ClientT, Query, get, post
from httpxic.filters import Filter, OffsetLimitFilter, to_params
from tests.conftest import resolve

if TYPE_CHECKING:
    import httpx2
    import respx

pytestmark = pytest.mark.anyio


class Status(enum.Enum):
    ACTIVE = "active"


class UserFilter(Filter):
    id__in: list[int] | None = None
    status: Status | None = None


class ServerSideFilter(OffsetLimitFilter):
    _kwargs: dict[str, Any] = PrivateAttr(default_factory=dict)
    name: str | None = None


class SearchBody(BaseModel):
    name: str


class FilterClient(APIClient[ClientT]):
    @get("/users")
    def list_users(self, filters: UserFilter | None = None) -> list[Any]: ...

    @get("/posts")
    def list_posts(
        self,
        filters: Annotated[ServerSideFilter, Query()],
        limit: Annotated[int | None, Query()] = None,
    ) -> list[Any]: ...

    @post("/users/search")
    def search_users(self, body: SearchBody) -> list[Any]: ...


@pytest.fixture
def client(http: httpx2.Client | httpx2.AsyncClient) -> Any:
    return FilterClient(http)


@pytest.mark.parametrize(
    ("filters", "query"),
    [
        (None, {}),
        (UserFilter(), {}),
        (
            UserFilter(
                q="ada",
                sort=["-id", "name"],
                page=2,
                fields={"id"},
                id__in=[1, 2],
                status=Status.ACTIVE,
            ),
            {
                "q": ["ada"],
                "sort": ["-id", "name"],
                "page": ["2"],
                "fields": ["id"],
                "id__in": ["1", "2"],
                "status": ["active"],
            },
        ),
        (UserFilter.model_validate({"query": "by name"}), {"q": ["by name"]}),
    ],
)
async def test_filter_model_is_sent_as_query_params(
    client: Any,
    respx_mock: respx.MockRouter,
    filters: UserFilter | None,
    query: dict[str, list[str]],
) -> None:
    route = respx_mock.get("/users").respond(json=[])

    await resolve(client.list_users(filters))

    assert parse_qs(route.calls.last.request.url.query.decode()) == query


async def test_explicit_query_param_overrides_filter_field(
    client: Any, respx_mock: respx.MockRouter
) -> None:
    route = respx_mock.get("/posts").respond(json=[])
    filters = ServerSideFilter(offset=10, limit=5, name="x")
    filters._kwargs["hidden"] = 1

    await resolve(client.list_posts(filters, limit=50))

    query = parse_qs(route.calls.last.request.url.query.decode())
    assert query == {"offset": ["10"], "limit": ["50"], "name": ["x"]}


async def test_model_on_post_stays_json_body(
    client: Any, respx_mock: respx.MockRouter
) -> None:
    route = respx_mock.post("/users/search").respond(json=[])

    await resolve(client.search_users(SearchBody(name="ada")))

    request = route.calls.last.request
    assert not request.url.query
    assert request.content == b'{"name":"ada"}'


def test_to_params_serializes_json_compatible_values() -> None:
    class Window(BaseModel):
        since: datetime
        tags: set[str]
        until: datetime | None = None

    since = datetime(2026, 1, 2, tzinfo=timezone.utc)

    assert to_params(Window(since=since, tags={"a"})) == {
        "since": "2026-01-02T00:00:00Z",
        "tags": ["a"],
    }
