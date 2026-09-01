"""Typed retirement wrapper around the phase-neutral rotation execution kernel."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Callable

from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_operations.execution_leases import ExecutionLeaseFence
from control_plane_kit_operations.advancement import CurrentGraphAdvancementResult
from control_plane_kit_operations.coordinator import (
    CoordinatorStatus,
    ExecutionCoordinator,
)
from control_plane_kit_operations.gateway_key_rotation_deployment_execution import (
    GatewayKeyRotationDeploymentExecutionAuthorizationDenied,
    GatewayKeyRotationDeploymentExecutionConflict,
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
from control_plane_kit_operations.workflows import IdempotencyKey


class GatewayKeyRotationRetirementExecutionError(ValueError):
    """Base bounded failure for post-checkpoint retirement execution."""


class GatewayKeyRotationRetirementExecutionConflict(
    GatewayKeyRotationRetirementExecutionError
):
    """Raised when checkpoint, run, graph, or rotation truth diverges."""


class GatewayKeyRotationRetirementExecutionAuthorizationDenied(
    GatewayKeyRotationRetirementExecutionError
):
    """Raised before progress when rotation or worker authority is absent."""


class GatewayKeyRotationRetirementExecutionOutcome(StrEnum):
    DISPATCHED = "dispatched"
    PROGRESSED = "progressed"
    ACCEPTED = "accepted"
    ACCEPTED_REPLAY = "accepted-replay"
    ALREADY_ADVANCED = "already-advanced"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class ProgressGatewayKeyRotationRetirement:
    rotation_id: str
    expected_prepared_rotation_version: int
    actor_id: str
    actor_scopes: tuple[PolicyScope, ...]
    worker_authority: ExecutionWorkerAuthority
    fence: ExecutionLeaseFence
    idempotency_key: IdempotencyKey

    def __post_init__(self) -> None:
        normalized = self.deployment_command()
        object.__setattr__(self, "actor_scopes", normalized.actor_scopes)

    def deployment_command(self) -> ProgressGatewayKeyRotationDeployment:
        return ProgressGatewayKeyRotationDeployment(
            rotation_id=self.rotation_id,
            phase=GatewayKeyRotationDeploymentPhase.RETIREMENT,
            expected_prepared_rotation_version=(
                self.expected_prepared_rotation_version
            ),
            actor_id=self.actor_id,
            actor_scopes=self.actor_scopes,
            worker_authority=self.worker_authority,
            fence=self.fence,
            idempotency_key=self.idempotency_key,
        )


@dataclass(frozen=True)
class GatewayKeyRotationRetirementExecutionResult:
    rotation: GatewayKeyRotation
    outcome: GatewayKeyRotationRetirementExecutionOutcome
    checkpoint: GatewayKeyRotationDeploymentCheckpoint
    coordinator_status: CoordinatorStatus | None = None
    effects_attempted: int = 0
    advancement: CurrentGraphAdvancementResult | None = None
    failure_code: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.rotation, GatewayKeyRotation):
            raise GatewayKeyRotationRetirementExecutionError(
                "execution result rotation is malformed"
            )
        if not isinstance(
            self.outcome, GatewayKeyRotationRetirementExecutionOutcome
        ):
            raise GatewayKeyRotationRetirementExecutionError(
                "execution outcome is unsupported"
            )
        if not isinstance(self.checkpoint, GatewayKeyRotationDeploymentCheckpoint):
            raise GatewayKeyRotationRetirementExecutionError(
                "execution checkpoint is malformed"
            )
        if self.coordinator_status is not None and not isinstance(
            self.coordinator_status, CoordinatorStatus
        ):
            raise GatewayKeyRotationRetirementExecutionError(
                "coordinator status is malformed"
            )
        if type(self.effects_attempted) is not int or self.effects_attempted < 0:
            raise GatewayKeyRotationRetirementExecutionError(
                "effects_attempted must be nonnegative"
            )
        accepted = self.outcome in {
            GatewayKeyRotationRetirementExecutionOutcome.ACCEPTED,
            GatewayKeyRotationRetirementExecutionOutcome.ACCEPTED_REPLAY,
            GatewayKeyRotationRetirementExecutionOutcome.ALREADY_ADVANCED,
        }
        if accepted != (
            self.checkpoint.status
            is GatewayKeyRotationDeploymentStatus.ACCEPTED
        ):
            raise GatewayKeyRotationRetirementExecutionError(
                "accepted outcome and checkpoint disagree"
            )
        blocked = (
            self.outcome is GatewayKeyRotationRetirementExecutionOutcome.BLOCKED
        )
        if blocked != (self.failure_code is not None):
            raise GatewayKeyRotationRetirementExecutionError(
                "blocked outcome requires one bounded failure code"
            )


class GatewayKeyRotationRetirementExecutionProgram:
    """Advance one prepared retirement deployment by one recoverable invocation."""

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
        command: ProgressGatewayKeyRotationRetirement,
    ) -> GatewayKeyRotationRetirementExecutionResult:
        if not isinstance(command, ProgressGatewayKeyRotationRetirement):
            raise TypeError("command must be ProgressGatewayKeyRotationRetirement")
        authorization_message = None
        conflict_message = None
        try:
            result = self._program.progress(command.deployment_command())
        except GatewayKeyRotationDeploymentExecutionAuthorizationDenied as error:
            authorization_message = str(error)
        except GatewayKeyRotationDeploymentExecutionConflict as error:
            conflict_message = str(error)
        if authorization_message is not None:
            raise GatewayKeyRotationRetirementExecutionAuthorizationDenied(
                authorization_message
            )
        if conflict_message is not None:
            raise GatewayKeyRotationRetirementExecutionConflict(conflict_message)
        return _result(result)


def _result(
    value: GatewayKeyRotationDeploymentExecutionResult,
) -> GatewayKeyRotationRetirementExecutionResult:
    return GatewayKeyRotationRetirementExecutionResult(
        rotation=value.rotation,
        outcome=GatewayKeyRotationRetirementExecutionOutcome(value.outcome.value),
        checkpoint=value.checkpoint,
        coordinator_status=value.coordinator_status,
        effects_attempted=value.effects_attempted,
        advancement=value.advancement,
        failure_code=value.failure_code,
    )


__all__ = [
    "GatewayKeyRotationRetirementExecutionAuthorizationDenied",
    "GatewayKeyRotationRetirementExecutionConflict",
    "GatewayKeyRotationRetirementExecutionError",
    "GatewayKeyRotationRetirementExecutionOutcome",
    "GatewayKeyRotationRetirementExecutionProgram",
    "GatewayKeyRotationRetirementExecutionResult",
    "ProgressGatewayKeyRotationRetirement",
]
