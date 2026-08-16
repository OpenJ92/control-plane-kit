"""Public command and result language for one effect-attempt start."""

from __future__ import annotations

from dataclasses import dataclass

from control_plane_kit_core.operations import (
    EffectAttemptIdentity,
    EffectAttemptStatus,
    EffectAttemptTransition,
    EffectAttemptTransitionKind,
    RunId,
)
from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_operations.effect_attempts import EffectAttemptRecord
from control_plane_kit_operations.execution_leases import ExecutionLeaseFence
from control_plane_kit_operations.lifecycle import ExecutionWorkerAuthority
from control_plane_kit_operations.records import OperationsRecordError
from control_plane_kit_operations.workflows import InvalidOperationCommand


class EffectAttemptStartError(RuntimeError):
    """Base error for effect-attempt start interpretation."""


class EffectAttemptStartNotFound(EffectAttemptStartError):
    """Raised when required request, run, plan, or attempt truth is absent."""


class EffectAttemptStartConflict(EffectAttemptStartError):
    """Raised when durable truth is malformed or incongruent."""


class EffectAttemptStartDenied(EffectAttemptStartError):
    """Raised when execution authority cannot start the attempt."""


@dataclass(frozen=True)
class StartEffectAttempt:
    """Request the first external-effect attempt under one execution lease."""

    request_id: str
    transition: EffectAttemptTransition
    authority: ExecutionWorkerAuthority
    fence: ExecutionLeaseFence

    def __post_init__(self) -> None:
        if not _valid_start_command(self):
            raise InvalidOperationCommand(
                "effect attempt start command is invalid"
            )


@dataclass(frozen=True)
class NewlyStarted:
    """One attempt newly committed by this invocation."""

    attempt: EffectAttemptRecord

    def __post_init__(self) -> None:
        if (
            type(self) is not NewlyStarted
            or type(self.attempt) is not EffectAttemptRecord
            or self.attempt.state.status is not EffectAttemptStatus.STARTED
            or self.attempt.original_start_event
            != self.attempt.latest_transition_event
        ):
            raise OperationsRecordError(
                "effect attempt start result is invalid"
            )


@dataclass(frozen=True)
class ExistingAttempt:
    """One exact committed attempt observed without dispatch authority."""

    attempt: EffectAttemptRecord

    def __post_init__(self) -> None:
        if (
            type(self) is not ExistingAttempt
            or type(self.attempt) is not EffectAttemptRecord
        ):
            raise OperationsRecordError(
                "effect attempt start result is invalid"
            )


EffectAttemptStartResult = NewlyStarted | ExistingAttempt


def _valid_start_command(command: object) -> bool:
    if type(command) is not StartEffectAttempt:
        return False
    transition = command.transition
    authority = command.authority
    fence = command.fence
    if (
        not _bounded_command_text(command.request_id)
        or type(transition) is not EffectAttemptTransition
        or type(transition.identity) is not EffectAttemptIdentity
        or type(transition.identity.run_id) is not RunId
        or type(transition.identity.run_id.value) is not str
        or type(transition.identity.activity_id) is not str
        or type(transition.identity.attempt) is not int
        or type(transition.request_fingerprint) is not str
        or not _valid_start_transition(transition)
        or type(authority) is not ExecutionWorkerAuthority
        or type(authority.worker_id) is not str
        or any(
            0 < ord(character) < 32
            for character in authority.worker_id
        )
        or type(authority.scopes) is not tuple
        or any(type(scope) is not PolicyScope for scope in authority.scopes)
        or type(fence) is not ExecutionLeaseFence
        or type(fence.worker_id) is not str
        or any(0 < ord(character) < 32 for character in fence.worker_id)
        or type(fence.generation) is not int
        or authority.worker_id != fence.worker_id
    ):
        return False
    return True


def _valid_start_transition(transition: EffectAttemptTransition) -> bool:
    identity = transition.identity
    try:
        reconstructed_identity = EffectAttemptIdentity(
            identity.run_id,
            identity.activity_id,
            identity.attempt,
        )
        reconstructed = EffectAttemptTransition(
            transition.kind,
            reconstructed_identity,
            request_fingerprint=transition.request_fingerprint,
            outcome_fingerprint=transition.outcome_fingerprint,
            prior_attempt=transition.prior_attempt,
            recovery_decision=transition.recovery_decision,
        )
    except ValueError:
        return False
    return (
        transition.kind is EffectAttemptTransitionKind.STARTED
        and identity.attempt == 1
        and transition.prior_attempt is None
        and reconstructed == transition
    )


def _bounded_command_text(value: object) -> bool:
    valid = (
        type(value) is str
        and 1 <= len(value) <= 512
        and not any(ord(character) < 32 for character in value)
    )
    if not valid:
        return False
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


__all__ = [
    "EffectAttemptStartConflict",
    "EffectAttemptStartDenied",
    "EffectAttemptStartError",
    "EffectAttemptStartNotFound",
    "EffectAttemptStartResult",
    "ExistingAttempt",
    "NewlyStarted",
    "StartEffectAttempt",
]
