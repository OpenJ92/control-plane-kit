"""Prepare an approved A -> A+B rotation deployment without runtime effects."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256
import re
from typing import Any, Callable

from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_operations.admission import (
    ExecutionAdmissionCommandService,
    ExecutionAdmissionDenied,
    ExecutionAdmissionError,
    RequestPlanExecution,
)
from control_plane_kit_operations.gateway_key_rotation_overlap import (
    GatewayKeyRotationOverlapProjectionAuthorizationDenied,
    GatewayKeyRotationOverlapProjectionError,
    GatewayKeyRotationOverlapProjectionService,
    PublishGatewayKeyRotationOverlapProjection,
)
from control_plane_kit_operations.gateway_key_rotations import (
    AdvanceGatewayKeyRotation,
    GatewayKeyRotation,
    GatewayKeyRotationDeploymentCheckpoint,
    GatewayKeyRotationDeploymentPhase,
    GatewayKeyRotationDeploymentStatus,
    GatewayKeyRotationError,
    GatewayKeyRotationService,
    GatewayKeyRotationStatus,
)
from control_plane_kit_operations.lifecycle import (
    ClaimAndOpenActivityRun,
    ExecutionWorkerAuthority,
    RunLifecycleCommandService,
    RunLifecycleDenied,
    RunLifecycleError,
    StartActivityRun,
)
from control_plane_kit_operations.planning import (
    ActivityPlanningCommandService,
    ActivityPlanningError,
    RequestActivityPlan,
)
from control_plane_kit_operations.workflows import (
    IdempotencyKey,
    InvalidOperationCommand,
    OperationCommandError,
    OperationCommandService,
    StartOperationSession,
)


_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$")
_REQUIRED_SCOPES = frozenset(
    {
        PolicyScope.DELEGATION_KEY_ROTATE,
        PolicyScope.PLAN_EXECUTE,
    }
)
_POST_OVERLAP_STATUSES = frozenset(
    {
        GatewayKeyRotationStatus.OVERLAP_READY,
        GatewayKeyRotationStatus.NEW_KEY_ACTIVE,
        GatewayKeyRotationStatus.DRAINING_OLD_GRANTS,
        GatewayKeyRotationStatus.RETIREMENT_DEPLOYING,
        GatewayKeyRotationStatus.COMPLETED,
    }
)


class GatewayKeyRotationOverlapPreparationError(ValueError):
    """Base bounded failure for overlap child-workflow preparation."""


class GatewayKeyRotationOverlapPreparationConflict(
    GatewayKeyRotationOverlapPreparationError
):
    """Raised when durable rotation, graph, approval, or child truth diverges."""


class GatewayKeyRotationOverlapPreparationAuthorizationDenied(
    GatewayKeyRotationOverlapPreparationError
):
    """Raised before mutation when fixed preparation authority is absent."""


class GatewayKeyRotationOverlapPreparationOutcome(StrEnum):
    PREPARED = "prepared"
    PREPARED_REPLAY = "prepared-replay"
    ALREADY_ADVANCED = "already-advanced"


@dataclass(frozen=True)
class PrepareGatewayKeyRotationOverlap:
    """Expected settled A lineage plus authority for one overlap preparation."""

    rotation_id: str
    expected_rotation_version: int
    expected_authored_graph_id: str
    expected_current_realized_projection_id: str
    expected_desired_realized_projection_id: str
    expected_desired_graph_revision: int
    actor_id: str
    actor_scopes: tuple[PolicyScope, ...]
    worker_authority: ExecutionWorkerAuthority
    lease_expires_at: str

    def __post_init__(self) -> None:
        for value, name in (
            (self.rotation_id, "rotation_id"),
            (self.expected_authored_graph_id, "expected_authored_graph_id"),
            (
                self.expected_current_realized_projection_id,
                "expected_current_realized_projection_id",
            ),
            (
                self.expected_desired_realized_projection_id,
                "expected_desired_realized_projection_id",
            ),
            (self.actor_id, "actor_id"),
        ):
            _identifier(value, name)
        if (
            type(self.expected_rotation_version) is not int
            or self.expected_rotation_version < 1
        ):
            raise InvalidOperationCommand(
                "expected_rotation_version must be positive"
            )
        if (
            type(self.expected_desired_graph_revision) is not int
            or self.expected_desired_graph_revision < 0
        ):
            raise InvalidOperationCommand(
                "expected_desired_graph_revision must be nonnegative"
            )
        if (
            self.expected_current_realized_projection_id
            != self.expected_desired_realized_projection_id
        ):
            raise InvalidOperationCommand(
                "overlap preparation requires one settled realized lineage"
            )
        if not isinstance(self.actor_scopes, tuple) or not all(
            isinstance(scope, PolicyScope) for scope in self.actor_scopes
        ):
            raise InvalidOperationCommand("actor_scopes must be a typed tuple")
        object.__setattr__(
            self,
            "actor_scopes",
            tuple(sorted(set(self.actor_scopes), key=lambda scope: scope.value)),
        )
        if not isinstance(self.worker_authority, ExecutionWorkerAuthority):
            raise InvalidOperationCommand(
                "worker_authority must be ExecutionWorkerAuthority"
            )
        _text(self.lease_expires_at, "lease_expires_at")


@dataclass(frozen=True)
class GatewayKeyRotationOverlapPreparationResult:
    rotation: GatewayKeyRotation
    outcome: GatewayKeyRotationOverlapPreparationOutcome
    checkpoint: GatewayKeyRotationDeploymentCheckpoint

    def __post_init__(self) -> None:
        if not isinstance(self.rotation, GatewayKeyRotation):
            raise GatewayKeyRotationOverlapPreparationError(
                "preparation result rotation is malformed"
            )
        if not isinstance(
            self.outcome,
            GatewayKeyRotationOverlapPreparationOutcome,
        ):
            raise GatewayKeyRotationOverlapPreparationError(
                "preparation outcome is unsupported"
            )
        if not isinstance(
            self.checkpoint,
            GatewayKeyRotationDeploymentCheckpoint,
        ):
            raise GatewayKeyRotationOverlapPreparationError(
                "preparation checkpoint is malformed"
            )


class GatewayKeyRotationOverlapPreparationProgram:
    """Compose canonical transactions through a started run and checkpoint."""

    def __init__(
        self,
        unit_of_work_factory: Callable[[], Any],
        *,
        clock: Callable[[], str],
        trusted_epoch_clock: Callable[[], int],
        id_factory: Callable[[], str],
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._operations = OperationCommandService(
            unit_of_work_factory,
            clock=clock,
            id_factory=id_factory,
        )
        self._projections = GatewayKeyRotationOverlapProjectionService(
            unit_of_work_factory,
            clock=clock,
            action_id_factory=id_factory,
        )
        self._planning = ActivityPlanningCommandService(
            unit_of_work_factory,
            clock=clock,
            id_factory=id_factory,
        )
        self._admission = ExecutionAdmissionCommandService(
            unit_of_work_factory,
            clock=clock,
            id_factory=id_factory,
        )
        self._lifecycle = RunLifecycleCommandService(
            unit_of_work_factory,
            clock=clock,
            id_factory=id_factory,
        )
        self._rotations = GatewayKeyRotationService(
            unit_of_work_factory,
            clock=trusted_epoch_clock,
        )

    def prepare(
        self,
        command: PrepareGatewayKeyRotationOverlap,
    ) -> GatewayKeyRotationOverlapPreparationResult:
        if not isinstance(command, PrepareGatewayKeyRotationOverlap):
            raise TypeError("command must be PrepareGatewayKeyRotationOverlap")
        self._require_fixed_authority(command)
        rotation = self._rotation(command.rotation_id)
        prior = self._classify_existing(rotation, command)
        if prior is not None:
            return prior
        if (
            rotation.status is not GatewayKeyRotationStatus.KEY_GENERATED
            or rotation.version != command.expected_rotation_version
            or rotation.approval_request_id is None
            or rotation.approval_decision_id is None
        ):
            raise GatewayKeyRotationOverlapPreparationConflict(
                "rotation is not the expected key-generated truth"
            )
        prefix = "gkrot-overlap:" + sha256(
            rotation.rotation_id.encode("utf-8")
        ).hexdigest()
        try:
            session = self._operations.execute(
                StartOperationSession(
                    workspace_id=rotation.workspace_id,
                    actor_id=command.actor_id,
                    title="Deploy gateway key rotation overlap",
                    idempotency_key=IdempotencyKey(f"{prefix}:session"),
                    metadata={
                        "rotation_id": rotation.rotation_id,
                        "deployment_phase": "overlap",
                    },
                )
            )
            publication = self._projections.execute(
                PublishGatewayKeyRotationOverlapProjection(
                    rotation_id=rotation.rotation_id,
                    session_id=session.session.session_id,
                    actor_id=command.actor_id,
                    expected_rotation_version=command.expected_rotation_version,
                    expected_authored_graph_id=(
                        command.expected_authored_graph_id
                    ),
                    expected_current_realized_projection_id=(
                        command.expected_current_realized_projection_id
                    ),
                    expected_desired_realized_projection_id=(
                        command.expected_desired_realized_projection_id
                    ),
                    expected_desired_graph_revision=(
                        command.expected_desired_graph_revision
                    ),
                    actor_scopes=command.actor_scopes,
                    idempotency_key=IdempotencyKey(f"{prefix}:projection"),
                )
            ).publication
            plan = self._planning.execute(
                RequestActivityPlan(
                    session_id=session.session.session_id,
                    workspace_id=rotation.workspace_id,
                    actor_id=command.actor_id,
                    expected_current_graph_id=publication.authored_graph_id,
                    expected_desired_graph_id=publication.authored_graph_id,
                    expected_current_realized_projection_id=(
                        publication.previous_realized_projection_id
                    ),
                    expected_desired_realized_projection_id=(
                        publication.desired_realized_projection_id
                    ),
                    expected_desired_graph_revision=(
                        publication.desired_graph_revision
                    ),
                    idempotency_key=IdempotencyKey(f"{prefix}:plan"),
                )
            )
            admission = self._admission.execute(
                RequestPlanExecution(
                    workspace_id=rotation.workspace_id,
                    session_id=session.session.session_id,
                    plan_id=plan.plan_record.plan_id,
                    approval_request_id=rotation.approval_request_id,
                    actor_id=command.actor_id,
                    actor_scopes=command.actor_scopes,
                    idempotency_key=IdempotencyKey(f"{prefix}:admission"),
                )
            )
            claim = self._lifecycle.execute(
                ClaimAndOpenActivityRun(
                    request_id=admission.request.identity.request_id,
                    authority=command.worker_authority,
                    lease_expires_at=command.lease_expires_at,
                    idempotency_key=IdempotencyKey(f"{prefix}:claim"),
                )
            )
            started = self._lifecycle.execute(
                StartActivityRun(
                    run_id=claim.run.run_id,
                    authority=command.worker_authority,
                    idempotency_key=IdempotencyKey(f"{prefix}:start"),
                )
            )
            checkpoint = self._checkpoint(
                session_id=session.session.session_id,
                plan=plan.plan_record,
                admission=admission.request,
                run_id=started.run.run_id,
                prepared_at=started.event.occurred_at,
            )
            prepared = self._rotations.advance(
                AdvanceGatewayKeyRotation(
                    rotation_id=rotation.rotation_id,
                    transition_id=f"{prefix}:prepared",
                    expected_status=GatewayKeyRotationStatus.KEY_GENERATED,
                    expected_version=rotation.version,
                    target_status=GatewayKeyRotationStatus.OVERLAP_DEPLOYING,
                    advanced_by=command.actor_id,
                    advanced_at=checkpoint.prepared_at,
                    actor_scopes=command.actor_scopes,
                    deployment=checkpoint,
                )
            )
        except (
            ActivityPlanningError,
            ExecutionAdmissionError,
            GatewayKeyRotationError,
            GatewayKeyRotationOverlapProjectionError,
            OperationCommandError,
            RunLifecycleError,
        ) as error:
            if isinstance(
                error,
                (
                    ExecutionAdmissionDenied,
                    GatewayKeyRotationOverlapProjectionAuthorizationDenied,
                    RunLifecycleDenied,
                ),
            ):
                raise GatewayKeyRotationOverlapPreparationAuthorizationDenied(
                    str(error)
                ) from error
            raise GatewayKeyRotationOverlapPreparationConflict(str(error)) from error
        return GatewayKeyRotationOverlapPreparationResult(
            rotation=prepared,
            outcome=GatewayKeyRotationOverlapPreparationOutcome.PREPARED,
            checkpoint=checkpoint,
        )

    def _rotation(self, rotation_id: str) -> GatewayKeyRotation:
        try:
            return self._rotations.get(rotation_id)
        except GatewayKeyRotationError as error:
            raise GatewayKeyRotationOverlapPreparationConflict(str(error)) from error

    def _classify_existing(
        self,
        rotation: GatewayKeyRotation,
        command: PrepareGatewayKeyRotationOverlap,
    ) -> GatewayKeyRotationOverlapPreparationResult | None:
        if rotation.status is GatewayKeyRotationStatus.KEY_GENERATED:
            return None
        checkpoint = rotation.overlap_deployment
        if rotation.status is GatewayKeyRotationStatus.OVERLAP_DEPLOYING:
            checkpoint = self._require_checkpoint(
                rotation,
                command,
                checkpoint,
                expected_status=GatewayKeyRotationDeploymentStatus.PREPARED,
                expected_version=command.expected_rotation_version + 1,
            )
            return GatewayKeyRotationOverlapPreparationResult(
                rotation=rotation,
                outcome=(
                    GatewayKeyRotationOverlapPreparationOutcome.PREPARED_REPLAY
                ),
                checkpoint=checkpoint,
            )
        later = rotation.status in _POST_OVERLAP_STATUSES or (
            rotation.status is GatewayKeyRotationStatus.BLOCKED
            and checkpoint is not None
        )
        if not later or rotation.version <= command.expected_rotation_version:
            raise GatewayKeyRotationOverlapPreparationConflict(
                "rotation cannot be prepared from its current state"
            )
        checkpoint = self._require_checkpoint(
            rotation,
            command,
            checkpoint,
            expected_status=None,
            expected_version=None,
        )
        return GatewayKeyRotationOverlapPreparationResult(
            rotation=rotation,
            outcome=GatewayKeyRotationOverlapPreparationOutcome.ALREADY_ADVANCED,
            checkpoint=checkpoint,
        )

    @staticmethod
    def _checkpoint(
        *,
        session_id: str,
        plan: Any,
        admission: Any,
        run_id: str,
        prepared_at: str,
    ) -> GatewayKeyRotationDeploymentCheckpoint:
        approval_decision_id = admission.approval_decision_id
        if not isinstance(approval_decision_id, str):
            raise GatewayKeyRotationOverlapPreparationConflict(
                "admitted child is missing approval decision evidence"
            )
        return GatewayKeyRotationDeploymentCheckpoint(
            phase=GatewayKeyRotationDeploymentPhase.OVERLAP,
            status=GatewayKeyRotationDeploymentStatus.PREPARED,
            session_id=session_id,
            plan_id=plan.plan_id,
            approval_request_id=admission.approval_request_id,
            approval_decision_id=approval_decision_id,
            execution_request_id=admission.identity.request_id,
            run_id=run_id,
            base_authored_graph_id=plan.base_graph_id,
            base_realized_projection_id=plan.base_realized_projection_id,
            desired_authored_graph_id=plan.desired_graph_id,
            desired_realized_projection_id=plan.desired_realized_projection_id,
            desired_revision=plan.desired_graph_revision,
            prepared_at=prepared_at,
        )

    @staticmethod
    def _require_fixed_authority(
        command: PrepareGatewayKeyRotationOverlap,
    ) -> None:
        missing = _REQUIRED_SCOPES - set(command.actor_scopes)
        if missing:
            raise GatewayKeyRotationOverlapPreparationAuthorizationDenied(
                "overlap preparation scopes are missing: "
                + ", ".join(sorted(scope.value for scope in missing))
            )
        if PolicyScope.EXECUTION_OPERATE not in command.worker_authority.scopes:
            raise GatewayKeyRotationOverlapPreparationAuthorizationDenied(
                "overlap worker requires execution:operate"
            )

    @staticmethod
    def _require_checkpoint(
        rotation: GatewayKeyRotation,
        command: PrepareGatewayKeyRotationOverlap,
        checkpoint: GatewayKeyRotationDeploymentCheckpoint | None,
        *,
        expected_status: GatewayKeyRotationDeploymentStatus | None,
        expected_version: int | None,
    ) -> GatewayKeyRotationDeploymentCheckpoint:
        if checkpoint is None:
            raise GatewayKeyRotationOverlapPreparationConflict(
                "rotation overlap checkpoint is missing"
            )
        if expected_status is not None and checkpoint.status is not expected_status:
            raise GatewayKeyRotationOverlapPreparationConflict(
                "rotation overlap checkpoint status changed"
            )
        if expected_version is not None and rotation.version != expected_version:
            raise GatewayKeyRotationOverlapPreparationConflict(
                "rotation overlap checkpoint version changed"
            )
        if (
            checkpoint.phase is not GatewayKeyRotationDeploymentPhase.OVERLAP
            or checkpoint.approval_request_id != rotation.approval_request_id
            or checkpoint.approval_decision_id != rotation.approval_decision_id
            or checkpoint.base_authored_graph_id
            != command.expected_authored_graph_id
            or checkpoint.desired_authored_graph_id
            != command.expected_authored_graph_id
            or checkpoint.base_realized_projection_id
            != command.expected_current_realized_projection_id
            or command.expected_current_realized_projection_id
            != command.expected_desired_realized_projection_id
            or checkpoint.desired_realized_projection_id
            != f"gateway-rotation-{rotation.rotation_id}-overlap"
            or checkpoint.desired_revision
            != command.expected_desired_graph_revision + 1
        ):
            raise GatewayKeyRotationOverlapPreparationConflict(
                "rotation overlap checkpoint does not match expected lineage"
            )
        return checkpoint


def _identifier(value: object, name: str) -> None:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise InvalidOperationCommand(f"{name} is malformed")


def _text(value: object, name: str) -> None:
    if not isinstance(value, str) or not value.strip() or len(value) > 256:
        raise InvalidOperationCommand(f"{name} is malformed")


__all__ = [
    "GatewayKeyRotationOverlapPreparationAuthorizationDenied",
    "GatewayKeyRotationOverlapPreparationConflict",
    "GatewayKeyRotationOverlapPreparationError",
    "GatewayKeyRotationOverlapPreparationOutcome",
    "GatewayKeyRotationOverlapPreparationProgram",
    "GatewayKeyRotationOverlapPreparationResult",
    "PrepareGatewayKeyRotationOverlap",
]
