"""Proxy implementation descriptors."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ProxyImplementation:
    """A runtime implementation family for a proxy node."""

    name: str
    image: str | None = None

    def metadata(self) -> dict[str, object]:
        """Return descriptor metadata."""

        data: dict[str, object] = {"implementation": self.name}
        if self.image is not None:
            data["image"] = self.image
        return data


class PythonImplementation(ProxyImplementation):
    """A small Python/ASGI proxy implementation."""

    def __init__(self, image: str | None = None) -> None:
        super().__init__("python", image)


class NginxImplementation(ProxyImplementation):
    """An nginx-backed HTTP implementation."""

    def __init__(self, image: str = "nginx:1.27-alpine") -> None:
        super().__init__("nginx", image)


class HAProxyImplementation(ProxyImplementation):
    """An HAProxy-backed HTTP or TCP implementation."""

    def __init__(self, image: str = "haproxy:2.9-alpine") -> None:
        super().__init__("haproxy", image)


class PgBouncerImplementation(ProxyImplementation):
    """A PgBouncer-backed Postgres pooling implementation."""

    def __init__(self, image: str = "edoburu/pgbouncer:latest") -> None:
        super().__init__("pgbouncer", image)
