"""Pure references and health-only gateway transit advertisements.

The V1 transit protocol denotes the existing NodeHealthReadRequestProfile.V1,
DelegatedGatewayNodeHealthReadTransitGrantProfile.V1 and NodeHealthReadResultProfile.V1
contracts. Advertising it neither supplies authority nor implements transport.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
import re

__all__ = [
    "GatewayTransitDeclaration", "GatewayTransitDeclarationCodec", "GatewayTransitProtocol",
    "RuntimeManagement", "RuntimeManagementCodec", "RuntimeManagementError",
]


_REFERENCE = re.compile(r"[a-z][a-z0-9._-]{0,127}\Z")
_SOCKET = re.compile(r"[a-z][a-z0-9-]{0,63}\Z")


class RuntimeManagementError(ValueError):
    """A bounded management declaration or selection failure."""


def _reference(value: object, *, socket: bool = False) -> None:
    pattern = _SOCKET if socket else _REFERENCE
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise RuntimeManagementError("management reference must be a bounded canonical identifier")


@dataclass(frozen=True)
class RuntimeManagement:
    """Select existing graph values; do not construct management infrastructure."""

    gateway_node_id: str
    management_ingress_id: str

    def __post_init__(self) -> None:
        _reference(self.gateway_node_id)
        _reference(self.management_ingress_id)

    def descriptor(self) -> dict[str, str]:
        return {
            "gateway_node_id": self.gateway_node_id,
            "management_ingress_id": self.management_ingress_id,
        }


class GatewayTransitProtocol(StrEnum):
    NODE_HEALTH_READ_V1 = "gateway-node-health-read-transit.v1"


@dataclass(frozen=True)
class GatewayTransitDeclaration:
    """Advertise one exact health transit socket, independently of own control."""

    provider_socket_name: str
    protocol: GatewayTransitProtocol

    def __post_init__(self) -> None:
        _reference(self.provider_socket_name, socket=True)
        if not isinstance(self.protocol, GatewayTransitProtocol):
            raise RuntimeManagementError("gateway transit protocol must be a supported profile")

    def descriptor(self) -> dict[str, str]:
        return {
            "provider_socket_name": self.provider_socket_name,
            "protocol": self.protocol.value,
        }


class RuntimeManagementCodec:
    """Strict nested codec for the reference pair."""

    def encode(self, value: RuntimeManagement) -> dict[str, str]:
        if not isinstance(value, RuntimeManagement):
            raise RuntimeManagementError("management selection must be typed")
        return value.descriptor()

    def decode(self, value: object) -> RuntimeManagement:
        if not isinstance(value, Mapping) or set(value) != {"gateway_node_id", "management_ingress_id"}:
            raise RuntimeManagementError("management selection fields are invalid")
        return RuntimeManagement(value["gateway_node_id"], value["management_ingress_id"])


class GatewayTransitDeclarationCodec:
    """Strict nested codec for the closed health transit advertisement."""

    def encode(self, value: GatewayTransitDeclaration) -> dict[str, str]:
        if not isinstance(value, GatewayTransitDeclaration):
            raise RuntimeManagementError("gateway transit declaration must be typed")
        return value.descriptor()

    def decode(self, value: object) -> GatewayTransitDeclaration:
        if not isinstance(value, Mapping) or set(value) != {"provider_socket_name", "protocol"}:
            raise RuntimeManagementError("gateway transit declaration fields are invalid")
        try:
            protocol = GatewayTransitProtocol(value["protocol"])
        except (TypeError, ValueError):
            raise RuntimeManagementError("gateway transit protocol is unsupported") from None
        return GatewayTransitDeclaration(value["provider_socket_name"], protocol)
