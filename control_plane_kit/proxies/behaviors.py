"""Proxy behavior descriptors."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ProxyBehavior:
    """Base value for proxy behavior metadata."""

    name: str

    def target_ids(self) -> tuple[str, ...]:
        """Return node ids this behavior can route to."""

        return ()

    def mutable_edge(self) -> bool:
        """Return true if the active target can be changed at runtime."""

        return False

    def metadata(self) -> dict[str, object]:
        """Return behavior details suitable for a node descriptor."""

        return {"behavior": self.name}


@dataclass(frozen=True)
class ActiveTarget(ProxyBehavior):
    """Route all traffic to one active target."""

    target: str

    def __init__(self, target: str) -> None:
        object.__setattr__(self, "name", "active-target")
        object.__setattr__(self, "target", target)

    def target_ids(self) -> tuple[str, ...]:
        return (self.target,)

    def mutable_edge(self) -> bool:
        return True

    def metadata(self) -> dict[str, object]:
        return {"behavior": self.name, "active_target": self.target}


@dataclass(frozen=True)
class RoundRobin(ProxyBehavior):
    """Distribute traffic across targets in order."""

    targets: tuple[str, ...]

    def __init__(self, targets: tuple[str, ...] | list[str]) -> None:
        object.__setattr__(self, "name", "round-robin")
        object.__setattr__(self, "targets", tuple(targets))

    def target_ids(self) -> tuple[str, ...]:
        return self.targets

    def metadata(self) -> dict[str, object]:
        return {"behavior": self.name, "targets": self.targets}


@dataclass(frozen=True)
class MirrorTraffic(ProxyBehavior):
    """Send primary traffic to one target and mirror to others."""

    primary: str
    mirrors: tuple[str, ...]

    def __init__(self, primary: str, mirrors: tuple[str, ...] | list[str]) -> None:
        object.__setattr__(self, "name", "mirror")
        object.__setattr__(self, "primary", primary)
        object.__setattr__(self, "mirrors", tuple(mirrors))

    def target_ids(self) -> tuple[str, ...]:
        return (self.primary, *self.mirrors)

    def metadata(self) -> dict[str, object]:
        return {"behavior": self.name, "primary": self.primary, "mirrors": self.mirrors}


@dataclass(frozen=True)
class ConnectionPool(ProxyBehavior):
    """Pool downstream connections.

    This is the shape used by PgBouncer-like Postgres pooling.  It records the
    intent; a runtime-specific implementation owns the real process behavior.
    """

    target: str
    pool_mode: str = "transaction"
    size: int = 20

    def __init__(self, target: str, pool_mode: str = "transaction", size: int = 20) -> None:
        object.__setattr__(self, "name", "connection-pool")
        object.__setattr__(self, "target", target)
        object.__setattr__(self, "pool_mode", pool_mode)
        object.__setattr__(self, "size", size)

    def target_ids(self) -> tuple[str, ...]:
        return (self.target,)

    def metadata(self) -> dict[str, object]:
        return {
            "behavior": self.name,
            "target": self.target,
            "pool_mode": self.pool_mode,
            "size": self.size,
        }
