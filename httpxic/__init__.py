from importlib.metadata import version

from httpx2 import ServerSentEvent

from .client import APIClient, ClientT
from .decorators import delete, get, head, options, patch, post, put, sse
from .exceptions import EmptyResponseError, HttpxicError
from .params import Body, Cookie, File, Form, Header, Param, Path, Query
from .types import DecodeOptions, EncodeOptions, EndpointOptions, RequestOptions

__version__ = version("httpxic")

__all__ = [
    "APIClient",
    "Body",
    "ClientT",
    "Cookie",
    "DecodeOptions",
    "EmptyResponseError",
    "EncodeOptions",
    "EndpointOptions",
    "File",
    "Form",
    "Header",
    "HttpxicError",
    "Param",
    "Path",
    "Query",
    "RequestOptions",
    "ServerSentEvent",
    "__version__",
    "delete",
    "get",
    "head",
    "options",
    "patch",
    "post",
    "put",
    "sse",
]
