from importlib.metadata import version

from .client import APIClient
from .decorators import delete, get, head, options, patch, post, put, sse
from .events import ServerSentEvent, aiter_sse
from .exceptions import ClientClosedError, EmptyResponseError, HttpxicError
from .params import Body, Cookie, File, Form, Header, Param, Path, Query
from .types import ClientOptions, DecodeOptions, EncodeOptions, RequestOptions

__version__ = version("httpxic")

__all__ = [
    "APIClient",
    "Body",
    "ClientClosedError",
    "ClientOptions",
    "Cookie",
    "DecodeOptions",
    "EmptyResponseError",
    "EncodeOptions",
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
    "aiter_sse",
    "delete",
    "get",
    "head",
    "options",
    "patch",
    "post",
    "put",
    "sse",
]
