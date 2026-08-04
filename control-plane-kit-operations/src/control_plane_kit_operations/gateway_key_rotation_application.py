"""Secret-free public application boundary for gateway key rotation."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Callable, Protocol

from control_plane_kit_core.delegation_keys import DelegationKeyPurpose
from control_plane_kit_core.identity import TrustedCommandContext
from control_plane_kit_core.secrets import SecretReference
from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_operations.approvals import (
    ApprovalAuthorizationDenied,
    ApprovalCommandService,
    ApprovalIdempotencyConflict,
    ApprovalStateConflict,
    ApprovalTargetNotFound,
    DecideApproval,
    RequestGatewayKeyRotationApproval,
)
from control_plane_kit_operations.gateway_key_rotations import (
    GatewayKeyRotation,
    GatewayKeyRotationAuthorizationDenied,
    GatewayKeyRotationConflict,
    GatewayKeyRotationNotFound,
    GatewayKeyRotationReadModel,
    GatewayKeyRotationService,
    GatewayKeyRotationStatus,
    GatewayKeyRotationTransition,
    RequestGatewayKeyRotation,
    gateway_key_rotation_read_model,
)
from control_plane_kit_operations.records import ApprovalDecisionKind
from control_plane_kit_operations.workflows import IdempotencyKey


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

    @classmethod
    def from_descriptor(cls, value: object) -> "GatewayKeyRotationPublicView":
        if not isinstance(value, dict):
            raise GatewayKeyRotationApplicationConflict(
                "rotation command receipt is malformed"
            )
        try:
            return cls(
                rotation_id=str(value["rotation_id"]),
                workspace_id=str(value["workspace_id"]),
                gateway_node_id=str(value["gateway_node_id"]),
                purpose=DelegationKeyPurpose(str(value["purpose"])),
                issuer=str(value["issuer"]),
                old_key_id=str(value["old_key_id"]),
                new_key_id=(
                    None if value["new_key_id"] is None else str(value["new_key_id"])
                ),
                status=GatewayKeyRotationStatus(str(value["status"])),
                version=int(value["version"]),
                correlation_id=str(value["correlation_id"]),
                requested_by=str(value["requested_by"]),
                requested_at=str(value["requested_at"]),
                drain_deadline_epoch=(
                    None
                    if value["drain_deadline_epoch"] is None
                    else int(value["drain_deadline_epoch"])
                ),
                failure_code=(
                    None if value["failure_code"] is None else str(value["failure_code"])
                ),
                updated_at=(
                    None if value["updated_at"] is None else str(value["updated_at"])
                ),
            )
        except (KeyError, TypeError, ValueError) as error:
            raise GatewayKeyRotationApplicationConflict(
                "rotation command receipt is malformed"
            ) from error


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
    failure_code: str | None = None

    def __post_init__(self) -> None:
        if self.failure_code is not None and (
            not isinstance(self.failure_code, str)
            or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,199}", self.failure_code)
            is None
        ):
            raise ValueError("rotation program failure code is malformed")

    def descriptor(self) -> dict[str, object]:
        result: dict[str, object] = {
            "rotation": self.rotation.descriptor(),
            "phase": self.phase,
            "outcome": self.outcome,
            "replayed": self.replayed,
        }
        if self.failure_code is not None:
            result["failure_code"] = self.failure_code
        return result

    @classmethod
    def from_descriptor(
        cls,
        value: object,
        *,
        replayed: bool,
    ) -> "GatewayKeyRotationProgramView":
        if not isinstance(value, dict):
            raise GatewayKeyRotationApplicationConflict(
                "rotation command receipt is malformed"
            )
        try:
            return cls(
                rotation=GatewayKeyRotationPublicView.from_descriptor(value["rotation"]),
                phase=str(value["phase"]),
                outcome=str(value["outcome"]),
                replayed=replayed,
                failure_code=(
                    None
                    if value.get("failure_code") is None
                    else str(value["failure_code"])
                ),
            )
        except (KeyError, TypeError, ValueError) as error:
            raise GatewayKeyRotationApplicationConflict(
                "rotation command receipt is malformed"
            ) from error


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


class GatewayKeyRotationPhaseExecutor(Protocol):
    """Execute exactly one operations-owned bounded phase."""

    def advance(
        self,
        rotation: GatewayKeyRotation,
        *,
        expected_version: int,
        actor_id: str,
        actor_scopes: tuple[PolicyScope, ...],
        idempotency_key: str,
    ) -> GatewayKeyRotationProgramView: ...


class GatewayKeyRotationApplicationService:
    """Trusted public facade over durable rotation programs and read truth."""

    def __init__(
        self,
        unit_of_work_factory: Callable[[], object],
        *,
        clock: Callable[[], str],
        trusted_epoch_clock: Callable[[], int],
        id_factory: Callable[[], str],
        phase_executor: GatewayKeyRotationPhaseExecutor,
    ) -> None:
        self._rotations = GatewayKeyRotationService(
            unit_of_work_factory,
            clock=trusted_epoch_clock,
        )
        self._approvals = ApprovalCommandService(
            unit_of_work_factory,
            clock=clock,
            id_factory=id_factory,
        )
        self._phase_executor = phase_executor

    def request(
        self,
        command: RequestGatewayKeyRotationProgram,
        context: TrustedCommandContext,
    ) -> GatewayKeyRotationPublicView:
        self._authorize(command.workspace_id, context, PolicyScope.DELEGATION_KEY_ROTATE)
        try:
            rotation = self._rotations.request(
                RequestGatewayKeyRotation(
                    workspace_id=command.workspace_id,
                    gateway_node_id=command.gateway_node_id,
                    purpose=command.purpose,
                    issuer=command.issuer,
                    old_key_id=command.old_key_id,
                    new_secret_reference=command.new_secret_reference,
                    key_generation_correlation=command.key_generation_correlation,
                    maximum_grant_lifetime_seconds=(
                        command.maximum_grant_lifetime_seconds
                    ),
                    clock_skew_seconds=command.clock_skew_seconds,
                    correlation_id=command.idempotency_key,
                    requested_by=context.actor_id,
                    requested_at=command.requested_at,
                    actor_scopes=(PolicyScope.DELEGATION_KEY_ROTATE,),
                )
            )
        except (GatewayKeyRotationAuthorizationDenied, GatewayKeyRotationConflict) as error:
            raise GatewayKeyRotationApplicationConflict(str(error)) from error
        return self._view(rotation)

    def request_approval(
        self,
        command: RequestGatewayKeyRotationProgramApproval,
        context: TrustedCommandContext,
    ) -> GatewayKeyRotationApprovalView:
        self._authorize(command.workspace_id, context, PolicyScope.DELEGATION_KEY_ROTATE)
        self._rotation(command.workspace_id, command.rotation_id)
        try:
            result = self._approvals.request_gateway_key_rotation(
                RequestGatewayKeyRotationApproval(
                    session_id=command.session_id,
                    rotation_id=command.rotation_id,
                    actor_id=context.actor_id,
                    actor_scopes=(PolicyScope.DELEGATION_KEY_ROTATE,),
                    idempotency_key=IdempotencyKey(command.idempotency_key),
                    comment=command.comment,
                )
            )
        except (ApprovalAuthorizationDenied, ApprovalIdempotencyConflict,
                ApprovalStateConflict, ApprovalTargetNotFound) as error:
            raise GatewayKeyRotationApplicationConflict(str(error)) from error
        approval = result.approval
        return GatewayKeyRotationApprovalView(
            self._view(result.rotation),
            approval.request.request_id,
            None,
            None,
            approval.replayed,
        )

    def decide(
        self,
        command: DecideGatewayKeyRotationProgram,
        context: TrustedCommandContext,
    ) -> GatewayKeyRotationApprovalView:
        self._authorize(
            command.workspace_id,
            context,
            PolicyScope.DELEGATION_KEY_ROTATE_APPROVE,
        )
        self._rotation(command.workspace_id, command.rotation_id)
        try:
            result = self._approvals.decide_gateway_key_rotation(
                DecideApproval(
                    session_id=command.session_id,
                    request_id=command.approval_request_id,
                    actor_id=context.actor_id,
                    actor_scopes=(PolicyScope.DELEGATION_KEY_ROTATE_APPROVE,),
                    decision=command.decision,
                    idempotency_key=IdempotencyKey(command.idempotency_key),
                    comment=command.comment,
                )
            )
        except (ApprovalAuthorizationDenied, ApprovalIdempotencyConflict,
                ApprovalStateConflict, ApprovalTargetNotFound) as error:
            raise GatewayKeyRotationApplicationConflict(str(error)) from error
        approval = result.approval
        if approval.request.subject.subject_id != command.rotation_id:
            raise GatewayKeyRotationApplicationConflict(
                "approval decision references a different rotation"
            )
        return GatewayKeyRotationApprovalView(
            self._view(result.rotation),
            approval.request.request_id,
            approval.decision.decision_id,
            approval.decision.decision,
            approval.replayed,
        )

    def advance(
        self,
        command: AdvanceGatewayKeyRotationProgram,
        context: TrustedCommandContext,
    ) -> GatewayKeyRotationProgramView:
        self._authorize(command.workspace_id, context, PolicyScope.DELEGATION_KEY_ROTATE)
        rotation = self._rotation(command.workspace_id, command.rotation_id)
        if rotation.version < command.expected_version:
            raise GatewayKeyRotationApplicationConflict(
                "rotation expected version is ahead of durable truth"
            )
        return self._phase_executor.advance(
            rotation,
            expected_version=command.expected_version,
            actor_id=context.actor_id,
            actor_scopes=context.granted_scopes,
            idempotency_key=command.idempotency_key,
        )

    def list(
        self,
        workspace_id: str,
        context: TrustedCommandContext,
    ) -> tuple[GatewayKeyRotationPublicView, ...]:
        self._authorize(workspace_id, context, PolicyScope.DELEGATION_KEY_READ)
        return tuple(
            GatewayKeyRotationPublicView.from_read_model(value)
            for value in self._rotations.list(workspace_id)
        )

    def detail(
        self,
        workspace_id: str,
        rotation_id: str,
        context: TrustedCommandContext,
    ) -> GatewayKeyRotationPublicView:
        self._authorize(workspace_id, context, PolicyScope.DELEGATION_KEY_READ)
        return self._view(self._rotation(workspace_id, rotation_id))

    def transitions(
        self,
        workspace_id: str,
        rotation_id: str,
        context: TrustedCommandContext,
    ) -> tuple[GatewayKeyRotationTransitionView, ...]:
        self._authorize(workspace_id, context, PolicyScope.DELEGATION_KEY_READ)
        self._rotation(workspace_id, rotation_id)
        return tuple(
            GatewayKeyRotationTransitionView.from_transition(value)
            for value in self._rotations.transitions(rotation_id)
        )

    def _rotation(self, workspace_id: str, rotation_id: str) -> GatewayKeyRotation:
        try:
            rotation = self._rotations.get(rotation_id)
        except GatewayKeyRotationNotFound as error:
            raise GatewayKeyRotationApplicationNotFound(
                "gateway key rotation was not found"
            ) from error
        if rotation.workspace_id != workspace_id:
            raise GatewayKeyRotationApplicationNotFound(
                "gateway key rotation was not found"
            )
        return rotation

    @staticmethod
    def _view(rotation: GatewayKeyRotation) -> GatewayKeyRotationPublicView:
        return GatewayKeyRotationPublicView.from_read_model(
            gateway_key_rotation_read_model(rotation)
        )

    @staticmethod
    def _authorize(
        workspace_id: str,
        context: TrustedCommandContext,
        required_scope: PolicyScope,
    ) -> None:
        if context.workspace_id != workspace_id:
            raise GatewayKeyRotationApplicationNotFound(
                "gateway key rotation workspace is not granted"
            )
        if required_scope not in context.granted_scopes:
            raise GatewayKeyRotationApplicationError(
                f"gateway key rotation requires {required_scope.value}"
            )


__all__ = [
    "AdvanceGatewayKeyRotationProgram",
    "DecideGatewayKeyRotationProgram",
    "GatewayKeyRotationApplication",
    "GatewayKeyRotationApplicationService",
    "GatewayKeyRotationApplicationConflict",
    "GatewayKeyRotationApplicationError",
    "GatewayKeyRotationApplicationNotFound",
    "GatewayKeyRotationApprovalView",
    "GatewayKeyRotationProgramView",
    "GatewayKeyRotationPhaseExecutor",
    "GatewayKeyRotationPublicView",
    "GatewayKeyRotationTransitionView",
    "RequestGatewayKeyRotationProgram",
    "RequestGatewayKeyRotationProgramApproval",
]
