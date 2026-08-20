"""Public command and observer language for runtime-effect reconciliation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from control_plane_kit_core.operations import EffectAttemptIdentity, RunId
from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_core.runtime_effect_observation import (
    RuntimeEffectObservationRequest,
    RuntimeEffectObservationResult,
)
from control_plane_kit_operations.execution_leases import ExecutionLeaseFence
from control_plane_kit_operations.lifecycle import ExecutionWorkerAuthority
from control_plane_kit_operations.runtime_authorities import (
    RegisteredRuntimeAuthority,
)
from control_plane_kit_operations.workflows import InvalidOperationCommand


class EffectAttemptReconciliationError(RuntimeError):
    """Base error for effect-attempt reconciliation."""


class EffectAttemptReconciliationNotFound(EffectAttemptReconciliationError):
    """Raised when required durable reconciliation truth is absent."""


class EffectAttemptReconciliationConflict(EffectAttemptReconciliationError):
    """Raised when durable reconciliation truth is incongruent."""


class EffectAttemptReconciliationDenied(EffectAttemptReconciliationError):
    """Raised when reconciliation authority is insufficient."""


class RuntimeEffectObserver(Protocol):
    """Observe one exact runtime effect without mutation authority."""

    def observe(
        self,
        request: RuntimeEffectObservationRequest,
        authority: RegisteredRuntimeAuthority | None,
    ) -> RuntimeEffectObservationResult: ...


@dataclass(frozen=True)
class ReconcileEffectAttempt:
    """Request reconciliation of one durable started effect attempt."""

    request_id: str
    identity: EffectAttemptIdentity
    authority: ExecutionWorkerAuthority
    fence: ExecutionLeaseFence

    def __post_init__(self) -> None:
        if not _valid_reconcile_command(self):
            raise InvalidOperationCommand(
                "effect attempt reconciliation command is invalid"
            )


def _valid_reconcile_command(command: object) -> bool:
    if type(command) is not ReconcileEffectAttempt:
        return False
    try:
        request_id = command.request_id
        identity = command.identity
        authority = command.authority
        fence = command.fence
    except AttributeError:
        return False
    if (
        not _bounded_command_text(request_id)
        or type(identity) is not EffectAttemptIdentity
        or type(identity.run_id) is not RunId
        or type(identity.run_id.value) is not str
        or not _bounded_command_text(identity.run_id.value)
        or type(identity.activity_id) is not str
        or not _bounded_command_text(identity.activity_id)
        or type(identity.attempt) is not int
        or type(authority) is not ExecutionWorkerAuthority
        or type(authority.worker_id) is not str
        or not _bounded_command_text(authority.worker_id)
        or type(authority.scopes) is not tuple
        or any(type(scope) is not PolicyScope for scope in authority.scopes)
        or type(fence) is not ExecutionLeaseFence
        or type(fence.worker_id) is not str
        or not _bounded_command_text(fence.worker_id)
        or type(fence.generation) is not int
        or authority.worker_id != fence.worker_id
    ):
        return False
    try:
        reconstructed_identity = EffectAttemptIdentity(
            RunId(identity.run_id.value),
            identity.activity_id,
            identity.attempt,
        )
        reconstructed_authority = ExecutionWorkerAuthority(
            authority.worker_id,
            authority.scopes,
        )
        reconstructed_fence = ExecutionLeaseFence(
            fence.worker_id,
            fence.generation,
        )
    except (InvalidOperationCommand, ValueError):
        return False
    return (
        reconstructed_identity == identity
        and reconstructed_authority == authority
        and reconstructed_fence == fence
    )


def _bounded_command_text(value: object) -> bool:
    if value.__class__ is not str:
        return False
    if not value:
        return False
    try:
        return len(value.encode("utf-8")) <= 512 and not any(
            ord(character) < 32 for character in value
        )
    except UnicodeEncodeError:
        return False


__all__ = [
    "EffectAttemptReconciliationConflict",
    "EffectAttemptReconciliationDenied",
    "EffectAttemptReconciliationError",
    "EffectAttemptReconciliationNotFound",
    "ReconcileEffectAttempt",
    "RuntimeEffectObserver",
]
