"""Typed overlap wrapper around the phase-neutral rotation execution kernel."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Callable

from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_operations.execution_leases import ExecutionLeaseFence
from control_plane_kit_operations.coordinator import (
    CoordinatorStatus,
    ExecutionCoordinator,
)
from control_plane_kit_operations.gateway_key_rotation_deployment_execution import (
    GatewayKeyRotationDeploymentExecutionAuthorizationDenied,
    GatewayKeyRotationDeploymentExecutionConflict,
    GatewayKeyRotationDeploymentExecutionOutcome,
    GatewayKeyRotationDeploymentExecutionProgram,
    GatewayKeyRotationDeploymentExecutionResult,
    ProgressGatewayKeyRotationDeployment,
)
from control_plane_kit_operations.gateway_key_rotations import (
    GatewayKeyRotation,
    GatewayKeyRotationDeploymentCheckpoint,
    GatewayKeyRotationDeploymentPhase,
    GatewayKeyRotationDeploymentStatus,
)
from control_plane_kit_operations.lifecycle import ExecutionWorkerAuthority
from control_plane_kit_operations.advancement import CurrentGraphAdvancementResult


class GatewayKeyRotationOverlapExecutionError(ValueError):
    pass


class GatewayKeyRotationOverlapExecutionConflict(
    GatewayKeyRotationOverlapExecutionError
):
    pass


class GatewayKeyRotationOverlapExecutionAuthorizationDenied(
    GatewayKeyRotationOverlapExecutionError
):
    pass


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
    fence: ExecutionLeaseFence

    def __post_init__(self) -> None:
        normalized = self.deployment_command()
        object.__setattr__(self, "actor_scopes", normalized.actor_scopes)

    def deployment_command(self) -> ProgressGatewayKeyRotationDeployment:
        return ProgressGatewayKeyRotationDeployment(
            rotation_id=self.rotation_id,
            phase=GatewayKeyRotationDeploymentPhase.OVERLAP,
            expected_prepared_rotation_version=(
                self.expected_prepared_rotation_version
            ),
            actor_id=self.actor_id,
            actor_scopes=self.actor_scopes,
            worker_authority=self.worker_authority,
            fence=self.fence,
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


class GatewayKeyRotationOverlapExecutionProgram:
    def __init__(
        self,
        unit_of_work_factory: Callable[[], Any],
        *,
        coordinator: ExecutionCoordinator,
        clock: Callable[[], str],
        trusted_epoch_clock: Callable[[], int],
        id_factory: Callable[[], str],
    ) -> None:
        self._program = GatewayKeyRotationDeploymentExecutionProgram(
            unit_of_work_factory,
            coordinator=coordinator,
            clock=clock,
            trusted_epoch_clock=trusted_epoch_clock,
            id_factory=id_factory,
        )

    def progress(
        self,
        command: ProgressGatewayKeyRotationOverlap,
    ) -> GatewayKeyRotationOverlapExecutionResult:
        if not isinstance(command, ProgressGatewayKeyRotationOverlap):
            raise TypeError("command must be ProgressGatewayKeyRotationOverlap")
        try:
            result = self._program.progress(command.deployment_command())
        except GatewayKeyRotationDeploymentExecutionAuthorizationDenied as error:
            raise GatewayKeyRotationOverlapExecutionAuthorizationDenied(
                str(error)
            ) from error
        except GatewayKeyRotationDeploymentExecutionConflict as error:
            raise GatewayKeyRotationOverlapExecutionConflict(str(error)) from error
        return _result(result)


def _result(
    value: GatewayKeyRotationDeploymentExecutionResult,
) -> GatewayKeyRotationOverlapExecutionResult:
    return GatewayKeyRotationOverlapExecutionResult(
        rotation=value.rotation,
        outcome=GatewayKeyRotationOverlapExecutionOutcome(value.outcome.value),
        checkpoint=value.checkpoint,
        coordinator_status=value.coordinator_status,
        effects_attempted=value.effects_attempted,
        advancement=value.advancement,
        failure_code=value.failure_code,
    )


__all__ = [
    "GatewayKeyRotationOverlapExecutionAuthorizationDenied",
    "GatewayKeyRotationOverlapExecutionConflict",
    "GatewayKeyRotationOverlapExecutionError",
    "GatewayKeyRotationOverlapExecutionOutcome",
    "GatewayKeyRotationOverlapExecutionProgram",
    "GatewayKeyRotationOverlapExecutionResult",
    "ProgressGatewayKeyRotationOverlap",
]
