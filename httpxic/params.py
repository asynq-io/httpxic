from __future__ import annotations

from typing import ClassVar

__all__ = [
    "Body",
    "Cookie",
    "File",
    "Form",
    "Header",
    "Param",
    "Path",
    "Query",
]


class _Marker:
    """Base for annotation markers, tagged with the wire ``kind`` they map to."""

    __slots__ = ()

    kind: ClassVar[str]

    def __init__(self) -> None:
        if getattr(type(self), "kind", None) is None:
            msg = (
                f"{type(self).__name__} is an abstract marker and cannot be "
                f"instantiated; use Query, Header, Cookie, Path, Body, Form or File, "
                f"or a subclass of one of them."
            )
            raise TypeError(msg)


class Param(_Marker):
    """Base for markers sent outside the request body, optionally under an alias."""

    __slots__ = ("alias",)

    def __init__(self, *, alias: str | None = None) -> None:
        super().__init__()
        self.alias = alias


class Query(Param):
    """Marker for a URL query string parameter."""

    __slots__ = ()

    kind: ClassVar[str] = "query"


class Header(Param):
    """Marker for a request header."""

    __slots__ = ()

    kind: ClassVar[str] = "header"


class Cookie(Param):
    """Marker for a request cookie."""

    __slots__ = ()

    kind: ClassVar[str] = "cookie"


class Path(Param):
    """Marker for a URL path placeholder, matched by parameter name."""

    __slots__ = ()

    kind: ClassVar[str] = "path"

    def __init__(self, *, alias: str | None = None) -> None:
        if alias is not None:
            msg = (
                "Path parameters are matched by parameter name and cannot be aliased; "
                "rename the function parameter to match the {placeholder} instead."
            )
            raise TypeError(msg)
        super().__init__()


class Body(_Marker):
    """Marker for the JSON-encoded request body."""

    __slots__ = ()

    kind: ClassVar[str] = "body"


class Form(Body):
    """Marker for an ``application/x-www-form-urlencoded`` request body."""

    __slots__ = ()

    kind: ClassVar[str] = "form"


class File(Form):
    """Marker for a ``multipart/form-data`` file upload."""

    __slots__ = ()

    kind: ClassVar[str] = "file"
