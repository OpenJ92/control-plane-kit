"""Public command and result language for one effect-attempt fold."""

from __future__ import annotations

from dataclasses import dataclass

from control_plane_kit_core.operations import (
    EffectAttemptIdentity,
    EffectAttemptStatus,
    EffectAttemptTransition,
    EffectAttemptTransitionKind,
    EffectRecoveryDecision,
    EffectRecoveryResolution,
    RunId,
)
from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_operations.effect_attempts import EffectAttemptRecord
from control_plane_kit_operations.execution_leases import ExecutionLeaseFence
from control_plane_kit_operations.lifecycle import ExecutionWorkerAuthority
from control_plane_kit_operations.records import (
    BoundedEvidence,
    FailureCategory,
    FailureEvidence,
    OperationsRecordError,
)
from control_plane_kit_operations.workflows import InvalidOperationCommand


class EffectAttemptFoldError(RuntimeError):
    """Base error for effect-attempt fold interpretation."""


class EffectAttemptFoldNotFound(EffectAttemptFoldError):
    """Raised when required request, run, or attempt truth is absent."""


class EffectAttemptFoldConflict(EffectAttemptFoldError):
    """Raised when durable truth is malformed or incongruent."""


class EffectAttemptFoldDenied(EffectAttemptFoldError):
    """Raised when execution authority cannot fold the attempt."""


@dataclass(frozen=True)
class FoldEffectAttempt:
    """Fold one direct result or accepted recovery decision."""

    request_id: str
    transition: EffectAttemptTransition
    authority: ExecutionWorkerAuthority
    fence: ExecutionLeaseFence
    failure: FailureEvidence | None

    def __post_init__(self) -> None:
        if not _valid_fold_command(self):
            raise InvalidOperationCommand("effect attempt fold command is invalid")


@dataclass(frozen=True)
class NewlyFolded:
    """One effect attempt newly folded by this invocation."""

    attempt: EffectAttemptRecord

    def __post_init__(self) -> None:
        if type(self) is not NewlyFolded or not _valid_fold_result(self.attempt):
            raise OperationsRecordError("effect attempt fold result is invalid")


@dataclass(frozen=True)
class ExistingFold:
    """One exact committed fold observed without mutation authority."""

    attempt: EffectAttemptRecord

    def __post_init__(self) -> None:
        if type(self) is not ExistingFold or not _valid_fold_result(self.attempt):
            raise OperationsRecordError("effect attempt fold result is invalid")


EffectAttemptFoldResult = NewlyFolded | ExistingFold


def _valid_fold_command(command: object) -> bool:
    if type(command) is not FoldEffectAttempt:
        return False
    transition = command.transition
    authority = command.authority
    fence = command.fence
    if (
        not _bounded_command_text(command.request_id)
        or not _transition_is_exact(transition)
        or transition.kind is EffectAttemptTransitionKind.STARTED
        or type(authority) is not ExecutionWorkerAuthority
        or type(authority.worker_id) is not str
        or any(ord(character) < 32 for character in authority.worker_id)
        or type(authority.scopes) is not tuple
        or any(type(scope) is not PolicyScope for scope in authority.scopes)
        or type(fence) is not ExecutionLeaseFence
        or type(fence.worker_id) is not str
        or any(ord(character) < 32 for character in fence.worker_id)
        or type(fence.generation) is not int
        or authority.worker_id != fence.worker_id
        or not _failure_is_exact(command.failure)
        or (command.failure is not None) is not _transition_requires_failure(
            transition
        )
    ):
        return False
    try:
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
    return reconstructed_authority == authority and reconstructed_fence == fence


def _transition_is_exact(transition: object) -> bool:
    if (
        type(transition) is not EffectAttemptTransition
        or type(transition.kind) is not EffectAttemptTransitionKind
        or not _identity_is_exact(transition.identity)
        or (
            transition.request_fingerprint is not None
            and type(transition.request_fingerprint) is not str
        )
        or (
            transition.outcome_fingerprint is not None
            and type(transition.outcome_fingerprint) is not str
        )
        or (
            transition.prior_attempt is not None
            and not _identity_is_exact(transition.prior_attempt)
        )
        or (
            transition.recovery_decision is not None
            and not _decision_is_exact(transition.recovery_decision)
        )
    ):
        return False
    try:
        reconstructed = EffectAttemptTransition(
            transition.kind,
            EffectAttemptIdentity(
                RunId(transition.identity.run_id.value),
                transition.identity.activity_id,
                transition.identity.attempt,
            ),
            request_fingerprint=transition.request_fingerprint,
            outcome_fingerprint=transition.outcome_fingerprint,
            prior_attempt=(
                EffectAttemptIdentity(
                    RunId(transition.prior_attempt.run_id.value),
                    transition.prior_attempt.activity_id,
                    transition.prior_attempt.attempt,
                )
                if transition.prior_attempt is not None
                else None
            ),
            recovery_decision=(
                _reconstruct_decision(transition.recovery_decision)
                if transition.recovery_decision is not None
                else None
            ),
        )
    except ValueError:
        return False
    return reconstructed == transition


def _identity_is_exact(identity: object) -> bool:
    return (
        type(identity) is EffectAttemptIdentity
        and type(identity.run_id) is RunId
        and type(identity.run_id.value) is str
        and type(identity.activity_id) is str
        and type(identity.attempt) is int
    )


def _decision_is_exact(decision: object) -> bool:
    return (
        type(decision) is EffectRecoveryDecision
        and type(decision.decision_id) is str
        and _identity_is_exact(decision.attempt_identity)
        and type(decision.resolution) is EffectRecoveryResolution
        and type(decision.uncertain_fingerprint) is str
        and type(decision.evidence_fingerprint) is str
    )


def _reconstruct_decision(decision: EffectRecoveryDecision) -> EffectRecoveryDecision:
    identity = decision.attempt_identity
    return EffectRecoveryDecision(
        decision.decision_id,
        EffectAttemptIdentity(
            RunId(identity.run_id.value),
            identity.activity_id,
            identity.attempt,
        ),
        decision.resolution,
        decision.uncertain_fingerprint,
        decision.evidence_fingerprint,
    )


def _failure_is_exact(failure: object) -> bool:
    if failure is None:
        return True
    if (
        type(failure) is not FailureEvidence
        or type(failure.category) is not FailureCategory
        or type(failure.code) is not str
        or type(failure.message) is not str
        or type(failure.details) is not BoundedEvidence
        or type(failure.details.canonical_json) is not str
    ):
        return False
    try:
        reconstructed = FailureEvidence(
            failure.category,
            failure.code,
            failure.message,
            BoundedEvidence(failure.details.canonical_json),
        )
    except (OperationsRecordError, ValueError):
        return False
    return reconstructed == failure


def _transition_requires_failure(transition: EffectAttemptTransition) -> bool:
    if transition.kind in {
        EffectAttemptTransitionKind.FAILED,
        EffectAttemptTransitionKind.UNSUPPORTED,
        EffectAttemptTransitionKind.UNCERTAIN,
    }:
        return True
    decision = transition.recovery_decision
    return (
        transition.kind is EffectAttemptTransitionKind.RECONCILED
        and decision is not None
        and decision.resolution is EffectRecoveryResolution.FAILED
    )


def _valid_fold_result(value: object) -> bool:
    if type(value) is not EffectAttemptRecord:
        return False
    try:
        reconstructed = EffectAttemptRecord(
            value.state,
            value.original_start_event,
            value.latest_transition_event,
        )
    except (OperationsRecordError, ValueError):
        return False
    return (
        reconstructed == value
        and value.state.status is not EffectAttemptStatus.STARTED
        and (value.latest_transition_event.failure is not None)
        is (
            value.state.status
            in {
                EffectAttemptStatus.FAILED,
                EffectAttemptStatus.UNSUPPORTED,
                EffectAttemptStatus.UNCERTAIN,
            }
        )
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
    "EffectAttemptFoldConflict",
    "EffectAttemptFoldDenied",
    "EffectAttemptFoldError",
    "EffectAttemptFoldNotFound",
    "EffectAttemptFoldResult",
    "ExistingFold",
    "FoldEffectAttempt",
    "NewlyFolded",
]
