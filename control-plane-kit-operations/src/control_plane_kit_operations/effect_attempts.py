"""Exact Operations records for one Core effect attempt."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import re

from control_plane_kit_core.operations import (
    ActivityEventKind,
    EffectAttemptFence,
    EffectAttemptIdentity,
    EffectAttemptState,
    EffectAttemptStatus,
    EffectRecoveryDecision,
    EffectRecoveryResolution,
    FailureCategory,
    RunId,
)

from .records import (
    ActivityEventRecord,
    BoundedEvidence,
    FailureEvidence,
    OperationsRecordError,
)


_MAX_EFFECT_ATTEMPT = 2_147_483_647
_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")


@dataclass(frozen=True)
class EffectAttemptEventEvidence:
    """Bounded event commitment to one exact effect-attempt state."""

    attempt: int
    state_fingerprint: str

    def __post_init__(self) -> None:
        if (
            type(self.attempt) is not int
            or not 1 <= self.attempt <= _MAX_EFFECT_ATTEMPT
            or type(self.state_fingerprint) is not str
            or _SHA256_PATTERN.fullmatch(self.state_fingerprint) is None
        ):
            raise OperationsRecordError(
                "effect attempt event evidence is invalid"
            )

    def descriptor(self) -> dict[str, object]:
        return {
            "attempt": self.attempt,
            "state_fingerprint": self.state_fingerprint,
        }


def effect_attempt_state_fingerprint(state: EffectAttemptState) -> str:
    """Commit to the exact canonical descriptor of one attempt state."""

    if type(state) is not EffectAttemptState:
        raise OperationsRecordError("effect attempt state must be typed")
    canonical = json.dumps(
        state.descriptor(),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return sha256(canonical).hexdigest()


@dataclass(frozen=True)
class EffectAttemptRecord:
    """One exact current state with its immutable event commitments."""

    state: EffectAttemptState
    original_start_event: ActivityEventRecord
    latest_transition_event: ActivityEventRecord

    def __post_init__(self) -> None:
        if not _record_is_valid(self):
            raise OperationsRecordError("effect attempt record is invalid")


def _record_is_valid(record: EffectAttemptRecord) -> bool:
    state = record.state
    original = record.original_start_event
    latest = record.latest_transition_event
    if (
        not _state_is_exact(state)
        or not _event_is_exact(original)
        or not _event_is_exact(latest)
    ):
        return False

    if original.kind is ActivityEventKind.STEP_STARTED:
        compensation = False
    elif original.kind is ActivityEventKind.STEP_COMPENSATION_STARTED:
        compensation = True
    else:
        return False

    started_state = EffectAttemptState(
        identity=state.identity,
        request_fingerprint=state.request_fingerprint,
        fence=state.fence,
        status=EffectAttemptStatus.STARTED,
        prior_attempt=state.prior_attempt,
    )
    if not _event_commits_to(original, started_state, original.kind):
        return False

    expected_latest_kind = _EVENT_KIND_BY_STATE.get(
        (compensation, state.status, state.recovery_decision is not None)
    )
    if expected_latest_kind is None:
        return False
    if state.status is EffectAttemptStatus.STARTED:
        return latest == original
    return (
        latest.event_id != original.event_id
        and latest.ordinal > original.ordinal
        and _event_commits_to(latest, state, expected_latest_kind)
    )


def _state_is_exact(state: object) -> bool:
    if type(state) is not EffectAttemptState:
        return False
    return (
        _identity_is_exact(state.identity)
        and type(state.request_fingerprint) is str
        and _fence_is_exact(state.fence)
        and type(state.status) is EffectAttemptStatus
        and (
            state.outcome_fingerprint is None
            or type(state.outcome_fingerprint) is str
        )
        and (
            state.prior_attempt is None
            or _identity_is_exact(state.prior_attempt)
        )
        and (
            state.recovery_decision is None
            or _recovery_decision_is_exact(state.recovery_decision)
        )
    )


def _identity_is_exact(identity: object) -> bool:
    return (
        type(identity) is EffectAttemptIdentity
        and type(identity.run_id) is RunId
        and type(identity.run_id.value) is str
        and type(identity.activity_id) is str
        and type(identity.attempt) is int
    )


def _fence_is_exact(fence: object) -> bool:
    return (
        type(fence) is EffectAttemptFence
        and type(fence.worker_id) is str
        and type(fence.generation) is int
    )


def _recovery_decision_is_exact(decision: object) -> bool:
    return (
        type(decision) is EffectRecoveryDecision
        and type(decision.decision_id) is str
        and _identity_is_exact(decision.attempt_identity)
        and type(decision.resolution) is EffectRecoveryResolution
        and type(decision.uncertain_fingerprint) is str
        and type(decision.evidence_fingerprint) is str
    )


def _event_is_exact(event: object) -> bool:
    if type(event) is not ActivityEventRecord:
        return False
    return (
        _postgres_text_is_valid(event.event_id)
        and type(event.run_id) is str
        and type(event.ordinal) is int
        and 1 <= event.ordinal <= _MAX_EFFECT_ATTEMPT
        and type(event.kind) is ActivityEventKind
        and type(event.occurred_at) is str
        and (event.activity_id is None or type(event.activity_id) is str)
        and _bounded_evidence_is_exact(event.evidence)
        and (event.failure is None or _failure_is_exact(event.failure))
        and event.recovery is None
    )


def _postgres_text_is_valid(value: object) -> bool:
    if type(value) is not str:
        return False
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def _bounded_evidence_is_exact(evidence: object) -> bool:
    return (
        type(evidence) is BoundedEvidence
        and type(evidence.canonical_json) is str
    )


def _failure_is_exact(failure: object) -> bool:
    return (
        type(failure) is FailureEvidence
        and type(failure.category) is FailureCategory
        and type(failure.code) is str
        and type(failure.message) is str
        and _bounded_evidence_is_exact(failure.details)
    )


def _event_commits_to(
    event: ActivityEventRecord,
    state: EffectAttemptState,
    expected_kind: ActivityEventKind,
) -> bool:
    identity = state.identity
    expected_evidence = BoundedEvidence.from_mapping(
        {
            "effect_attempt": EffectAttemptEventEvidence(
                identity.attempt,
                effect_attempt_state_fingerprint(state),
            ).descriptor()
        }
    )
    return (
        event.kind is expected_kind
        and event.run_id == identity.run_id.value
        and event.activity_id == identity.activity_id
        and event.evidence == expected_evidence
    )


_EVENT_KIND_BY_STATE = {
    (False, EffectAttemptStatus.STARTED, False): ActivityEventKind.STEP_STARTED,
    (False, EffectAttemptStatus.SUCCEEDED, False): ActivityEventKind.STEP_SUCCEEDED,
    (False, EffectAttemptStatus.FAILED, False): ActivityEventKind.STEP_FAILED,
    (False, EffectAttemptStatus.UNSUPPORTED, False): (
        ActivityEventKind.STEP_UNSUPPORTED
    ),
    (False, EffectAttemptStatus.UNCERTAIN, False): ActivityEventKind.STEP_UNCERTAIN,
    (False, EffectAttemptStatus.SUCCEEDED, True): (
        ActivityEventKind.STEP_UNCERTAINTY_RESOLVED_SUCCEEDED
    ),
    (False, EffectAttemptStatus.FAILED, True): (
        ActivityEventKind.STEP_UNCERTAINTY_RESOLVED_FAILED
    ),
    (False, EffectAttemptStatus.ABANDONED, True): (
        ActivityEventKind.STEP_UNCERTAINTY_ABANDONED
    ),
    (True, EffectAttemptStatus.STARTED, False): (
        ActivityEventKind.STEP_COMPENSATION_STARTED
    ),
    (True, EffectAttemptStatus.SUCCEEDED, False): (
        ActivityEventKind.STEP_COMPENSATION_SUCCEEDED
    ),
    (True, EffectAttemptStatus.FAILED, False): (
        ActivityEventKind.STEP_COMPENSATION_FAILED
    ),
    (True, EffectAttemptStatus.UNSUPPORTED, False): (
        ActivityEventKind.STEP_COMPENSATION_UNSUPPORTED
    ),
    (True, EffectAttemptStatus.UNCERTAIN, False): (
        ActivityEventKind.STEP_COMPENSATION_UNCERTAIN
    ),
    (True, EffectAttemptStatus.SUCCEEDED, True): (
        ActivityEventKind.STEP_COMPENSATION_UNCERTAINTY_RESOLVED_SUCCEEDED
    ),
    (True, EffectAttemptStatus.FAILED, True): (
        ActivityEventKind.STEP_COMPENSATION_UNCERTAINTY_RESOLVED_FAILED
    ),
    (True, EffectAttemptStatus.ABANDONED, True): (
        ActivityEventKind.STEP_COMPENSATION_UNCERTAINTY_ABANDONED
    ),
}


__all__ = [
    "EffectAttemptEventEvidence",
    "EffectAttemptRecord",
    "effect_attempt_state_fingerprint",
]
