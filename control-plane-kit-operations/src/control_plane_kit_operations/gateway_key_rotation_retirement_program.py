"""Prepare an approved A+B -> B retirement deployment without runtime effects."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256
import re
from typing import Any, Callable

from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_operations.admission import (
    ExecutionAdmissionDenied,
    ExecutionAdmissionError,
    ExecutionAdmissionCommandService,
)
from control_plane_kit_operations.gateway_key_rotation_deployment_preparation import (
    prepare_gateway_key_rotation_child,
)
from control_plane_kit_operations.gateway_key_rotation_retirement import (
    GatewayKeyRotationRetirementProjectionAuthorizationDenied,
    GatewayKeyRotationRetirementProjectionError,
    GatewayKeyRotationRetirementProjectionService,
    PublishGatewayKeyRotationRetirementProjection,
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
    ExecutionWorkerAuthority,
    RunLifecycleCommandService,
    RunLifecycleDenied,
    RunLifecycleError,
)
from control_plane_kit_operations.planning import (
    ActivityPlanningCommandService,
    ActivityPlanningError,
)
from control_plane_kit_operations.workflows import (
    IdempotencyKey,
    InvalidOperationCommand,
    OperationCommandError,
    OperationCommandService,
)


_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$")
_REQUIRED_SCOPES = frozenset(
    {PolicyScope.DELEGATION_KEY_ROTATE, PolicyScope.PLAN_EXECUTE}
)


class GatewayKeyRotationRetirementPreparationError(ValueError):
    """Base bounded failure for retirement child preparation."""


class GatewayKeyRotationRetirementPreparationConflict(
    GatewayKeyRotationRetirementPreparationError
):
    """Raised when rotation, graph, deadline, approval, or child truth diverges."""


class GatewayKeyRotationRetirementPreparationAuthorizationDenied(
    GatewayKeyRotationRetirementPreparationError
):
    """Raised before mutation when fixed preparation authority is absent."""


class GatewayKeyRotationRetirementPreparationOutcome(StrEnum):
    PREPARED = "prepared"
    PREPARED_REPLAY = "prepared-replay"
    ALREADY_ADVANCED = "already-advanced"


@dataclass(frozen=True)
class PrepareGatewayKeyRotationRetirement:
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
            raise InvalidOperationCommand("expected_rotation_version must be positive")
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
                "retirement preparation requires one settled realized lineage"
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
class GatewayKeyRotationRetirementPreparationResult:
    rotation: GatewayKeyRotation
    outcome: GatewayKeyRotationRetirementPreparationOutcome
    checkpoint: GatewayKeyRotationDeploymentCheckpoint

    def __post_init__(self) -> None:
        if not isinstance(self.rotation, GatewayKeyRotation):
            raise GatewayKeyRotationRetirementPreparationError(
                "preparation result rotation is malformed"
            )
        if not isinstance(
            self.outcome,
            GatewayKeyRotationRetirementPreparationOutcome,
        ):
            raise GatewayKeyRotationRetirementPreparationError(
                "preparation outcome is unsupported"
            )
        if not isinstance(
            self.checkpoint,
            GatewayKeyRotationDeploymentCheckpoint,
        ):
            raise GatewayKeyRotationRetirementPreparationError(
                "preparation checkpoint is malformed"
            )


class GatewayKeyRotationRetirementPreparationProgram:
    """Converge drained overlap to one started B-only ordinary child run."""

    def __init__(
        self,
        unit_of_work_factory: Callable[[], Any],
        *,
        clock: Callable[[], str],
        trusted_epoch_clock: Callable[[], int],
        id_factory: Callable[[], str],
    ) -> None:
        self._trusted_epoch_clock = trusted_epoch_clock
        self._operations = OperationCommandService(
            unit_of_work_factory, clock=clock, id_factory=id_factory
        )
        self._projections = GatewayKeyRotationRetirementProjectionService(
            unit_of_work_factory,
            clock=clock,
            trusted_epoch_clock=trusted_epoch_clock,
            action_id_factory=id_factory,
        )
        self._planning = ActivityPlanningCommandService(
            unit_of_work_factory, clock=clock, id_factory=id_factory
        )
        self._admission = ExecutionAdmissionCommandService(
            unit_of_work_factory, clock=clock, id_factory=id_factory
        )
        self._lifecycle = RunLifecycleCommandService(
            unit_of_work_factory, clock=clock, id_factory=id_factory
        )
        self._rotations = GatewayKeyRotationService(
            unit_of_work_factory, clock=trusted_epoch_clock
        )

    def prepare(
        self,
        command: PrepareGatewayKeyRotationRetirement,
    ) -> GatewayKeyRotationRetirementPreparationResult:
        if not isinstance(command, PrepareGatewayKeyRotationRetirement):
            raise TypeError("command must be PrepareGatewayKeyRotationRetirement")
        self._require_authority(command)
        rotation = self._rotation(command.rotation_id)
        existing = self._classify_existing(rotation, command)
        if existing is not None:
            return existing
        if (
            rotation.status is not GatewayKeyRotationStatus.DRAINING_OLD_GRANTS
            or rotation.version != command.expected_rotation_version
            or rotation.approval_request_id is None
            or rotation.approval_decision_id is None
            or rotation.drain_deadline_epoch is None
        ):
            raise GatewayKeyRotationRetirementPreparationConflict(
                "rotation is not the expected draining truth"
            )
        now = self._trusted_epoch_clock()
        if type(now) is not int or now < rotation.drain_deadline_epoch:
            raise GatewayKeyRotationRetirementPreparationConflict(
                "old capability grants have not drained"
            )
        prefix = "gkrot-retirement:" + sha256(
            rotation.rotation_id.encode("utf-8")
        ).hexdigest()
        try:
            child = prepare_gateway_key_rotation_child(
                rotation=rotation,
                command=command,
                phase=GatewayKeyRotationDeploymentPhase.RETIREMENT,
                prefix=prefix,
                operations=self._operations,
                projections=self._projections,
                projection_command=lambda session_id: (
                    PublishGatewayKeyRotationRetirementProjection(
                        rotation_id=rotation.rotation_id,
                        session_id=session_id,
                        actor_id=command.actor_id,
                        expected_rotation_version=command.expected_rotation_version,
                        expected_authored_graph_id=command.expected_authored_graph_id,
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
                ),
                planning=self._planning,
                admission=self._admission,
                lifecycle=self._lifecycle,
            )
            checkpoint = child.checkpoint
            prepared = self._rotations.advance(
                AdvanceGatewayKeyRotation(
                    rotation_id=rotation.rotation_id,
                    transition_id=f"{prefix}:prepared",
                    expected_status=GatewayKeyRotationStatus.DRAINING_OLD_GRANTS,
                    expected_version=rotation.version,
                    target_status=GatewayKeyRotationStatus.RETIREMENT_DEPLOYING,
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
            GatewayKeyRotationRetirementProjectionError,
            OperationCommandError,
            RunLifecycleError,
            ValueError,
        ) as error:
            if isinstance(
                error,
                (
                    ExecutionAdmissionDenied,
                    GatewayKeyRotationRetirementProjectionAuthorizationDenied,
                    RunLifecycleDenied,
                ),
            ):
                raise GatewayKeyRotationRetirementPreparationAuthorizationDenied(
                    str(error)
                ) from error
            raise GatewayKeyRotationRetirementPreparationConflict(str(error)) from error
        return GatewayKeyRotationRetirementPreparationResult(
            prepared,
            GatewayKeyRotationRetirementPreparationOutcome.PREPARED,
            checkpoint,
        )

    def _rotation(self, rotation_id: str) -> GatewayKeyRotation:
        try:
            return self._rotations.get(rotation_id)
        except GatewayKeyRotationError as error:
            raise GatewayKeyRotationRetirementPreparationConflict(str(error)) from error

    def _classify_existing(
        self,
        rotation: GatewayKeyRotation,
        command: PrepareGatewayKeyRotationRetirement,
    ) -> GatewayKeyRotationRetirementPreparationResult | None:
        if rotation.status is GatewayKeyRotationStatus.DRAINING_OLD_GRANTS:
            return None
        checkpoint = rotation.retirement_deployment
        if rotation.status is GatewayKeyRotationStatus.RETIREMENT_DEPLOYING:
            checkpoint = self._require_checkpoint(
                rotation,
                command,
                checkpoint,
                expected_status=GatewayKeyRotationDeploymentStatus.PREPARED,
                expected_version=command.expected_rotation_version + 1,
            )
            return GatewayKeyRotationRetirementPreparationResult(
                rotation,
                GatewayKeyRotationRetirementPreparationOutcome.PREPARED_REPLAY,
                checkpoint,
            )
        if (
            rotation.status is not GatewayKeyRotationStatus.COMPLETED
            or rotation.version <= command.expected_rotation_version
        ):
            raise GatewayKeyRotationRetirementPreparationConflict(
                "rotation cannot be prepared from its current state"
            )
        checkpoint = self._require_checkpoint(
            rotation,
            command,
            checkpoint,
            expected_status=None,
            expected_version=None,
        )
        return GatewayKeyRotationRetirementPreparationResult(
            rotation,
            GatewayKeyRotationRetirementPreparationOutcome.ALREADY_ADVANCED,
            checkpoint,
        )

    @staticmethod
    def _require_authority(command: PrepareGatewayKeyRotationRetirement) -> None:
        missing = _REQUIRED_SCOPES - set(command.actor_scopes)
        if missing:
            raise GatewayKeyRotationRetirementPreparationAuthorizationDenied(
                "retirement preparation scopes are missing: "
                + ", ".join(sorted(scope.value for scope in missing))
            )
        if PolicyScope.EXECUTION_OPERATE not in command.worker_authority.scopes:
            raise GatewayKeyRotationRetirementPreparationAuthorizationDenied(
                "retirement worker requires execution:operate"
            )

    @staticmethod
    def _require_checkpoint(
        rotation: GatewayKeyRotation,
        command: PrepareGatewayKeyRotationRetirement,
        checkpoint: GatewayKeyRotationDeploymentCheckpoint | None,
        *,
        expected_status: GatewayKeyRotationDeploymentStatus | None,
        expected_version: int | None,
    ) -> GatewayKeyRotationDeploymentCheckpoint:
        if checkpoint is None:
            raise GatewayKeyRotationRetirementPreparationConflict(
                "rotation retirement checkpoint is missing"
            )
        if expected_status is not None and checkpoint.status is not expected_status:
            raise GatewayKeyRotationRetirementPreparationConflict(
                "rotation retirement checkpoint status changed"
            )
        if expected_version is not None and rotation.version != expected_version:
            raise GatewayKeyRotationRetirementPreparationConflict(
                "rotation retirement checkpoint version changed"
            )
        if (
            checkpoint.phase is not GatewayKeyRotationDeploymentPhase.RETIREMENT
            or checkpoint.approval_request_id != rotation.approval_request_id
            or checkpoint.approval_decision_id != rotation.approval_decision_id
            or checkpoint.base_authored_graph_id != command.expected_authored_graph_id
            or checkpoint.desired_authored_graph_id
            != command.expected_authored_graph_id
            or checkpoint.base_realized_projection_id
            != command.expected_current_realized_projection_id
            or command.expected_current_realized_projection_id
            != command.expected_desired_realized_projection_id
            or checkpoint.desired_realized_projection_id
            != f"gateway-rotation-{rotation.rotation_id}-retirement"
            or checkpoint.desired_revision
            != command.expected_desired_graph_revision + 1
        ):
            raise GatewayKeyRotationRetirementPreparationConflict(
                "rotation retirement checkpoint does not match expected lineage"
            )
        return checkpoint


def _identifier(value: object, name: str) -> None:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise InvalidOperationCommand(f"{name} is malformed")


def _text(value: object, name: str) -> None:
    if not isinstance(value, str) or not value.strip() or len(value) > 256:
        raise InvalidOperationCommand(f"{name} is malformed")


__all__ = [
    "GatewayKeyRotationRetirementPreparationAuthorizationDenied",
    "GatewayKeyRotationRetirementPreparationConflict",
    "GatewayKeyRotationRetirementPreparationError",
    "GatewayKeyRotationRetirementPreparationOutcome",
    "GatewayKeyRotationRetirementPreparationProgram",
    "GatewayKeyRotationRetirementPreparationResult",
    "PrepareGatewayKeyRotationRetirement",
]
