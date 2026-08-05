"""Phase-typed dispatch, reconciliation, and acceptance for key rotation."""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum
from hashlib import sha256
import re
from typing import Any, Callable

from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_operations.advancement import (
    AdvanceCurrentGraph,
    CurrentGraphAdvancementCommandService,
    CurrentGraphAdvancementError,
    CurrentGraphAdvancementResult,
)
from control_plane_kit_operations.coordinator import (
    CoordinatorStatus,
    ExecuteActivityRun,
    ExecutionCoordinator,
    ExecutionCoordinatorError,
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
from control_plane_kit_operations.lifecycle import ExecutionWorkerAuthority
from control_plane_kit_operations.records import (
    ActivityPlanRecord,
    ActivityRunRecord,
    ExecutionRequestRecord,
    WorkspaceRecord,
)
from control_plane_kit_operations.workflows import (
    IdempotencyKey,
    InvalidOperationCommand,
)


_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$")
class GatewayKeyRotationDeploymentExecutionError(ValueError):
    """Base bounded failure for post-checkpoint deployment execution."""


class GatewayKeyRotationDeploymentExecutionConflict(
    GatewayKeyRotationDeploymentExecutionError
):
    """Raised when checkpoint, run, graph, or rotation truth diverges."""


class GatewayKeyRotationDeploymentExecutionAuthorizationDenied(
    GatewayKeyRotationDeploymentExecutionError
):
    """Raised before progress when rotation or worker authority is absent."""


class GatewayKeyRotationDeploymentExecutionOutcome(StrEnum):
    DISPATCHED = "dispatched"
    PROGRESSED = "progressed"
    ACCEPTED = "accepted"
    ACCEPTED_REPLAY = "accepted-replay"
    ALREADY_ADVANCED = "already-advanced"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class ProgressGatewayKeyRotationDeployment:
    rotation_id: str
    phase: GatewayKeyRotationDeploymentPhase
    expected_prepared_rotation_version: int
    actor_id: str
    actor_scopes: tuple[PolicyScope, ...]
    worker_authority: ExecutionWorkerAuthority

    def __post_init__(self) -> None:
        _identifier(self.rotation_id, "rotation_id")
        _identifier(self.actor_id, "actor_id")
        if not isinstance(self.phase, GatewayKeyRotationDeploymentPhase):
            raise InvalidOperationCommand("deployment phase is unsupported")
        if (
            type(self.expected_prepared_rotation_version) is not int
            or self.expected_prepared_rotation_version < 1
        ):
            raise InvalidOperationCommand(
                "expected_prepared_rotation_version must be positive"
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


@dataclass(frozen=True)
class GatewayKeyRotationDeploymentExecutionResult:
    rotation: GatewayKeyRotation
    outcome: GatewayKeyRotationDeploymentExecutionOutcome
    checkpoint: GatewayKeyRotationDeploymentCheckpoint
    coordinator_status: CoordinatorStatus | None = None
    effects_attempted: int = 0
    advancement: CurrentGraphAdvancementResult | None = None
    failure_code: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.rotation, GatewayKeyRotation):
            raise GatewayKeyRotationDeploymentExecutionError(
                "execution result rotation is malformed"
            )
        if not isinstance(self.outcome, GatewayKeyRotationDeploymentExecutionOutcome):
            raise GatewayKeyRotationDeploymentExecutionError(
                "execution outcome is unsupported"
            )
        if not isinstance(self.checkpoint, GatewayKeyRotationDeploymentCheckpoint):
            raise GatewayKeyRotationDeploymentExecutionError(
                "execution checkpoint is malformed"
            )
        if self.coordinator_status is not None and not isinstance(
            self.coordinator_status, CoordinatorStatus
        ):
            raise GatewayKeyRotationDeploymentExecutionError(
                "coordinator status is malformed"
            )
        if type(self.effects_attempted) is not int or self.effects_attempted < 0:
            raise GatewayKeyRotationDeploymentExecutionError(
                "effects_attempted must be nonnegative"
            )
        accepted = self.outcome in {
            GatewayKeyRotationDeploymentExecutionOutcome.ACCEPTED,
            GatewayKeyRotationDeploymentExecutionOutcome.ACCEPTED_REPLAY,
            GatewayKeyRotationDeploymentExecutionOutcome.ALREADY_ADVANCED,
        }
        if accepted != (
            self.checkpoint.status
            is GatewayKeyRotationDeploymentStatus.ACCEPTED
        ):
            raise GatewayKeyRotationDeploymentExecutionError(
                "accepted outcome and checkpoint disagree"
            )
        blocked = self.outcome is GatewayKeyRotationDeploymentExecutionOutcome.BLOCKED
        if blocked != (self.failure_code is not None):
            raise GatewayKeyRotationDeploymentExecutionError(
                "blocked outcome requires one bounded failure code"
            )


@dataclass(frozen=True)
class _DeploymentSnapshot:
    workspace: WorkspaceRecord
    checkpoint: GatewayKeyRotationDeploymentCheckpoint
    plan: ActivityPlanRecord
    request: ExecutionRequestRecord
    run: ActivityRunRecord


class GatewayKeyRotationDeploymentExecutionProgram:
    """Advance one prepared rotation deployment by one recoverable invocation."""

    def __init__(
        self,
        unit_of_work_factory: Callable[[], Any],
        *,
        coordinator: ExecutionCoordinator,
        clock: Callable[[], str],
        trusted_epoch_clock: Callable[[], int],
        id_factory: Callable[[], str],
    ) -> None:
        if not isinstance(coordinator, ExecutionCoordinator):
            raise TypeError("coordinator must be ExecutionCoordinator")
        self._unit_of_work_factory = unit_of_work_factory
        self._coordinator = coordinator
        self._clock = clock
        self._advancement = CurrentGraphAdvancementCommandService(
            unit_of_work_factory,
            clock=clock,
            id_factory=id_factory,
        )
        self._rotations = GatewayKeyRotationService(
            unit_of_work_factory,
            clock=trusted_epoch_clock,
        )

    def progress(
        self,
        command: ProgressGatewayKeyRotationDeployment,
    ) -> GatewayKeyRotationDeploymentExecutionResult:
        if not isinstance(command, ProgressGatewayKeyRotationDeployment):
            raise TypeError("command must be ProgressGatewayKeyRotationDeployment")
        self._require_authority(command)
        rotation = self._rotation(command.rotation_id)
        prior = self._classify_existing(rotation, command)
        if prior is not None:
            return prior
        snapshot = self._snapshot(rotation, command)
        if self._current_is_desired(snapshot):
            return self._advance_and_accept(rotation, snapshot, command)
        try:
            coordinated = self._coordinator.execute(
                ExecuteActivityRun(
                    run_id=snapshot.checkpoint.run_id,
                    authority=command.worker_authority,
                    idempotency_key=IdempotencyKey(
                        f"{_prefix(rotation.rotation_id, command.phase)}:execute"
                    ),
                    max_effects=1,
                )
            )
        except ExecutionCoordinatorError as error:
            raise GatewayKeyRotationDeploymentExecutionConflict(str(error)) from error
        if coordinated.status is CoordinatorStatus.COMPLETED:
            return self._advance_and_accept(
                rotation,
                snapshot,
                command,
                effects_attempted=coordinated.effects_attempted,
            )
        if coordinated.status in {
            CoordinatorStatus.PROGRESSED,
            CoordinatorStatus.WAITING,
        }:
            outcome = (
                GatewayKeyRotationDeploymentExecutionOutcome.DISPATCHED
                if coordinated.effects_attempted
                else GatewayKeyRotationDeploymentExecutionOutcome.PROGRESSED
            )
            return GatewayKeyRotationDeploymentExecutionResult(
                rotation=rotation,
                outcome=outcome,
                checkpoint=snapshot.checkpoint,
                coordinator_status=coordinated.status,
                effects_attempted=coordinated.effects_attempted,
            )
        failure_code = _failure_code(coordinated.status, command.phase)
        blocked = self._block(rotation, command, failure_code)
        return GatewayKeyRotationDeploymentExecutionResult(
            rotation=blocked,
            outcome=GatewayKeyRotationDeploymentExecutionOutcome.BLOCKED,
            checkpoint=snapshot.checkpoint,
            coordinator_status=coordinated.status,
            effects_attempted=coordinated.effects_attempted,
            failure_code=failure_code,
        )

    def _advance_and_accept(
        self,
        rotation: GatewayKeyRotation,
        snapshot: _DeploymentSnapshot,
        command: ProgressGatewayKeyRotationDeployment,
        *,
        effects_attempted: int = 0,
    ) -> GatewayKeyRotationDeploymentExecutionResult:
        checkpoint = snapshot.checkpoint
        try:
            advancement = self._advancement.execute(
                AdvanceCurrentGraph(
                    workspace_id=rotation.workspace_id,
                    run_id=checkpoint.run_id,
                    plan_id=checkpoint.plan_id,
                    expected_current_graph_id=checkpoint.base_authored_graph_id,
                    expected_current_realized_projection_id=(
                        checkpoint.base_realized_projection_id
                    ),
                    desired_graph_id=checkpoint.desired_authored_graph_id,
                    desired_realized_projection_id=(
                        checkpoint.desired_realized_projection_id
                    ),
                    expected_desired_graph_revision=checkpoint.desired_revision,
                    authority=command.worker_authority,
                    idempotency_key=IdempotencyKey(
                        f"{_prefix(rotation.rotation_id, command.phase)}:advance"
                    ),
                )
            )
            accepted_checkpoint = replace(
                checkpoint,
                status=GatewayKeyRotationDeploymentStatus.ACCEPTED,
                accepted_current_graph_id=advancement.to_authored_graph_id,
                accepted_current_projection_id=(
                    advancement.to_realized_projection_id
                ),
                accepted_at=advancement.event.occurred_at,
            )
            accepted = self._rotations.advance(
                AdvanceGatewayKeyRotation(
                    rotation_id=rotation.rotation_id,
                    transition_id=(
                        f"{_prefix(rotation.rotation_id, command.phase)}:accepted"
                    ),
                    expected_status=_prepared_status(command.phase),
                    expected_version=rotation.version,
                    target_status=_accepted_status(command.phase),
                    advanced_by=command.actor_id,
                    advanced_at=advancement.event.occurred_at,
                    actor_scopes=command.actor_scopes,
                    deployment=accepted_checkpoint,
                )
            )
        except (CurrentGraphAdvancementError, GatewayKeyRotationError) as error:
            raise GatewayKeyRotationDeploymentExecutionConflict(str(error)) from error
        return GatewayKeyRotationDeploymentExecutionResult(
            rotation=accepted,
            outcome=GatewayKeyRotationDeploymentExecutionOutcome.ACCEPTED,
            checkpoint=accepted_checkpoint,
            coordinator_status=CoordinatorStatus.COMPLETED,
            effects_attempted=effects_attempted,
            advancement=advancement,
        )

    def _block(
        self,
        rotation: GatewayKeyRotation,
        command: ProgressGatewayKeyRotationDeployment,
        failure_code: str,
    ) -> GatewayKeyRotation:
        try:
            return self._rotations.advance(
                AdvanceGatewayKeyRotation(
                    rotation_id=rotation.rotation_id,
                    transition_id=(
                        f"{_prefix(rotation.rotation_id, command.phase)}:blocked:"
                        f"{failure_code}"
                    ),
                    expected_status=_prepared_status(command.phase),
                    expected_version=rotation.version,
                    target_status=GatewayKeyRotationStatus.BLOCKED,
                    advanced_by=command.actor_id,
                    advanced_at=self._clock(),
                    actor_scopes=command.actor_scopes,
                    failure_code=failure_code,
                )
            )
        except GatewayKeyRotationError as error:
            raise GatewayKeyRotationDeploymentExecutionConflict(str(error)) from error

    def _snapshot(
        self,
        rotation: GatewayKeyRotation,
        command: ProgressGatewayKeyRotationDeployment,
    ) -> _DeploymentSnapshot:
        checkpoint = _prepared_checkpoint(rotation, command)
        with self._unit_of_work_factory() as unit_of_work:
            stores = unit_of_work.stores
            try:
                workspace = stores.workspaces.get(rotation.workspace_id)
                plan = stores.activity_history.get_plan(checkpoint.plan_id)
                request = stores.execution.get_request(
                    checkpoint.execution_request_id
                )
                run = stores.execution.get_run(checkpoint.run_id)
            except KeyError as error:
                raise GatewayKeyRotationDeploymentExecutionConflict(
                    f"{command.phase.value} child truth is missing"
                ) from error
            unit_of_work.commit()
        claim = request.claim
        if (
            workspace.desired_graph_id != checkpoint.desired_authored_graph_id
            or workspace.desired_realized_projection_id
            != checkpoint.desired_realized_projection_id
            or workspace.desired_graph_revision != checkpoint.desired_revision
            or plan.plan_id != checkpoint.plan_id
            or plan.session_id != checkpoint.session_id
            or plan.base_graph_id != checkpoint.base_authored_graph_id
            or plan.base_realized_projection_id
            != checkpoint.base_realized_projection_id
            or plan.desired_graph_id != checkpoint.desired_authored_graph_id
            or plan.desired_realized_projection_id
            != checkpoint.desired_realized_projection_id
            or plan.desired_graph_revision != checkpoint.desired_revision
            or request.identity.request_id != checkpoint.execution_request_id
            or request.identity.workspace_id != rotation.workspace_id
            or request.identity.session_id != checkpoint.session_id
            or request.identity.plan_id != checkpoint.plan_id
            or request.approval_request_id != checkpoint.approval_request_id
            or request.approval_decision_id != checkpoint.approval_decision_id
            or run.run_id != checkpoint.run_id
            or run.plan_id != checkpoint.plan_id
            or run.admission.request_id != checkpoint.execution_request_id
            or claim is None
            or claim.worker_id != command.worker_authority.worker_id
            or not _current_is_base_or_desired(workspace, checkpoint)
        ):
            raise GatewayKeyRotationDeploymentExecutionConflict(
                f"{command.phase.value} checkpoint does not match durable child truth"
            )
        return _DeploymentSnapshot(workspace, checkpoint, plan, request, run)

    @staticmethod
    def _current_is_desired(snapshot: _DeploymentSnapshot) -> bool:
        return (
            snapshot.workspace.current_graph_id
            == snapshot.checkpoint.desired_authored_graph_id
            and snapshot.workspace.current_realized_projection_id
            == snapshot.checkpoint.desired_realized_projection_id
        )

    def _classify_existing(
        self,
        rotation: GatewayKeyRotation,
        command: ProgressGatewayKeyRotationDeployment,
    ) -> GatewayKeyRotationDeploymentExecutionResult | None:
        if rotation.status is _prepared_status(command.phase):
            return None
        checkpoint = _checkpoint_for(rotation, command.phase)
        if rotation.status is GatewayKeyRotationStatus.BLOCKED:
            if (
                checkpoint is None
                or checkpoint.status is not GatewayKeyRotationDeploymentStatus.PREPARED
                or rotation.version
                != command.expected_prepared_rotation_version + 1
                or rotation.failure_code is None
            ):
                raise GatewayKeyRotationDeploymentExecutionConflict(
                    f"blocked {command.phase.value} evidence is incomplete"
                )
            return GatewayKeyRotationDeploymentExecutionResult(
                rotation=rotation,
                outcome=GatewayKeyRotationDeploymentExecutionOutcome.BLOCKED,
                checkpoint=checkpoint,
                failure_code=rotation.failure_code,
            )
        if rotation.status is _accepted_status(command.phase):
            outcome = GatewayKeyRotationDeploymentExecutionOutcome.ACCEPTED_REPLAY
        elif rotation.status in _later_statuses(command.phase):
            outcome = GatewayKeyRotationDeploymentExecutionOutcome.ALREADY_ADVANCED
        else:
            raise GatewayKeyRotationDeploymentExecutionConflict(
                f"rotation is not prepared for {command.phase.value} execution"
            )
        if (
            checkpoint is None
            or checkpoint.status is not GatewayKeyRotationDeploymentStatus.ACCEPTED
            or rotation.version <= command.expected_prepared_rotation_version
        ):
            raise GatewayKeyRotationDeploymentExecutionConflict(
                f"accepted {command.phase.value} evidence is incomplete"
            )
        return GatewayKeyRotationDeploymentExecutionResult(
            rotation=rotation,
            outcome=outcome,
            checkpoint=checkpoint,
        )

    def _rotation(self, rotation_id: str) -> GatewayKeyRotation:
        try:
            return self._rotations.get(rotation_id)
        except GatewayKeyRotationError as error:
            raise GatewayKeyRotationDeploymentExecutionConflict(str(error)) from error

    @staticmethod
    def _require_authority(command: ProgressGatewayKeyRotationDeployment) -> None:
        if PolicyScope.DELEGATION_KEY_ROTATE not in command.actor_scopes:
            raise GatewayKeyRotationDeploymentExecutionAuthorizationDenied(
                f"{command.phase.value} execution requires delegation-key:rotate"
            )
        if PolicyScope.EXECUTION_OPERATE not in command.worker_authority.scopes:
            raise GatewayKeyRotationDeploymentExecutionAuthorizationDenied(
                f"{command.phase.value} worker requires execution:operate"
            )


def _prepared_checkpoint(
    rotation: GatewayKeyRotation,
    command: ProgressGatewayKeyRotationDeployment,
) -> GatewayKeyRotationDeploymentCheckpoint:
    checkpoint = _checkpoint_for(rotation, command.phase)
    if (
        rotation.status is not _prepared_status(command.phase)
        or rotation.version != command.expected_prepared_rotation_version
        or checkpoint is None
        or checkpoint.phase is not command.phase
        or checkpoint.status is not GatewayKeyRotationDeploymentStatus.PREPARED
    ):
        raise GatewayKeyRotationDeploymentExecutionConflict(
            f"rotation is not the exact prepared {command.phase.value} deployment"
        )
    return checkpoint


def _current_is_base_or_desired(
    workspace: WorkspaceRecord,
    checkpoint: GatewayKeyRotationDeploymentCheckpoint,
) -> bool:
    identity = (
        workspace.current_graph_id,
        workspace.current_realized_projection_id,
    )
    return identity in {
        (
            checkpoint.base_authored_graph_id,
            checkpoint.base_realized_projection_id,
        ),
        (
            checkpoint.desired_authored_graph_id,
            checkpoint.desired_realized_projection_id,
        ),
    }


def _checkpoint_for(
    rotation: GatewayKeyRotation,
    phase: GatewayKeyRotationDeploymentPhase,
) -> GatewayKeyRotationDeploymentCheckpoint | None:
    return (
        rotation.overlap_deployment
        if phase is GatewayKeyRotationDeploymentPhase.OVERLAP
        else rotation.retirement_deployment
    )


def _prepared_status(
    phase: GatewayKeyRotationDeploymentPhase,
) -> GatewayKeyRotationStatus:
    return (
        GatewayKeyRotationStatus.OVERLAP_DEPLOYING
        if phase is GatewayKeyRotationDeploymentPhase.OVERLAP
        else GatewayKeyRotationStatus.RETIREMENT_DEPLOYING
    )


def _accepted_status(
    phase: GatewayKeyRotationDeploymentPhase,
) -> GatewayKeyRotationStatus:
    return (
        GatewayKeyRotationStatus.OVERLAP_READY
        if phase is GatewayKeyRotationDeploymentPhase.OVERLAP
        else GatewayKeyRotationStatus.RETIREMENT_READY
    )


def _later_statuses(
    phase: GatewayKeyRotationDeploymentPhase,
) -> frozenset[GatewayKeyRotationStatus]:
    if phase is GatewayKeyRotationDeploymentPhase.OVERLAP:
        return frozenset(
            {
                GatewayKeyRotationStatus.NEW_KEY_ACTIVE,
                GatewayKeyRotationStatus.DRAINING_OLD_GRANTS,
                GatewayKeyRotationStatus.RETIREMENT_DEPLOYING,
                GatewayKeyRotationStatus.RETIREMENT_READY,
                GatewayKeyRotationStatus.COMPLETED,
            }
        )
    return frozenset({GatewayKeyRotationStatus.COMPLETED})


def _failure_code(
    status: CoordinatorStatus,
    phase: GatewayKeyRotationDeploymentPhase,
) -> str:
    label = phase.value
    return {
        CoordinatorStatus.FAILED: f"{label}-effect-failed",
        CoordinatorStatus.UNSUPPORTED: f"{label}-effect-unsupported",
        CoordinatorStatus.UNCERTAIN: f"{label}-effect-uncertain",
        CoordinatorStatus.IN_FLIGHT: f"{label}-effect-uncertain",
        CoordinatorStatus.BLOCKED: f"{label}-run-blocked",
    }.get(status, f"{label}-execution-unexpected")


def _prefix(rotation_id: str, phase: GatewayKeyRotationDeploymentPhase) -> str:
    return (
        f"gkrot-{phase.value}:"
        + sha256(rotation_id.encode("utf-8")).hexdigest()
    )


def _identifier(value: object, name: str) -> None:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise InvalidOperationCommand(f"{name} is malformed")


__all__ = [
    "GatewayKeyRotationDeploymentExecutionAuthorizationDenied",
    "GatewayKeyRotationDeploymentExecutionConflict",
    "GatewayKeyRotationDeploymentExecutionError",
    "GatewayKeyRotationDeploymentExecutionOutcome",
    "GatewayKeyRotationDeploymentExecutionProgram",
    "GatewayKeyRotationDeploymentExecutionResult",
    "ProgressGatewayKeyRotationDeployment",
]
