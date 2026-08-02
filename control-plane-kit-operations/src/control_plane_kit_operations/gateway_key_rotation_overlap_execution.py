"""Dispatch, reconcile, and accept one prepared gateway overlap deployment."""

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
_LATER_STATUSES = frozenset(
    {
        GatewayKeyRotationStatus.NEW_KEY_ACTIVE,
        GatewayKeyRotationStatus.DRAINING_OLD_GRANTS,
        GatewayKeyRotationStatus.RETIREMENT_DEPLOYING,
        GatewayKeyRotationStatus.COMPLETED,
    }
)


class GatewayKeyRotationOverlapExecutionError(ValueError):
    """Base bounded failure for post-checkpoint overlap execution."""


class GatewayKeyRotationOverlapExecutionConflict(
    GatewayKeyRotationOverlapExecutionError
):
    """Raised when checkpoint, run, graph, or rotation truth diverges."""


class GatewayKeyRotationOverlapExecutionAuthorizationDenied(
    GatewayKeyRotationOverlapExecutionError
):
    """Raised before progress when rotation or worker authority is absent."""


class GatewayKeyRotationOverlapExecutionOutcome(StrEnum):
    DISPATCHED = "dispatched"
    PROGRESSED = "progressed"
    ACCEPTED = "accepted"
    ACCEPTED_REPLAY = "accepted-replay"
    ALREADY_ADVANCED = "already-advanced"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class ProgressGatewayKeyRotationOverlap:
    rotation_id: str
    expected_prepared_rotation_version: int
    actor_id: str
    actor_scopes: tuple[PolicyScope, ...]
    worker_authority: ExecutionWorkerAuthority

    def __post_init__(self) -> None:
        _identifier(self.rotation_id, "rotation_id")
        _identifier(self.actor_id, "actor_id")
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
class GatewayKeyRotationOverlapExecutionResult:
    rotation: GatewayKeyRotation
    outcome: GatewayKeyRotationOverlapExecutionOutcome
    checkpoint: GatewayKeyRotationDeploymentCheckpoint
    coordinator_status: CoordinatorStatus | None = None
    effects_attempted: int = 0
    advancement: CurrentGraphAdvancementResult | None = None
    failure_code: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.rotation, GatewayKeyRotation):
            raise GatewayKeyRotationOverlapExecutionError(
                "execution result rotation is malformed"
            )
        if not isinstance(self.outcome, GatewayKeyRotationOverlapExecutionOutcome):
            raise GatewayKeyRotationOverlapExecutionError(
                "execution outcome is unsupported"
            )
        if not isinstance(self.checkpoint, GatewayKeyRotationDeploymentCheckpoint):
            raise GatewayKeyRotationOverlapExecutionError(
                "execution checkpoint is malformed"
            )
        if self.coordinator_status is not None and not isinstance(
            self.coordinator_status, CoordinatorStatus
        ):
            raise GatewayKeyRotationOverlapExecutionError(
                "coordinator status is malformed"
            )
        if type(self.effects_attempted) is not int or self.effects_attempted < 0:
            raise GatewayKeyRotationOverlapExecutionError(
                "effects_attempted must be nonnegative"
            )
        accepted = self.outcome in {
            GatewayKeyRotationOverlapExecutionOutcome.ACCEPTED,
            GatewayKeyRotationOverlapExecutionOutcome.ACCEPTED_REPLAY,
            GatewayKeyRotationOverlapExecutionOutcome.ALREADY_ADVANCED,
        }
        if accepted != (
            self.checkpoint.status
            is GatewayKeyRotationDeploymentStatus.ACCEPTED
        ):
            raise GatewayKeyRotationOverlapExecutionError(
                "accepted outcome and checkpoint disagree"
            )
        blocked = self.outcome is GatewayKeyRotationOverlapExecutionOutcome.BLOCKED
        if blocked != (self.failure_code is not None):
            raise GatewayKeyRotationOverlapExecutionError(
                "blocked outcome requires one bounded failure code"
            )


@dataclass(frozen=True)
class _OverlapSnapshot:
    workspace: WorkspaceRecord
    checkpoint: GatewayKeyRotationDeploymentCheckpoint
    plan: ActivityPlanRecord
    request: ExecutionRequestRecord
    run: ActivityRunRecord


class GatewayKeyRotationOverlapExecutionProgram:
    """Advance one prepared overlap deployment by one recoverable invocation."""

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
        command: ProgressGatewayKeyRotationOverlap,
    ) -> GatewayKeyRotationOverlapExecutionResult:
        if not isinstance(command, ProgressGatewayKeyRotationOverlap):
            raise TypeError("command must be ProgressGatewayKeyRotationOverlap")
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
                        f"{_prefix(rotation.rotation_id)}:execute"
                    ),
                    max_effects=1,
                )
            )
        except ExecutionCoordinatorError as error:
            raise GatewayKeyRotationOverlapExecutionConflict(str(error)) from error
        if coordinated.status is CoordinatorStatus.COMPLETED:
            return self._advance_and_accept(
                rotation,
                snapshot,
                command,
                effects_attempted=coordinated.effects_attempted,
            )
        if coordinated.status is CoordinatorStatus.PROGRESSED:
            outcome = (
                GatewayKeyRotationOverlapExecutionOutcome.DISPATCHED
                if coordinated.effects_attempted
                else GatewayKeyRotationOverlapExecutionOutcome.PROGRESSED
            )
            return GatewayKeyRotationOverlapExecutionResult(
                rotation=rotation,
                outcome=outcome,
                checkpoint=snapshot.checkpoint,
                coordinator_status=coordinated.status,
                effects_attempted=coordinated.effects_attempted,
            )
        failure_code = _failure_code(coordinated.status)
        blocked = self._block(rotation, command, failure_code)
        return GatewayKeyRotationOverlapExecutionResult(
            rotation=blocked,
            outcome=GatewayKeyRotationOverlapExecutionOutcome.BLOCKED,
            checkpoint=snapshot.checkpoint,
            coordinator_status=coordinated.status,
            effects_attempted=coordinated.effects_attempted,
            failure_code=failure_code,
        )

    def _advance_and_accept(
        self,
        rotation: GatewayKeyRotation,
        snapshot: _OverlapSnapshot,
        command: ProgressGatewayKeyRotationOverlap,
        *,
        effects_attempted: int = 0,
    ) -> GatewayKeyRotationOverlapExecutionResult:
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
                        f"{_prefix(rotation.rotation_id)}:advance"
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
                    transition_id=f"{_prefix(rotation.rotation_id)}:accepted",
                    expected_status=GatewayKeyRotationStatus.OVERLAP_DEPLOYING,
                    expected_version=rotation.version,
                    target_status=GatewayKeyRotationStatus.OVERLAP_READY,
                    advanced_by=command.actor_id,
                    advanced_at=advancement.event.occurred_at,
                    actor_scopes=command.actor_scopes,
                    deployment=accepted_checkpoint,
                )
            )
        except (CurrentGraphAdvancementError, GatewayKeyRotationError) as error:
            raise GatewayKeyRotationOverlapExecutionConflict(str(error)) from error
        return GatewayKeyRotationOverlapExecutionResult(
            rotation=accepted,
            outcome=GatewayKeyRotationOverlapExecutionOutcome.ACCEPTED,
            checkpoint=accepted_checkpoint,
            coordinator_status=CoordinatorStatus.COMPLETED,
            effects_attempted=effects_attempted,
            advancement=advancement,
        )

    def _block(
        self,
        rotation: GatewayKeyRotation,
        command: ProgressGatewayKeyRotationOverlap,
        failure_code: str,
    ) -> GatewayKeyRotation:
        try:
            return self._rotations.advance(
                AdvanceGatewayKeyRotation(
                    rotation_id=rotation.rotation_id,
                    transition_id=(
                        f"{_prefix(rotation.rotation_id)}:blocked:{failure_code}"
                    ),
                    expected_status=GatewayKeyRotationStatus.OVERLAP_DEPLOYING,
                    expected_version=rotation.version,
                    target_status=GatewayKeyRotationStatus.BLOCKED,
                    advanced_by=command.actor_id,
                    advanced_at=self._clock(),
                    actor_scopes=command.actor_scopes,
                    failure_code=failure_code,
                )
            )
        except GatewayKeyRotationError as error:
            raise GatewayKeyRotationOverlapExecutionConflict(str(error)) from error

    def _snapshot(
        self,
        rotation: GatewayKeyRotation,
        command: ProgressGatewayKeyRotationOverlap,
    ) -> _OverlapSnapshot:
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
                raise GatewayKeyRotationOverlapExecutionConflict(
                    "overlap child truth is missing"
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
            raise GatewayKeyRotationOverlapExecutionConflict(
                "overlap checkpoint does not match durable child truth"
            )
        return _OverlapSnapshot(workspace, checkpoint, plan, request, run)

    @staticmethod
    def _current_is_desired(snapshot: _OverlapSnapshot) -> bool:
        return (
            snapshot.workspace.current_graph_id
            == snapshot.checkpoint.desired_authored_graph_id
            and snapshot.workspace.current_realized_projection_id
            == snapshot.checkpoint.desired_realized_projection_id
        )

    def _classify_existing(
        self,
        rotation: GatewayKeyRotation,
        command: ProgressGatewayKeyRotationOverlap,
    ) -> GatewayKeyRotationOverlapExecutionResult | None:
        if rotation.status is GatewayKeyRotationStatus.OVERLAP_DEPLOYING:
            return None
        checkpoint = rotation.overlap_deployment
        if rotation.status is GatewayKeyRotationStatus.BLOCKED:
            if (
                checkpoint is None
                or checkpoint.status is not GatewayKeyRotationDeploymentStatus.PREPARED
                or rotation.version
                != command.expected_prepared_rotation_version + 1
                or rotation.failure_code is None
            ):
                raise GatewayKeyRotationOverlapExecutionConflict(
                    "blocked overlap evidence is incomplete"
                )
            return GatewayKeyRotationOverlapExecutionResult(
                rotation=rotation,
                outcome=GatewayKeyRotationOverlapExecutionOutcome.BLOCKED,
                checkpoint=checkpoint,
                failure_code=rotation.failure_code,
            )
        if rotation.status is GatewayKeyRotationStatus.OVERLAP_READY:
            outcome = GatewayKeyRotationOverlapExecutionOutcome.ACCEPTED_REPLAY
        elif rotation.status in _LATER_STATUSES:
            outcome = GatewayKeyRotationOverlapExecutionOutcome.ALREADY_ADVANCED
        else:
            raise GatewayKeyRotationOverlapExecutionConflict(
                "rotation is not prepared for overlap execution"
            )
        if (
            checkpoint is None
            or checkpoint.status is not GatewayKeyRotationDeploymentStatus.ACCEPTED
            or rotation.version <= command.expected_prepared_rotation_version
        ):
            raise GatewayKeyRotationOverlapExecutionConflict(
                "accepted overlap evidence is incomplete"
            )
        return GatewayKeyRotationOverlapExecutionResult(
            rotation=rotation,
            outcome=outcome,
            checkpoint=checkpoint,
        )

    def _rotation(self, rotation_id: str) -> GatewayKeyRotation:
        try:
            return self._rotations.get(rotation_id)
        except GatewayKeyRotationError as error:
            raise GatewayKeyRotationOverlapExecutionConflict(str(error)) from error

    @staticmethod
    def _require_authority(command: ProgressGatewayKeyRotationOverlap) -> None:
        if PolicyScope.DELEGATION_KEY_ROTATE not in command.actor_scopes:
            raise GatewayKeyRotationOverlapExecutionAuthorizationDenied(
                "overlap execution requires delegation-key:rotate"
            )
        if PolicyScope.EXECUTION_OPERATE not in command.worker_authority.scopes:
            raise GatewayKeyRotationOverlapExecutionAuthorizationDenied(
                "overlap worker requires execution:operate"
            )


def _prepared_checkpoint(
    rotation: GatewayKeyRotation,
    command: ProgressGatewayKeyRotationOverlap,
) -> GatewayKeyRotationDeploymentCheckpoint:
    checkpoint = rotation.overlap_deployment
    if (
        rotation.status is not GatewayKeyRotationStatus.OVERLAP_DEPLOYING
        or rotation.version != command.expected_prepared_rotation_version
        or checkpoint is None
        or checkpoint.phase is not GatewayKeyRotationDeploymentPhase.OVERLAP
        or checkpoint.status is not GatewayKeyRotationDeploymentStatus.PREPARED
    ):
        raise GatewayKeyRotationOverlapExecutionConflict(
            "rotation is not the exact prepared overlap deployment"
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


def _failure_code(status: CoordinatorStatus) -> str:
    return {
        CoordinatorStatus.FAILED: "overlap-effect-failed",
        CoordinatorStatus.UNSUPPORTED: "overlap-effect-unsupported",
        CoordinatorStatus.UNCERTAIN: "overlap-effect-uncertain",
        CoordinatorStatus.IN_FLIGHT: "overlap-effect-uncertain",
        CoordinatorStatus.BLOCKED: "overlap-run-blocked",
    }.get(status, "overlap-execution-unexpected")


def _prefix(rotation_id: str) -> str:
    return "gkrot-overlap:" + sha256(rotation_id.encode("utf-8")).hexdigest()


def _identifier(value: object, name: str) -> None:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise InvalidOperationCommand(f"{name} is malformed")


__all__ = [
    "GatewayKeyRotationOverlapExecutionAuthorizationDenied",
    "GatewayKeyRotationOverlapExecutionConflict",
    "GatewayKeyRotationOverlapExecutionError",
    "GatewayKeyRotationOverlapExecutionOutcome",
    "GatewayKeyRotationOverlapExecutionProgram",
    "GatewayKeyRotationOverlapExecutionResult",
    "ProgressGatewayKeyRotationOverlap",
]
