from __future__ import annotations

import ast
import importlib
import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qs

import httpx2
import pytest

from httpxic import ServerSentEvent
from httpxic.codegen import _literal, generate, main
from httpxic.filters import Filter, OffsetLimitFilter
from tests.conftest import collect, resolve

if TYPE_CHECKING:
    from collections.abc import Iterator
    from types import ModuleType

    import respx

pytest.importorskip("datamodel_code_generator")

pytestmark = pytest.mark.timeout(30)

FIXTURE = Path(__file__).parent / "fixtures" / "petstore.yaml"
FILTERS_FIXTURE = Path(__file__).parent / "fixtures" / "filters.json"
FILTERS_PACKAGE = "filters_client"
PACKAGE = "petstore_client"
PET = {"id": 7, "name": "Rex"}


@pytest.fixture(scope="module")
def generated(tmp_path_factory: pytest.TempPathFactory) -> Iterator[ModuleType]:
    root = tmp_path_factory.mktemp("generated")
    main(["generate", "--config", str(write_config(root, spec=str(FIXTURE)))])
    with pytest.MonkeyPatch.context() as patch:
        patch.syspath_prepend(str(root))
        yield importlib.import_module(PACKAGE)
    for name in [m for m in sys.modules if m.split(".")[0] == PACKAGE]:
        del sys.modules[name]


@pytest.fixture
def pets(generated: ModuleType, http: httpx2.Client | httpx2.AsyncClient) -> Any:
    return generated.PetStoreClient(http)


@pytest.fixture
def client_source(generated: ModuleType) -> str:
    return Path(generated.__file__).with_name("client.py").read_text()


def write_spec(tmp_path: Path, **overrides: Any) -> Path:
    spec = {"openapi": "3.0.3", "info": {"title": "demo"}, "paths": {}, **overrides}
    path = tmp_path / "openapi.json"
    path.write_text(json.dumps(spec))
    return path


def write_config(tmp_path: Path, name: str = "config.json", **config: Any) -> Path:
    path = tmp_path / name
    path.write_text(json.dumps({"output": PACKAGE, **config}))
    return path


def generate_source(
    tmp_path: Path, config: dict[str, Any] | None = None, **overrides: Any
) -> str:
    spec = write_spec(tmp_path, **overrides)
    config_path = write_config(tmp_path, spec=str(spec), output="out", **config or {})
    main(["generate", "--config", str(config_path)])
    return (tmp_path / "out" / "client.py").read_text()


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("kwargs", "query", "cookie"),
    [
        ({}, {}, None),
        (
            {"page_size": 10, "status": "sold", "tags": ["a", "b"], "session": "s1"},
            {"page-size": ["10"], "status": ["sold"], "tags": ["a", "b"]},
            "session=s1",
        ),
    ],
)
async def test_query_header_and_cookie_params_use_wire_names(
    pets: Any,
    respx_mock: respx.MockRouter,
    kwargs: dict[str, Any],
    query: dict[str, list[str]],
    cookie: str | None,
) -> None:
    route = respx_mock.get("/pets").respond(json=[PET])

    result = await resolve(pets.list_pets(x_request_id="r1", **kwargs))

    request = route.calls.last.request
    assert parse_qs(request.url.query.decode()) == query
    assert request.headers["X-Request-Id"] == "r1"
    assert request.headers.get("Cookie") == cookie
    assert [pet.model_dump(exclude_none=True) for pet in result] == [PET]


def test_enum_parameter_is_hoisted_into_a_model(generated: ModuleType) -> None:
    assert [m.value for m in generated.client.ListPetsStatus] == ["available", "sold"]


@pytest.mark.anyio
async def test_path_placeholder_is_renamed_to_a_parameter(
    pets: Any, respx_mock: respx.MockRouter
) -> None:
    respx_mock.get("/pets/7").respond(json=PET)

    pet = await resolve(pets.get_pet(7))

    assert pet.name == "Rex"


@pytest.mark.anyio
async def test_json_body_is_serialized_by_alias(
    generated: ModuleType, pets: Any, respx_mock: respx.MockRouter
) -> None:
    route = respx_mock.post("/pets").respond(201, json=PET)
    body = generated.client.Pet(id=1, name="Rex", **{"pet-type": "dog"})

    await resolve(pets.create_pet(body=body))

    assert json.loads(route.calls.last.request.content) == {
        "id": 1,
        "name": "Rex",
        "pet-type": "dog",
        "tags": None,
    }


@pytest.mark.anyio
async def test_no_content_response_returns_none(
    pets: Any, respx_mock: respx.MockRouter
) -> None:
    respx_mock.delete("/pets/7").respond(204)

    assert await resolve(pets.delete_pet(7)) is None


@pytest.mark.anyio
async def test_non_json_response_returns_raw_response(
    pets: Any, respx_mock: respx.MockRouter
) -> None:
    respx_mock.get("/pets/7/photo").respond(content=b"\x89PNG")

    response = await resolve(pets.get_pets_pet_id_photo("7"))

    assert isinstance(response, httpx2.Response)
    assert response.content == b"\x89PNG"


@pytest.mark.anyio
async def test_urlencoded_form_fields(pets: Any, respx_mock: respx.MockRouter) -> None:
    route = respx_mock.post("/login").respond(json={"ok": True})

    result = await resolve(pets.login(username="u", pin="1234"))

    assert parse_qs(route.calls.last.request.content.decode()) == {
        "username": ["u"],
        "pin": ["1234"],
    }
    assert result == {"ok": True}


@pytest.mark.anyio
async def test_multipart_upload_with_file(
    pets: Any, respx_mock: respx.MockRouter
) -> None:
    route = respx_mock.put("/pets/7/photo").respond(json={"ok": True})

    await resolve(pets.upload_photo("7", file=b"PNGDATA", caption="cute"))

    request = route.calls.last.request
    assert request.headers["Content-Type"].startswith("multipart/form-data")
    assert b"PNGDATA" in request.content
    assert b"cute" in request.content


@pytest.mark.anyio
async def test_raw_body_is_sent_with_its_media_type(
    pets: Any, respx_mock: respx.MockRouter
) -> None:
    route = respx_mock.post("/pets/7/photo").respond(json=PET)

    pet = await resolve(pets.upload_raw("7", body=b"\x89PNG", caption="cute"))

    request = route.calls.last.request
    assert request.content == b"\x89PNG"
    assert request.headers["Content-Type"] == "application/octet-stream"
    assert parse_qs(request.url.query.decode()) == {"caption": ["cute"]}
    assert pet.name == "Rex"


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("path", "stream", "data"),
    [
        ("/events", "stream_events", json.dumps(PET)),
        ("/events/typed", "stream_typed", '{"n": 3}'),
    ],
)
async def test_sse_events_are_validated_against_the_schema(
    pets: Any, respx_mock: respx.MockRouter, path: str, stream: str, data: str
) -> None:
    respx_mock.get(path).respond(
        content=f"data: {data}\n\n", headers={"Content-Type": "text/event-stream"}
    )

    events = await collect(getattr(pets, stream)())

    assert [event.model_dump(exclude_none=True) for event in events] == [
        json.loads(data)
    ]


@pytest.mark.anyio
async def test_sse_without_json_schema_yields_raw_events(
    pets: Any, respx_mock: respx.MockRouter
) -> None:
    route = respx_mock.post("/events").respond(
        content="event: tick\ndata: hello\n\n",
        headers={"Content-Type": "text/event-stream"},
    )

    events = await collect(pets.stream_raw(body="go"))

    assert route.calls.last.request.content == b'"go"'
    assert [(e.event, e.data) for e in events] == [("tick", "hello")]
    assert isinstance(events[0], ServerSentEvent)


@pytest.mark.anyio
async def test_hoisted_inline_schemas_round_trip(
    generated: ModuleType, pets: Any, respx_mock: respx.MockRouter
) -> None:
    route = respx_mock.patch("/pets/7").respond(json={"updated": True})

    result = await resolve(
        pets.request_2(7, body=generated.client.RequestRequest(name="Max"))
    )

    request = route.calls.last.request
    assert json.loads(request.content) == {"name": "Max"}
    assert request.headers["Content-Type"] == "application/merge-patch+json"
    assert result == generated.client.RequestResponse(updated=True)


@pytest.mark.parametrize(
    "name",
    ["list_pets_2", "request_2", "get_2", "_2_version", "get_pets_pet_id_photo"],
)
def test_method_names_are_sanitized_and_deduplicated(
    generated: ModuleType, name: str
) -> None:
    assert callable(getattr(generated.PetStoreClient, name))


def test_keyword_parameter_gets_alias(generated: ModuleType) -> None:
    hints = generated.PetStoreClient._2_version.__wrapped__.__annotations__

    assert hints["from_"].__metadata__[0].alias == "from"


def test_colliding_schema_name_is_renamed(generated: ModuleType) -> None:
    assert [m.value for m in generated.client.Field2] == ["x"]


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("plain", '"plain"'),
        ("it's", '"it\'s"'),
        ('say "hi"', "'say \"hi\"'"),
        ("both ' \"\n\\", repr("both ' \"\n\\")),
        ({"Content-Type": "text/plain"}, '{"Content-Type": "text/plain"}'),
    ],
)
def test_literal_prefers_double_quotes(value: object, expected: str) -> None:
    assert _literal(value) == expected
    assert ast.literal_eval(expected) == value


@pytest.mark.parametrize(
    ("operation", "reason"),
    [
        ("bodyOnGet", "request body on GET"),
        ("emptyBody", "request body without content"),
        ("eventsOnPut", "event stream on PUT"),
        ("badFormField", "form field 'first-name' is not a valid parameter name"),
        ("keywordFormField", "form field 'from' is not a valid parameter name"),
        ("selfFormField", "form field 'self' is not a valid parameter name"),
        ("nestedFormField", "non-scalar parameter 'owner'"),
        ("deepObjectFilter", "'deepObject' parameter 'filter'"),
        ("querystringParam", "'querystring' parameter 'q'"),
        ("objectQuery", "non-scalar parameter 'filter'"),
        ("externalRef", "external $ref 'other.yaml#/Param'"),
        ("danglingRef", "unresolvable $ref '#/components/schemas/Missing'"),
        ("traceIt", "TRACE operations are not supported"),
    ],
)
def test_unsupported_operations_are_skipped_with_reason(
    client_source: str, operation: str, reason: str
) -> None:
    assert f'    # skipped "{operation}": {reason}\n' in client_source


def test_untrusted_path_cannot_inject_code(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = "/x'); import os; ('{id}"
    source = generate_source(
        tmp_path,
        paths={
            path: {"get": {"operationId": "x\nimport os", "responses": {}}},
            "/y": {"trace": {"operationId": "t\nimport os", "responses": {}}},
        },
    )

    tree = ast.parse(source)
    decorator = tree.body[-1].body[0].decorator_list[0]
    assert decorator.args[0].value == "/x'); import os; ('{id}"
    assert not [n for n in ast.walk(tree) if isinstance(n, ast.Import)]
    assert "skipped 't\\nimport os'" in capsys.readouterr().err


def test_spec_without_schemas_writes_no_models(tmp_path: Path) -> None:
    source = generate_source(
        tmp_path,
        paths={"/ping": {"get": {"responses": {"200": {"description": "ok"}}}}},
    )

    assert "from .models" not in source
    assert not (tmp_path / "out" / "models.py").exists()
    assert "    def get_ping(self) -> None:" in source


@pytest.mark.parametrize(
    ("info", "config", "expected"),
    [
        ({"title": "demo"}, {}, "class DemoClient(APIClient[ClientT]):\n    pass"),
        ({"title": "API"}, {}, "class APIClient2(APIClient[ClientT]):"),
        ({}, {}, "class ApiClient(APIClient[ClientT]):"),
        ({"title": "demo"}, {"class_name": "Shop"}, "class Shop(APIClient[ClientT]):"),
    ],
)
def test_class_name(
    tmp_path: Path, info: dict[str, str], config: dict[str, str], expected: str
) -> None:
    source = generate_source(tmp_path, config, info=info)

    assert expected in source
    init = (tmp_path / "out" / "__init__.py").read_text()
    assert init.startswith(
        f"from .client import {expected.split()[1].split('(', maxsplit=1)[0]}\n"
    )


@pytest.mark.parametrize("name", ["1x", "class", "APIClient", 42])
def test_invalid_class_name_is_rejected(tmp_path: Path, name: object) -> None:
    with pytest.raises(SystemExit, match="is not a valid class name"):
        generate_source(tmp_path, {"class_name": name})


@pytest.mark.parametrize("content", ["swagger: '2.0'\n", "- not\n- a mapping\n"])
def test_non_openapi3_document_is_rejected(tmp_path: Path, content: str) -> None:
    spec = tmp_path / "spec.yaml"
    spec.write_text(content)

    with pytest.raises(SystemExit, match=r"not an OpenAPI 3\.x document"):
        main(["generate", "--config", str(write_config(tmp_path, spec=str(spec)))])


def test_spec_is_fetched_from_url(
    tmp_path: Path, base_url: str, respx_mock: respx.MockRouter
) -> None:
    spec = {"openapi": "3.1.0", "info": {"title": "remote"}, "paths": {}}
    respx_mock.get("/openapi.json").respond(json=spec)

    config = write_config(tmp_path, spec=f"{base_url}/openapi.json")

    main(["generate", "--config", str(config)])

    assert "class RemoteClient(" in (tmp_path / PACKAGE / "client.py").read_text()


def test_model_renamed_by_datamodel_codegen_fails_loudly(tmp_path: Path) -> None:
    with pytest.raises(SystemExit, match="did not emit the models EmailStr"):
        generate_source(
            tmp_path,
            paths={
                "/me": {
                    "get": {
                        "responses": {
                            "200": {
                                "description": "ok",
                                "content": {
                                    "application/json": {
                                        "schema": {
                                            "$ref": "#/components/schemas/EmailStr"
                                        }
                                    }
                                },
                            }
                        }
                    }
                }
            },
            components={
                "schemas": {
                    "EmailStr": {
                        "type": "object",
                        "properties": {"x": {"type": "string", "format": "email"}},
                    }
                }
            },
        )


def test_missing_codegen_extra_is_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(sys.modules, "datamodel_code_generator", None)

    with pytest.raises(SystemExit, match=r"pip install 'httpxic\[codegen\]'"):
        generate_source(tmp_path, components={"schemas": {"A": {"type": "object"}}})


SCHEMAS = {
    "schemas": {"A": {"type": "object", "properties": {"x": {"type": "string"}}}}
}


@pytest.mark.parametrize(
    "config_file",
    [
        "spec: openapi.json\noutput: out\n"
        "model_options:\n  custom_file_header: '# custom header'\n",
        '{"spec": "openapi.json", "output": "out",'
        ' "model_options": {"custom_file_header": "# custom header"}}',
    ],
)
def test_model_options_in_config_override_defaults(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, config_file: str
) -> None:
    write_spec(tmp_path, components=SCHEMAS)
    config = tmp_path / "config.yaml"
    config.write_text(config_file)
    monkeypatch.chdir(tmp_path.parent)

    main(["generate", "--config", str(config.relative_to(tmp_path.parent))])

    assert (tmp_path / "out" / "models.py").read_text().startswith("# custom header\n")


def test_model_options_are_passed_to_datamodel_codegen(tmp_path: Path) -> None:
    output = tmp_path / "out"

    generate(
        str(write_spec(tmp_path, components=SCHEMAS)),
        output,
        model_options={"target_python_version": "3.12", "use_union_operator": False},
    )

    assert "x: Optional[str] = None" in (output / "models.py").read_text()


@pytest.mark.parametrize(
    ("options", "message"),
    [
        ({"bogus": 1}, "invalid model options"),
        ({"output": "x.py"}, "model options output are set by httpxic"),
    ],
)
def test_invalid_model_options_are_rejected(
    tmp_path: Path, options: dict[str, Any], message: str
) -> None:
    with pytest.raises(SystemExit, match=message):
        generate(
            str(write_spec(tmp_path, components=SCHEMAS)),
            tmp_path / "out",
            model_options=options,
        )


def test_models_share_a_generated_base_model(tmp_path: Path) -> None:
    generate_source(tmp_path, components=SCHEMAS)

    models = (tmp_path / "out" / "models.py").read_text()
    assert "class BaseModel(_BaseModel):" in models
    assert models.count("populate_by_name=True") == 1
    assert "class A(BaseModel):" in models


def test_base_model_replaces_the_generated_base(tmp_path: Path) -> None:
    generate_source(
        tmp_path, {"base_model": "my_app.schemas:BaseSchema"}, components=SCHEMAS
    )

    models = (tmp_path / "out" / "models.py").read_text()
    assert "from my_app.schemas import BaseSchema\n" in models
    assert "class A(BaseSchema):" in models
    assert "ConfigDict" not in models


@pytest.mark.parametrize("base_model", ["my_app.BaseSchema", "my_app:", "1x:A", 42])
def test_invalid_base_model_is_rejected(tmp_path: Path, base_model: object) -> None:
    with pytest.raises(SystemExit, match=r"is not a 'package\.module:Class' path"):
        generate_source(tmp_path, {"base_model": base_model}, components=SCHEMAS)


def test_config_must_be_a_mapping(tmp_path: Path) -> None:
    config = tmp_path / "config.yaml"
    config.write_text("- a\n- b\n")

    with pytest.raises(SystemExit, match="config must be a mapping"):
        main(["generate", "--config", str(config)])


@pytest.mark.parametrize(
    ("config", "message"),
    [
        ({"spec": "x", "output": "o", "bogus": 1}, "unknown config keys bogus"),
        ({}, "missing config keys spec, output"),
        (
            {"spec": "x", "output": "o", "model_options": ["a"]},
            "model_options must be a mapping",
        ),
        ({"spec": "x", "output": "o", "filters": "yes"}, "filters must be a boolean"),
    ],
)
def test_invalid_config_is_rejected(
    tmp_path: Path, config: dict[str, Any], message: str
) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(config))

    with pytest.raises(SystemExit, match=message):
        main(["generate", "--config", str(config_path)])


def test_generated_files_share_the_httpxic_header(generated: ModuleType) -> None:
    package = Path(generated.__file__).parent

    for name in ("client.py", "models.py"):
        header = (package / name).read_text().splitlines()[0]
        assert header == "# Generated by httpxic from an OpenAPI spec."


def test_multi_name_imports_are_wrapped_one_per_line(client_source: str) -> None:
    assert "from collections.abc import Iterator\n" in client_source
    assert "from typing import (\n    Annotated,\n    Any,\n)\n" in client_source
    assert "from .models import (\n    Field2,\n    ListPetsStatus,\n" in client_source


@pytest.fixture(scope="module")
def filters_generated(
    tmp_path_factory: pytest.TempPathFactory,
) -> Iterator[ModuleType]:
    root = tmp_path_factory.mktemp("filters")
    config = write_config(root, spec=str(FILTERS_FIXTURE), output=FILTERS_PACKAGE)
    main(["generate", "--config", str(config)])
    with pytest.MonkeyPatch.context() as patch:
        patch.syspath_prepend(str(root))
        yield importlib.import_module(FILTERS_PACKAGE)
    for name in [m for m in sys.modules if m.split(".")[0] == FILTERS_PACKAGE]:
        del sys.modules[name]


def filter_source(tmp_path: Path, *params: dict[str, Any]) -> str:
    return generate_source(
        tmp_path,
        paths={
            "/items": {
                "get": {
                    "operationId": "list",
                    "parameters": list(params),
                    "responses": {},
                }
            }
        },
    )


def query_param(name: str, *, required: bool = False) -> dict[str, Any]:
    return {
        "name": name,
        "in": "query",
        "required": required,
        "schema": {"anyOf": [{"type": "string"}, {"type": "null"}]},
    }


@pytest.mark.anyio
async def test_fastapi_views_filter_is_generated_as_filter_class(
    filters_generated: ModuleType,
    http: httpx2.Client | httpx2.AsyncClient,
    respx_mock: respx.MockRouter,
) -> None:
    from filters_client import client, models  # noqa: PLC0415

    route = respx_mock.get("/users").respond(json=[{"id": 1, "name": "ada"}])
    filters = client.ListUsersFilter(
        q="ada", sort=["-id"], page=2, id__in=[1, 2], status=models.Status.active
    )

    users = await resolve(
        filters_generated.FiltersClient(http).list_users(filters=filters)
    )

    assert issubclass(client.ListUsersFilter, Filter)
    assert issubclass(client.ListPostsFilter, OffsetLimitFilter)
    assert users == [models.User(id=1, name="ada")]
    assert parse_qs(route.calls.last.request.url.query.decode()) == {
        "q": ["ada"],
        "sort": ["-id"],
        "page": ["2"],
        "id__in": ["1", "2"],
        "status": ["active"],
    }


@pytest.mark.anyio
async def test_operation_without_filter_params_keeps_raw_params(
    filters_generated: ModuleType,
    http: httpx2.Client | httpx2.AsyncClient,
    respx_mock: respx.MockRouter,
) -> None:
    route = respx_mock.get("/ping").respond(json={})

    await resolve(filters_generated.FiltersClient(http).ping(verbose=True))

    assert route.calls.last.request.url.query == b"verbose=true"


def test_filters_can_be_disabled_in_config(tmp_path: Path) -> None:
    spec = json.loads(FILTERS_FIXTURE.read_text())

    source = generate_source(tmp_path, {"filters": False}, **spec)

    assert "httpxic.filters" not in source
    assert 'id_in: Annotated[list[int] | None, Query(alias="id__in")] = None' in source
    assert "sort: Annotated[list[str] | None, Query()] = None" in source


@pytest.mark.parametrize(
    ("params", "expected"),
    [
        (
            [query_param("created-at__gte")],
            "class ListFilter(BaseFilter):\n"
            '    created_at_gte: str | None = Field(default=None, alias="created-at__gte")\n',
        ),
        (
            [query_param("org-id__eq", required=True)],
            'org_id_eq: str | None = Field(alias="org-id__eq")\n',
        ),
        (
            [query_param("tenant__eq", required=True)],
            "filters: Annotated[ListFilter, Query()]) -> Response",
        ),
        (
            [query_param("q"), query_param("query")],
            "class ListFilter(SearchFilter):\n"
            '    query_2: str | None = Field(default=None, alias="query")\n',
        ),
        (
            [query_param("cursor"), query_param("page_size")],
            "class ListFilter(CursorPaginationFilter):\n    pass\n",
        ),
        (
            [query_param("q", required=True)],
            "q: Annotated[str | None, Query()]) -> Response",
        ),
    ],
)
def test_filter_class_detection(
    tmp_path: Path, params: list[dict[str, Any]], expected: str
) -> None:
    assert expected in filter_source(tmp_path, *params)


def test_nullable_union_query_param_is_skipped(tmp_path: Path) -> None:
    param = query_param("x")
    param["schema"]["anyOf"].append({"type": "integer"})

    source = filter_source(tmp_path, param)

    assert "# skipped \"list\": non-scalar parameter 'x'" in source
