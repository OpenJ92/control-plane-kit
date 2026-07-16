"""Closed durable values for activity execution truth.

This module is pure.  It names execution facts but does not persist them or
perform runtime effects.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Mapping, TypeAlias


MAX_EVIDENCE_BYTES = 8_192
MAX_EVIDENCE_DEPTH = 6
MAX_EVIDENCE_ITEMS = 64
MAX_EVIDENCE_TEXT = 1_024

EvidenceScalar: TypeAlias = str | int | float | bool | None
EvidenceValue: TypeAlias = (
    EvidenceScalar | list["EvidenceValue"] | dict[str, "EvidenceValue"]
)


class ExecutionValueError(ValueError):
    """Raised when execution truth cannot be represented safely."""


class ExecutionRequestStatus(StrEnum):
    """Lifecycle of durable execution intent before an ActivityRun exists."""

    QUEUED = "queued"
    CLAIMED = "claimed"
    CANCELLED = "cancelled"


class ActivityRunStatus(StrEnum):
    """Closed lifecycle of one execution attempt."""

    CLAIMED = "claimed"
    RUNNING = "running"
    PAUSED = "paused"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    COMPENSATING = "compensating"
    COMPENSATED = "compensated"
    PARTIALLY_FAILED = "partially_failed"
    CANCELLED = "cancelled"


class ActivityEventKind(StrEnum):
    """Canonical event vocabulary shared by persistence and saga replay."""

    REQUEST_ADMITTED = "request_admitted"
    REQUEST_CLAIMED = "request_claimed"
    RUN_OPENED = "run_opened"
    RUN_STARTED = "run_started"
    RUN_PAUSED = "run_paused"
    RUN_RESUMED = "run_resumed"
    STEP_STARTED = "step_started"
    STEP_SUCCEEDED = "step_succeeded"
    STEP_FAILED = "step_failed"
    STEP_UNCERTAIN = "step_uncertain"
    COMPENSATION_STARTED = "compensation_started"
    COMPENSATION_SUCCEEDED = "compensation_succeeded"
    COMPENSATION_FAILED = "compensation_failed"
    RUN_SUCCEEDED = "run_succeeded"
    RUN_FAILED = "run_failed"
    RUN_CANCELLED = "run_cancelled"


class ObservationStatus(StrEnum):
    """Closed observation vocabulary without optimistic health inference."""

    STARTING = "starting"
    PROCESS_STARTED = "process_started"
    REACHABLE = "reachable"
    HEALTHY = "healthy"
    UNHEALTHY = "unhealthy"
    TIMED_OUT = "timed_out"
    UNKNOWN = "unknown"


class ObservationFreshness(StrEnum):
    """Whether an observation may still describe current runtime state."""

    FRESH = "fresh"
    STALE = "stale"


class FailureCategory(StrEnum):
    """Operator-relevant classification of execution failure evidence."""

    RETRYABLE = "retryable"
    TERMINAL = "terminal"
    UNCERTAIN = "uncertain"
    OPERATOR_REVIEW = "operator_review"


@dataclass(frozen=True)
class ExecutionRequestIdentity:
    """Stable ownership coordinates for one execution request."""

    request_id: str
    workspace_id: str
    session_id: str
    plan_id: str

    def __post_init__(self) -> None:
        _require_text_fields(self)


@dataclass(frozen=True)
class ExecutionIdempotency:
    """Scoped retry identity plus the fingerprint that detects conflicts."""

    key: str
    intent_fingerprint: str

    def __post_init__(self) -> None:
        _require_text_fields(self)


@dataclass(frozen=True)
class ClaimIdentity:
    """Worker ownership and bounded lease evidence for a claimed request."""

    worker_id: str
    claimed_at: str
    lease_expires_at: str

    def __post_init__(self) -> None:
        _require_text_fields(self)


@dataclass(frozen=True)
class RetryIdentity:
    """Identity of an explicit new attempt derived from a prior run."""

    attempt: int
    prior_run_id: str | None = None

    def __post_init__(self) -> None:
        if type(self.attempt) is not int or self.attempt < 1:
            raise ExecutionValueError("retry attempt must be a positive integer")
        if self.attempt == 1 and self.prior_run_id is not None:
            raise ExecutionValueError("first attempt cannot reference a prior run")
        if self.attempt > 1 and not _is_text(self.prior_run_id):
            raise ExecutionValueError("retry attempts require a prior run id")


@dataclass(frozen=True)
class AdmittedRun:
    """Run ownership by one durable execution request."""

    request_id: str

    def __post_init__(self) -> None:
        _require_text_fields(self)


@dataclass(frozen=True)
class LegacyImportedRun:
    """Truthful marker for pre-admission Roadmap 0007 history."""

    schema_version: int = 7

    def __post_init__(self) -> None:
        if self.schema_version != 7:
            raise ExecutionValueError("only Roadmap 0007 run history may be imported")


RunAdmission: TypeAlias = AdmittedRun | LegacyImportedRun


@dataclass(frozen=True)
class BoundedEvidence:
    """Canonical bounded JSON evidence safe for durable descriptors.

    Values are copied through deterministic JSON at construction, so callers
    cannot mutate durable evidence through an aliased dictionary.
    """

    canonical_json: str = "{}"

    @classmethod
    def from_mapping(cls, value: Mapping[str, object] | None = None) -> "BoundedEvidence":
        candidate = {} if value is None else dict(value)
        _validate_evidence(candidate, path="evidence", depth=0)
        canonical = json.dumps(candidate, sort_keys=True, separators=(",", ":"))
        if len(canonical.encode("utf-8")) > MAX_EVIDENCE_BYTES:
            raise ExecutionValueError(
                f"evidence must not exceed {MAX_EVIDENCE_BYTES} encoded bytes"
            )
        return cls(canonical)

    def __post_init__(self) -> None:
        try:
            value = json.loads(self.canonical_json)
        except (TypeError, ValueError) as error:
            raise ExecutionValueError("evidence must be canonical JSON") from error
        if not isinstance(value, dict):
            raise ExecutionValueError("evidence must encode an object")
        _validate_evidence(value, path="evidence", depth=0)
        canonical = json.dumps(value, sort_keys=True, separators=(",", ":"))
        if canonical != self.canonical_json:
            raise ExecutionValueError("evidence JSON must be deterministic and canonical")
        if len(canonical.encode("utf-8")) > MAX_EVIDENCE_BYTES:
            raise ExecutionValueError(
                f"evidence must not exceed {MAX_EVIDENCE_BYTES} encoded bytes"
            )

    def descriptor(self) -> dict[str, object]:
        """Return a fresh JSON object for descriptors and persistence."""

        return json.loads(self.canonical_json)


@dataclass(frozen=True)
class FailureEvidence:
    """Bounded failure evidence suitable for events and operator reads."""

    category: FailureCategory
    code: str
    message: str
    details: BoundedEvidence = field(default_factory=BoundedEvidence)

    def __post_init__(self) -> None:
        if not isinstance(self.category, FailureCategory):
            raise TypeError("failure category must be FailureCategory")
        if not _is_text(self.code):
            raise ExecutionValueError("failure code must be non-empty text")
        if not _is_text(self.message):
            raise ExecutionValueError("failure message must be non-empty text")
        if len(self.message) > MAX_EVIDENCE_TEXT:
            raise ExecutionValueError(
                f"failure message must not exceed {MAX_EVIDENCE_TEXT} characters"
            )
        if not isinstance(self.details, BoundedEvidence):
            raise TypeError("failure details must be BoundedEvidence")


@dataclass(frozen=True)
class ExecutionRequestRecord:
    """Durable admitted intent to execute one approved canonical plan."""

    identity: ExecutionRequestIdentity
    status: ExecutionRequestStatus
    requested_by: str
    requested_at: str
    approval_request_id: str
    approval_decision_id: str
    idempotency: ExecutionIdempotency
    claim: ClaimIdentity | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.identity, ExecutionRequestIdentity):
            raise TypeError("execution request identity must be typed")
        if not isinstance(self.status, ExecutionRequestStatus):
            raise TypeError("execution request status must be ExecutionRequestStatus")
        _require_text_fields(
            self,
            excluded={"identity", "status", "idempotency", "claim"},
        )
        if not isinstance(self.idempotency, ExecutionIdempotency):
            raise TypeError("execution request idempotency must be typed")
        if self.status is ExecutionRequestStatus.CLAIMED:
            if not isinstance(self.claim, ClaimIdentity):
                raise ExecutionValueError("claimed execution request requires claim identity")
        elif self.claim is not None:
            raise ExecutionValueError("only a claimed execution request may carry a claim")


@dataclass(frozen=True)
class ActivityRunRecord:
    """One durable execution attempt for an admitted request."""

    run_id: str
    plan_id: str
    admission: RunAdmission
    retry: RetryIdentity
    status: ActivityRunStatus
    created_at: str
    started_at: str | None = None
    finished_at: str | None = None
    metadata: BoundedEvidence = field(default_factory=BoundedEvidence)

    def __post_init__(self) -> None:
        _require_text_fields(
            self,
            excluded={
                "admission",
                "retry",
                "status",
                "started_at",
                "finished_at",
                "metadata",
            },
        )
        if not isinstance(self.admission, (AdmittedRun, LegacyImportedRun)):
            raise TypeError("activity run admission must be typed")
        if not isinstance(self.retry, RetryIdentity):
            raise TypeError("activity run retry identity must be typed")
        if not isinstance(self.status, ActivityRunStatus):
            raise TypeError("activity run status must be ActivityRunStatus")
        if self.started_at is not None and not _is_text(self.started_at):
            raise ExecutionValueError("started_at must be non-empty text when present")
        if self.finished_at is not None and not _is_text(self.finished_at):
            raise ExecutionValueError("finished_at must be non-empty text when present")
        if not isinstance(self.metadata, BoundedEvidence):
            raise TypeError("activity run metadata must be BoundedEvidence")


@dataclass(frozen=True)
class ActivityEventRecord:
    """One ordered canonical event used for history and saga reconstruction."""

    event_id: str
    run_id: str
    ordinal: int
    kind: ActivityEventKind
    occurred_at: str
    activity_id: str | None = None
    evidence: BoundedEvidence = field(default_factory=BoundedEvidence)
    failure: FailureEvidence | None = None

    def __post_init__(self) -> None:
        _require_text_fields(
            self,
            excluded={"ordinal", "kind", "activity_id", "evidence", "failure"},
        )
        if type(self.ordinal) is not int or self.ordinal < 1:
            raise ExecutionValueError("event ordinal must be a positive integer")
        if not isinstance(self.kind, ActivityEventKind):
            raise TypeError("activity event kind must be ActivityEventKind")
        if self.activity_id is not None and not _is_text(self.activity_id):
            raise ExecutionValueError("activity_id must be non-empty text when present")
        if not isinstance(self.evidence, BoundedEvidence):
            raise TypeError("activity event evidence must be BoundedEvidence")
        if self.failure is not None and not isinstance(self.failure, FailureEvidence):
            raise TypeError("activity event failure must be FailureEvidence when present")


@dataclass(frozen=True)
class ObservationRecord:
    """Observed runtime evidence kept separate from desired graph truth."""

    observation_id: str
    workspace_id: str
    subject_id: str
    status: ObservationStatus
    observed_at: str
    evidence: BoundedEvidence = field(default_factory=BoundedEvidence)
    freshness: ObservationFreshness = ObservationFreshness.FRESH

    def __post_init__(self) -> None:
        _require_text_fields(self, excluded={"status", "evidence", "freshness"})
        if not isinstance(self.status, ObservationStatus):
            raise TypeError("observation status must be ObservationStatus")
        if not isinstance(self.evidence, BoundedEvidence):
            raise TypeError("observation evidence must be BoundedEvidence")
        if not isinstance(self.freshness, ObservationFreshness):
            raise TypeError("observation freshness must be ObservationFreshness")


ExecutionDescriptorValue: TypeAlias = (
    ExecutionRequestRecord
    | ActivityRunRecord
    | ActivityEventRecord
    | ObservationRecord
    | ClaimIdentity
    | RetryIdentity
    | AdmittedRun
    | LegacyImportedRun
    | FailureEvidence
)


def _require_text_fields(value: object, *, excluded: set[str] | None = None) -> None:
    excluded = excluded or set()
    for name, field_value in vars(value).items():
        if name in excluded:
            continue
        if not _is_text(field_value):
            raise ExecutionValueError(f"{name} must be non-empty text")


def _is_text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _validate_evidence(value: object, *, path: str, depth: int) -> None:
    if depth > MAX_EVIDENCE_DEPTH:
        raise ExecutionValueError(
            f"evidence nesting must not exceed {MAX_EVIDENCE_DEPTH} levels"
        )
    if isinstance(value, dict):
        if len(value) > MAX_EVIDENCE_ITEMS:
            raise ExecutionValueError(
                f"{path} must not contain more than {MAX_EVIDENCE_ITEMS} fields"
            )
        for key, item in value.items():
            if not _is_text(key):
                raise ExecutionValueError(f"{path} keys must be non-empty text")
            if _secret_shaped(key):
                raise ExecutionValueError(
                    f"{path}.{key} is secret-shaped and cannot enter durable evidence"
                )
            _validate_evidence(item, path=f"{path}.{key}", depth=depth + 1)
        return
    if isinstance(value, list):
        if len(value) > MAX_EVIDENCE_ITEMS:
            raise ExecutionValueError(
                f"{path} must not contain more than {MAX_EVIDENCE_ITEMS} items"
            )
        for index, item in enumerate(value):
            _validate_evidence(item, path=f"{path}[{index}]", depth=depth + 1)
        return
    if isinstance(value, str):
        if len(value) > MAX_EVIDENCE_TEXT:
            raise ExecutionValueError(
                f"{path} text must not exceed {MAX_EVIDENCE_TEXT} characters"
            )
        return
    if type(value) is float and not math.isfinite(value):
        raise ExecutionValueError(f"{path} must contain a finite number")
    if value is None or type(value) in {bool, int, float}:
        return
    raise ExecutionValueError(
        f"{path} contains unsupported evidence value {type(value).__name__}"
    )


def _secret_shaped(key: str) -> bool:
    normalized = key.lower().replace("-", "_")
    return any(
        marker in normalized
        for marker in ("password", "secret", "token", "credential", "private_key")
    )


EMPTY_EVIDENCE = BoundedEvidence()
