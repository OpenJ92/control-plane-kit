"""Exact Operations records for one Core effect attempt."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import re

from control_plane_kit_core.operations import (
    ActivityEventKind,
    EffectAttemptState,
    EffectAttemptStatus,
)

from .records import ActivityEventRecord, BoundedEvidence, OperationsRecordError


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
        type(state) is not EffectAttemptState
        or type(original) is not ActivityEventRecord
        or type(latest) is not ActivityEventRecord
        or type(original.evidence) is not BoundedEvidence
        or type(latest.evidence) is not BoundedEvidence
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
