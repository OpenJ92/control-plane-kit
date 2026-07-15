"""Small HTTP message values for package server behavior tests."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Mapping


@dataclass(frozen=True)
class HttpRequest:
    """Minimal HTTP request value used by in-memory server block tests."""

    method: str = "GET"
    path: str = "/"
    query: str = ""
    headers: Mapping[str, str] = field(default_factory=dict)
    body: bytes = b""

    @property
    def path_with_query(self) -> str:
        if not self.query:
            return self.path
        return f"{self.path}?{self.query}"


@dataclass(frozen=True)
class HttpResponse:
    """Minimal HTTP response value used by in-memory server block tests."""

    status_code: int = 200
    headers: Mapping[str, str] = field(default_factory=dict)
    body: bytes = b""

    @classmethod
    def text(cls, body: str, *, status_code: int = 200) -> "HttpResponse":
        return cls(status_code=status_code, headers={"content-type": "text/plain"}, body=body.encode())


HttpHandler = Callable[[HttpRequest], HttpResponse]
