"""Secret-free public application boundary for gateway key rotation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from control_plane_kit_core.delegation_keys import DelegationKeyPurpose
from control_plane_kit_core.identity import TrustedCommandContext
from control_plane_kit_core.secrets import SecretReference
from control_plane_kit_operations.gateway_key_rotations import (
    GatewayKeyRotationReadModel,
    GatewayKeyRotationStatus,
    GatewayKeyRotationTransition,
)
from control_plane_kit_operations.records import ApprovalDecisionKind


class GatewayKeyRotationApplicationError(ValueError):
    """Bounded public rotation application failure."""


class GatewayKeyRotationApplicationConflict(GatewayKeyRotationApplicationError):
    """The command conflicts with durable rotation truth."""


class GatewayKeyRotationApplicationNotFound(GatewayKeyRotationApplicationError):
    """The requested rotation does not exist in the workspace."""


@dataclass(frozen=True)
class RequestGatewayKeyRotationProgram:
    workspace_id: str
    gateway_node_id: str
    purpose: DelegationKeyPurpose
    issuer: str
    old_key_id: str
    new_secret_reference: SecretReference
    key_generation_correlation: str
    maximum_grant_lifetime_seconds: int
    clock_skew_seconds: int
    idempotency_key: str
    requested_at: str


@dataclass(frozen=True)
class RequestGatewayKeyRotationProgramApproval:
    workspace_id: str
    session_id: str
    rotation_id: str
    idempotency_key: str
    comment: str | None = None


@dataclass(frozen=True)
class DecideGatewayKeyRotationProgram:
    workspace_id: str
    session_id: str
    rotation_id: str
    approval_request_id: str
    decision: ApprovalDecisionKind
    idempotency_key: str
    comment: str | None = None


@dataclass(frozen=True)
class AdvanceGatewayKeyRotationProgram:
    workspace_id: str
    rotation_id: str
    expected_version: int
    idempotency_key: str


@dataclass(frozen=True)
class GatewayKeyRotationPublicView:
    rotation_id: str
    workspace_id: str
    gateway_node_id: str
    purpose: DelegationKeyPurpose
    issuer: str
    old_key_id: str
    new_key_id: str | None
    status: GatewayKeyRotationStatus
    version: int
    correlation_id: str
    requested_by: str
    requested_at: str
    drain_deadline_epoch: int | None
    failure_code: str | None
    updated_at: str | None

    @classmethod
    def from_read_model(
        cls,
        value: GatewayKeyRotationReadModel,
    ) -> "GatewayKeyRotationPublicView":
        return cls(**value.__dict__)

    def descriptor(self) -> dict[str, object]:
        return {
            "rotation_id": self.rotation_id,
            "workspace_id": self.workspace_id,
            "gateway_node_id": self.gateway_node_id,
            "purpose": self.purpose.value,
            "issuer": self.issuer,
            "old_key_id": self.old_key_id,
            "new_key_id": self.new_key_id,
            "status": self.status.value,
            "version": self.version,
            "correlation_id": self.correlation_id,
            "requested_by": self.requested_by,
            "requested_at": self.requested_at,
            "drain_deadline_epoch": self.drain_deadline_epoch,
            "failure_code": self.failure_code,
            "updated_at": self.updated_at,
        }


@dataclass(frozen=True)
class GatewayKeyRotationApprovalView:
    rotation: GatewayKeyRotationPublicView
    approval_request_id: str
    approval_decision_id: str | None
    decision: ApprovalDecisionKind | None
    replayed: bool

    def descriptor(self) -> dict[str, object]:
        return {
            "rotation": self.rotation.descriptor(),
            "approval_request_id": self.approval_request_id,
            "approval_decision_id": self.approval_decision_id,
            "decision": None if self.decision is None else self.decision.value,
            "replayed": self.replayed,
        }


@dataclass(frozen=True)
class GatewayKeyRotationProgramView:
    rotation: GatewayKeyRotationPublicView
    phase: str
    outcome: str
    replayed: bool = False

    def descriptor(self) -> dict[str, object]:
        return {
            "rotation": self.rotation.descriptor(),
            "phase": self.phase,
            "outcome": self.outcome,
            "replayed": self.replayed,
        }


@dataclass(frozen=True)
class GatewayKeyRotationTransitionView:
    transition_id: str
    from_status: GatewayKeyRotationStatus
    to_status: GatewayKeyRotationStatus
    from_version: int
    to_version: int
    advanced_by: str
    advanced_at: str
    failure_code: str | None

    @classmethod
    def from_transition(
        cls,
        value: GatewayKeyRotationTransition,
    ) -> "GatewayKeyRotationTransitionView":
        return cls(
            transition_id=value.transition_id,
            from_status=value.from_status,
            to_status=value.to_status,
            from_version=value.from_version,
            to_version=value.to_version,
            advanced_by=value.advanced_by,
            advanced_at=value.advanced_at,
            failure_code=value.failure_code,
        )

    def descriptor(self) -> dict[str, object]:
        return {
            "transition_id": self.transition_id,
            "from_status": self.from_status.value,
            "to_status": self.to_status.value,
            "from_version": self.from_version,
            "to_version": self.to_version,
            "advanced_by": self.advanced_by,
            "advanced_at": self.advanced_at,
            "failure_code": self.failure_code,
        }


class GatewayKeyRotationApplication(Protocol):
    """Shared HTTP/MCP-facing operations service; no concrete effects here."""

    def request(
        self,
        command: RequestGatewayKeyRotationProgram,
        context: TrustedCommandContext,
    ) -> GatewayKeyRotationPublicView: ...

    def request_approval(
        self,
        command: RequestGatewayKeyRotationProgramApproval,
        context: TrustedCommandContext,
    ) -> GatewayKeyRotationApprovalView: ...

    def decide(
        self,
        command: DecideGatewayKeyRotationProgram,
        context: TrustedCommandContext,
    ) -> GatewayKeyRotationApprovalView: ...

    def advance(
        self,
        command: AdvanceGatewayKeyRotationProgram,
        context: TrustedCommandContext,
    ) -> GatewayKeyRotationProgramView: ...

    def list(
        self,
        workspace_id: str,
        context: TrustedCommandContext,
    ) -> tuple[GatewayKeyRotationPublicView, ...]: ...

    def detail(
        self,
        workspace_id: str,
        rotation_id: str,
        context: TrustedCommandContext,
    ) -> GatewayKeyRotationPublicView: ...

    def transitions(
        self,
        workspace_id: str,
        rotation_id: str,
        context: TrustedCommandContext,
    ) -> tuple[GatewayKeyRotationTransitionView, ...]: ...


__all__ = [
    "AdvanceGatewayKeyRotationProgram",
    "DecideGatewayKeyRotationProgram",
    "GatewayKeyRotationApplication",
    "GatewayKeyRotationApplicationConflict",
    "GatewayKeyRotationApplicationError",
    "GatewayKeyRotationApplicationNotFound",
    "GatewayKeyRotationApprovalView",
    "GatewayKeyRotationProgramView",
    "GatewayKeyRotationPublicView",
    "GatewayKeyRotationTransitionView",
    "RequestGatewayKeyRotationProgram",
    "RequestGatewayKeyRotationProgramApproval",
]
