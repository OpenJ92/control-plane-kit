"""Pure effect-attempt recovery contracts."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class InvalidEffectRecoveryContract(ValueError):
    """Raised when effect-attempt recovery contract data is incoherent."""


class EffectAttemptStatus(StrEnum):
    """Closed durable states for one external-effect attempt."""

    STARTED = "started"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    UNSUPPORTED = "unsupported"
    UNCERTAIN = "uncertain"
    ABANDONED = "abandoned"


class EffectAttemptTransitionKind(StrEnum):
    """Closed transition vocabulary for effect-attempt folding."""

    STARTED = "started"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    UNSUPPORTED = "unsupported"
    UNCERTAIN = "uncertain"
    RECONCILED = "reconciled"
    ABANDONED = "abandoned"


class EffectRecoveryResolution(StrEnum):
    """Closed outcomes available to explicit effect recovery."""

    SUCCEEDED = "succeeded"
    FAILED = "failed"
    ABANDONED = "abandoned"


@dataclass(frozen=True)
class EffectAttemptIdentity:
    run_id: str
    activity_id: str
    attempt: int


@dataclass(frozen=True)
class EffectAttemptFence:
    worker_id: str
    generation: int


@dataclass(frozen=True)
class EffectRecoveryDecision:
    decision_id: str
    attempt_identity: EffectAttemptIdentity
    resolution: EffectRecoveryResolution
    evidence_fingerprint: str


@dataclass(frozen=True)
class EffectAttemptTransition:
    kind: EffectAttemptTransitionKind
    identity: EffectAttemptIdentity
    request_fingerprint: str | None = None
    outcome_fingerprint: str | None = None
    prior_attempt: EffectAttemptIdentity | None = None
    recovery_decision: EffectRecoveryDecision | None = None


@dataclass(frozen=True)
class EffectAttemptState:
    identity: EffectAttemptIdentity
    request_fingerprint: str
    fence: EffectAttemptFence
    status: EffectAttemptStatus
    outcome_fingerprint: str | None = None
    prior_attempt: EffectAttemptIdentity | None = None
    recovery_decision: EffectRecoveryDecision | None = None


def fold_effect_attempt(
    state: EffectAttemptState | None,
    transition: EffectAttemptTransition,
    *,
    fence: EffectAttemptFence,
) -> EffectAttemptState:
    """Fold one durable transition under the active ownership fence."""

    raise InvalidEffectRecoveryContract("effect-attempt fold is not implemented")
