from __future__ import annotations

import pytest

from httpxic.params import (
    Body,
    Cookie,
    File,
    Form,
    Header,
    Param,
    Path,
    Query,
)


class ApiKey(Header):
    pass


class Payload(Body):
    pass


class Untagged(Param):
    pass


@pytest.mark.parametrize(
    ("marker", "expected"),
    [
        (Query, "query"),
        (Header, "header"),
        (Cookie, "cookie"),
        (Path, "path"),
        (Body, "body"),
        (Form, "form"),
        (File, "file"),
    ],
)
def test_marker_kind(marker: type[Param | Body], expected: str) -> None:
    assert marker.kind == expected
    assert marker().kind == expected


@pytest.mark.parametrize(
    ("marker", "expected"),
    [(ApiKey, "header"), (Payload, "body")],
)
def test_subclass_inherits_kind(marker: type[Param | Body], expected: str) -> None:
    assert marker().kind == expected


def test_alias_is_stored() -> None:
    assert Query(alias="x").alias == "x"


def test_alias_defaults_to_none() -> None:
    assert Query().alias is None


def test_path_rejects_alias() -> None:
    with pytest.raises(TypeError, match="matched by parameter name"):
        Path(alias="user_id")


def test_path_allows_explicit_none_alias() -> None:
    assert Path(alias=None).alias is None


@pytest.mark.parametrize("marker", [Param, Untagged])
def test_abstract_marker_cannot_be_instantiated(marker: type[Param]) -> None:
    with pytest.raises(TypeError, match="abstract marker"):
        marker()


@pytest.mark.parametrize("marker", [Query, Header, Cookie, Path, Body, Form, File])
def test_markers_have_no_instance_dict(marker: type[Param | Body]) -> None:
    assert not hasattr(marker(), "__dict__")


def test_body_subclasses_are_body_instances() -> None:
    assert isinstance(Form(), Body)
    assert isinstance(File(), Body)
    assert not isinstance(Body(), Param)
