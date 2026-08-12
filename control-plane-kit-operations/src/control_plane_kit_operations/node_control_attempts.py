"""Durable, secret-free node-control intent values."""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib

import rfc8785

from control_plane_kit_core.node_control import (
    DelegatedWorkloadNodeControlGrant,
    NodeControlCommandRequest,
)
from control_plane_kit_core.node_control_transit import (
    DelegatedGatewayNodeControlTransitGrant,
)
from control_plane_kit_operations._temporal import validate_canonical_utc_timestamp


class NodeControlAttemptError(ValueError):
    """Raised when intended node-control evidence is incoherent."""


class NodeControlAttemptConflict(NodeControlAttemptError):
    """Raised when durable replay identity already belongs to another intent."""


class NodeControlAttemptCorrupt(NodeControlAttemptError):
    """Raised when retained intended evidence cannot be reconstructed exactly."""


@dataclass(frozen=True, slots=True)
class NodeControlIntendedAttempt:
    """One exact command intent and the public authority values supporting it."""

    attempt_id: str = field(repr=False)
    actor_subject: str
    current_graph_id: str
    current_realized_projection_id: str
    gateway_runtime_id: str
    transit_key_registration_id: str = field(repr=False)
    workload_key_registration_id: str = field(repr=False)
    transit_authorization_id: str = field(repr=False)
    workload_authorization_id: str = field(repr=False)
    transit_correlation_id: str = field(repr=False)
    workload_correlation_id: str = field(repr=False)
    intended_at: str
    request: NodeControlCommandRequest
    transit_grant: DelegatedGatewayNodeControlTransitGrant
    workload_grant: DelegatedWorkloadNodeControlGrant

    def __post_init__(self) -> None:
        failed = False
        try:
            for value in (
                self.attempt_id, self.actor_subject, self.current_graph_id,
                self.current_realized_projection_id, self.gateway_runtime_id,
                self.transit_key_registration_id,
                self.workload_key_registration_id,
                self.transit_authorization_id,
                self.workload_authorization_id,
                self.transit_correlation_id,
                self.workload_correlation_id,
            ):
                if type(value) is not str or not value or len(value.encode()) > 200:
                    raise ValueError
            validate_canonical_utc_timestamp(self.intended_at)
            if not isinstance(self.request, NodeControlCommandRequest):
                raise ValueError
            if not isinstance(
                self.transit_grant, DelegatedGatewayNodeControlTransitGrant
            ) or not isinstance(
                self.workload_grant, DelegatedWorkloadNodeControlGrant
            ):
                raise ValueError
            request = self.request
            transit = self.transit_grant
            workload = self.workload_grant
            common = (
                request.target,
                request.variable_name,
                request.operation,
                request.command_codec,
                request.request_id,
                request.idempotency_key,
                request.canonical_digest(),
            )
            if (
                transit.attempt_id != self.attempt_id
                or transit.workspace_id != request.target.workspace_id
                or transit.graph_revision != request.target.graph_revision
                or self.current_graph_id != request.target.graph_revision.value
                or (
                    transit.target, transit.variable_name, transit.operation,
                    transit.command_codec, transit.request_id,
                    transit.idempotency_key, transit.request_digest,
                ) != common
                or (
                    workload.target, workload.variable_name, workload.operation,
                    workload.command_codec, workload.request_id,
                    workload.idempotency_key, workload.request_digest,
                ) != common
            ):
                raise ValueError
        except Exception:
            failed = True
        if failed:
            raise NodeControlAttemptError(
                "node-control intended attempt is incoherent"
            ) from None

    @property
    def workspace_id(self) -> str:
        return self.request.target.workspace_id.value

    @property
    def request_id(self) -> str:
        return self.request.request_id

    @property
    def request_bytes(self) -> bytes:
        return self.request.canonical_bytes()

    @property
    def transit_grant_bytes(self) -> bytes:
        return self.transit_grant.canonical_bytes()

    @property
    def workload_grant_bytes(self) -> bytes:
        return self.workload_grant.canonical_bytes()

    @property
    def intent_fingerprint(self) -> str:
        encoded = rfc8785.dumps(
            {
                "profile": "node-control-intent.v1",
                "actor_subject": self.actor_subject,
                "gateway_node_id": self.transit_grant.gateway_node_id.value,
                "request_digest": self.request.canonical_digest().value,
            }
        )
        return hashlib.sha256(encoded).hexdigest()


__all__ = [
    "NodeControlAttemptConflict",
    "NodeControlAttemptCorrupt",
    "NodeControlAttemptError",
    "NodeControlIntendedAttempt",
]
