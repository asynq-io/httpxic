from __future__ import annotations

__all__ = [
    "ClientClosedError",
    "EmptyResponseError",
    "HttpxicError",
]


class HttpxicError(Exception):
    """Base class for every error raised by httpxic itself."""


class EmptyResponseError(HttpxicError):
    """Raised when a response body is empty but the endpoint cannot return ``None``."""

    def __init__(self, endpoint: str, status_code: int) -> None:
        self.endpoint = endpoint
        self.status_code = status_code
        super().__init__(
            f"{endpoint}: received an empty response body with status {status_code}, "
            f"but the declared return type does not allow None"
        )


class ClientClosedError(HttpxicError, RuntimeError):
    """Raised when an already closed client is re-entered instead of recreated.

    Also a ``RuntimeError``, so handlers written before this class existed keep
    catching it.
    """

    def __init__(self, client_name: str) -> None:
        self.client_name = client_name
        super().__init__(
            f"{client_name} has already been closed, "
            f"create a new instance instead of reusing it"
        )
