from __future__ import annotations

__all__ = [
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
