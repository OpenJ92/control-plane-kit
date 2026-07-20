"""Closed authoring values for process environment bindings."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Mapping, TypeAlias


_ENVIRONMENT_NAME = re.compile(r"[A-Z][A-Z0-9_]{0,127}\Z")
_MAX_PUBLIC_VALUE_BYTES = 16_384
_SECRET_MARKERS = (
    "secret",
    "token",
    "password",
    "credential",
    "private_key",
    "api_key",
)


@dataclass(frozen=True, order=True)
class PublicStaticEnvironmentBinding:
    """One bounded, explicitly non-secret process environment literal."""

    name: str
    value: str

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not _ENVIRONMENT_NAME.fullmatch(self.name):
            raise ValueError("public environment binding name is malformed")
        if any(marker in self.name.lower() for marker in _SECRET_MARKERS):
            raise ValueError(
                "secret-shaped environment names require SecretEnvironmentDelivery"
            )
        if not isinstance(self.value, str):
            raise TypeError("public environment binding value must be a string")
        if "\x00" in self.value or len(self.value.encode("utf-8")) > _MAX_PUBLIC_VALUE_BYTES:
            raise ValueError("public environment binding value is malformed or unbounded")

    def descriptor(self) -> dict[str, str]:
        return {
            "kind": "public-static",
            "name": self.name,
            "value": self.value,
        }


@dataclass(frozen=True, order=True)
class SocketDerivedEnvironmentBinding:
    """One connection-derived assignment retaining its graph edge origin."""

    name: str
    value: str
    edge_id: str

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not _ENVIRONMENT_NAME.fullmatch(self.name):
            raise ValueError("socket environment binding name is malformed")
        if not isinstance(self.value, str) or not self.value.strip() or "\x00" in self.value:
            raise ValueError("socket environment binding value is malformed")
        if not isinstance(self.edge_id, str) or not self.edge_id.strip():
            raise ValueError("socket environment binding edge identity is malformed")

    def descriptor(self) -> dict[str, str]:
        return {
            "kind": "socket-derived",
            "name": self.name,
            "value": self.value,
            "edge_id": self.edge_id,
        }


EnvironmentBinding: TypeAlias = (
    PublicStaticEnvironmentBinding | SocketDerivedEnvironmentBinding
)


def environment_binding_from_descriptor(
    descriptor: Mapping[str, object],
) -> EnvironmentBinding:
    """Decode one closed non-secret graph environment binding."""

    kind = descriptor.get("kind")
    name = descriptor.get("name")
    value = descriptor.get("value")
    if not isinstance(name, str) or not isinstance(value, str):
        raise ValueError("environment binding descriptor is malformed")
    if kind == "public-static":
        if set(descriptor) != {"kind", "name", "value"}:
            raise ValueError("public environment binding descriptor is malformed")
        return PublicStaticEnvironmentBinding(name, value)
    if kind == "socket-derived":
        edge_id = descriptor.get("edge_id")
        if set(descriptor) != {"kind", "name", "value", "edge_id"} or not isinstance(
            edge_id, str
        ):
            raise ValueError("socket environment binding descriptor is malformed")
        return SocketDerivedEnvironmentBinding(name, value, edge_id)
    raise ValueError("unknown environment binding descriptor variant")
