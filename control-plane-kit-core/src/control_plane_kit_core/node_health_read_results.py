"""Closed semantic observations correlated to an exact declared health request.

A request digest binds context; it is neither a signature nor proof of freshness.
Transport/authentication failures are not values in this result language.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Mapping

from control_plane_kit_core.node_control import NodeControlCanonicalization
from control_plane_kit_core.node_control_surface_reads import (
    WorkloadNodeControlSurfaceDeclaration, WorkloadNodeControlSurfaceDeclarationProfile,
)
from control_plane_kit_core.node_health_reads import (
    NodeHealthReadContractError, NodeHealthReadRequest, _bounded_bytes, _bounded_mapping,
)

MAX_NODE_HEALTH_READ_RESULT_BYTES = 446
_RESULT_KEYS = frozenset({"profile", "canonicalization", "request_id", "request_digest",
                          "declaration_identity", "kind", "outcome"})


class NodeHealthReadResultProfile(StrEnum):
    V1 = "workload-node-health-read-result.v1"


class NodeHealthReadOutcome(StrEnum):
    HEALTHY = "healthy"
    UNHEALTHY = "unhealthy"
    UNKNOWN = "unknown"
    UNSUPPORTED = "unsupported"


@dataclass(frozen=True)
class NodeHealthReadResult:
    request: NodeHealthReadRequest = field(repr=False)
    declaration: WorkloadNodeControlSurfaceDeclaration = field(repr=False)
    outcome: NodeHealthReadOutcome

    def __post_init__(self) -> None:
        _validate_context(self.request, self.declaration)
        if type(self.outcome) is not NodeHealthReadOutcome:
            raise NodeHealthReadContractError("health result outcome is unknown")
        self.canonical_bytes()

    @property
    def profile(self) -> NodeHealthReadResultProfile:
        return NodeHealthReadResultProfile.V1

    @property
    def canonicalization(self) -> NodeControlCanonicalization:
        return NodeControlCanonicalization.JCS_RFC8785_V1

    def descriptor(self) -> dict[str, object]:
        return {"profile": self.profile.value, "canonicalization": self.canonicalization.value,
                "request_id": self.request.request_id, "request_digest": self.request.canonical_digest().value,
                "declaration_identity": self.declaration.identity().value, "kind": self.request.kind.value,
                "outcome": self.outcome.value}

    def canonical_bytes(self) -> bytes:
        return _bounded_bytes(self.descriptor(), _context_bound(self.request))


@dataclass(frozen=True)
class NodeHealthReadResultCodec:
    request: NodeHealthReadRequest = field(repr=False)
    declaration: WorkloadNodeControlSurfaceDeclaration = field(repr=False)

    def __post_init__(self) -> None:
        _validate_context(self.request, self.declaration)

    def encode(self, result: NodeHealthReadResult) -> dict[str, object]:
        if (type(result) is not NodeHealthReadResult or result.request != self.request
                or result.declaration != self.declaration):
            raise NodeHealthReadContractError("health result context mismatch")
        return result.descriptor()

    def decode(self, descriptor: Mapping[str, object]) -> NodeHealthReadResult:
        value = _bounded_mapping(descriptor, _RESULT_KEYS, _context_bound(self.request))
        # Derive all binding claims from the receiving context, not the payload.
        expected = NodeHealthReadResult(self.request, self.declaration, NodeHealthReadOutcome.UNKNOWN).descriptor()
        if any(value[name] != expected[name] for name in _RESULT_KEYS - {"outcome"}):
            raise NodeHealthReadContractError("health result context mismatch")
        try:
            outcome = NodeHealthReadOutcome(value["outcome"])
        except (ValueError, TypeError):
            pass
        else:
            return NodeHealthReadResult(self.request, self.declaration, outcome)
        raise NodeHealthReadContractError("health result outcome is unknown")


def _validate_context(request: object, declaration: object) -> None:
    if (type(request) is not NodeHealthReadRequest or type(declaration) is not WorkloadNodeControlSurfaceDeclaration
            or declaration.profile is not WorkloadNodeControlSurfaceDeclarationProfile.V2
            or request.declaration_identity != declaration.identity()
            or request.target.provider_socket_name != declaration.surface.provider_socket_name
            or request.kind not in declaration.surface.health_reads):
        raise NodeHealthReadContractError("health result requires an exact declared request")


def _context_bound(request: NodeHealthReadRequest) -> int:
    return MAX_NODE_HEALTH_READ_RESULT_BYTES - (128 - len(request.request_id)) - (9 - len(request.kind.value))


__all__ = ["MAX_NODE_HEALTH_READ_RESULT_BYTES", "NodeHealthReadResultProfile", "NodeHealthReadOutcome",
           "NodeHealthReadResult", "NodeHealthReadResultCodec"]
