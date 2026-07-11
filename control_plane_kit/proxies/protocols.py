"""Network protocol descriptors."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class NetworkProtocol:
    """A protocol family understood by a proxy node."""

    name: str
    default_port: int
    endpoint_scheme: str


class HttpProtocol(NetworkProtocol):
    """HTTP reverse-proxy semantics."""

    def __init__(self, default_port: int = 8080) -> None:
        super().__init__("http", default_port, "http")


class TcpProtocol(NetworkProtocol):
    """Opaque TCP forwarding semantics."""

    def __init__(self, default_port: int) -> None:
        super().__init__("tcp", default_port, "tcp")


class PostgresProtocol(NetworkProtocol):
    """Postgres wire protocol treated safely as TCP-like traffic."""

    def __init__(self, default_port: int = 5432) -> None:
        super().__init__("postgres", default_port, "postgresql")
