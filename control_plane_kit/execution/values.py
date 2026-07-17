"""Closed durable values for activity execution truth.

This module is pure.  It names execution facts but does not persist them or
perform runtime effects.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Mapping, TypeAlias

if TYPE_CHECKING:
    from control_plane_kit.execution.recovery import RecoveryDecisionRecord


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
    UNCOMPENSATED_FAILURE = "uncompensated_failure"
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
    STEP_UNSUPPORTED = "step_unsupported"
    STEP_UNCERTAIN = "step_uncertain"
    STEP_UNCERTAINTY_RESOLVED_SUCCEEDED = "step_uncertainty_resolved_succeeded"
    STEP_UNCERTAINTY_RESOLVED_FAILED = "step_uncertainty_resolved_failed"
    STEP_COMPENSATION_STARTED = "step_compensation_started"
    STEP_COMPENSATION_SUCCEEDED = "step_compensation_succeeded"
    STEP_COMPENSATION_FAILED = "step_compensation_failed"
    RECOVERY_DECISION_RECORDED = "recovery_decision_recorded"
    RUN_COMPENSATION_STARTED = "run_compensation_started"
    RUN_COMPENSATION_SUCCEEDED = "run_compensation_succeeded"
    RUN_COMPENSATION_FAILED = "run_compensation_failed"
    RUN_UNCOMPENSATED_FAILURE_ACCEPTED = "run_uncompensated_failure_accepted"
    RUN_SUCCEEDED = "run_succeeded"
    RUN_FAILED = "run_failed"
    RUN_CANCELLED = "run_cancelled"
    CURRENT_GRAPH_ADVANCED = "current_graph_advanced"


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


class ObservationStaleReason(StrEnum):
    """Closed reasons immutable evidence cannot describe current state."""

    RECORDED_STALE = "recorded-stale"
    UNCORRELATED = "uncorrelated"
    GRAPH_CHANGED = "graph-changed"
    EXPIRED = "expired"
    MALFORMED_TIMESTAMP = "malformed-timestamp"
    FUTURE_TIMESTAMP = "future-timestamp"


class ProbeKind(StrEnum):
    """Independent runtime facts; no constructor implies another."""

    PROCESS = "process"
    TRANSPORT = "transport"
    APPLICATION_HEALTH = "application-health"
    READINESS = "readiness"


class ProbeOutcome(StrEnum):
    """Closed exact outcomes retained with durable observations."""

    PROCESS_RUNNING = "process-running"
    PROCESS_STOPPED = "process-stopped"
    REACHABLE = "reachable"
    REFUSED = "refused"
    HEALTHY = "healthy"
    UNHEALTHY = "unhealthy"
    TIMED_OUT = "timed-out"
    MALFORMED = "malformed"
    UNKNOWN = "unknown"
    READY = "ready"
    NOT_READY = "not-ready"


class EndpointContext(StrEnum):
    """Reachability scope without retaining an endpoint address."""

    RUNTIME_PRIVATE = "runtime-private"
    HOST_LOCAL = "host-local"
    PUBLIC = "public"


def probe_outcome_is_valid(kind: ProbeKind, outcome: ProbeOutcome) -> bool:
    """Return whether an exact outcome belongs to its observation layer."""

    return outcome in {
        ProbeKind.PROCESS: frozenset(
            {ProbeOutcome.PROCESS_RUNNING, ProbeOutcome.PROCESS_STOPPED, ProbeOutcome.UNKNOWN}
        ),
        ProbeKind.TRANSPORT: frozenset(
            {
                ProbeOutcome.REACHABLE,
                ProbeOutcome.REFUSED,
                ProbeOutcome.TIMED_OUT,
                ProbeOutcome.UNKNOWN,
            }
        ),
        ProbeKind.APPLICATION_HEALTH: frozenset(
            {
                ProbeOutcome.HEALTHY,
                ProbeOutcome.UNHEALTHY,
                ProbeOutcome.REFUSED,
                ProbeOutcome.TIMED_OUT,
                ProbeOutcome.MALFORMED,
                ProbeOutcome.UNKNOWN,
            }
        ),
        ProbeKind.READINESS: frozenset(
            {ProbeOutcome.READY, ProbeOutcome.NOT_READY, ProbeOutcome.UNKNOWN}
        ),
    }[kind]


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
    """Current projection of one run over its authoritative event history."""

    run_id: str
    plan_id: str
    admission: AdmittedRun
    retry: RetryIdentity
    status: ActivityRunStatus
    created_at: str
    started_at: str | None = None
    settled_at: str | None = None
    metadata: BoundedEvidence = field(default_factory=BoundedEvidence)

    def __post_init__(self) -> None:
        _require_text_fields(
            self,
            excluded={
                "admission",
                "retry",
                "status",
                "started_at",
                "settled_at",
                "metadata",
            },
        )
        if not isinstance(self.admission, AdmittedRun):
            raise TypeError("activity run admission must be AdmittedRun")
        if not isinstance(self.retry, RetryIdentity):
            raise TypeError("activity run retry identity must be typed")
        if not isinstance(self.status, ActivityRunStatus):
            raise TypeError("activity run status must be ActivityRunStatus")
        if self.started_at is not None and not _is_text(self.started_at):
            raise ExecutionValueError("started_at must be non-empty text when present")
        if self.settled_at is not None and not _is_text(self.settled_at):
            raise ExecutionValueError("settled_at must be non-empty text when present")
        if not isinstance(self.metadata, BoundedEvidence):
            raise TypeError("activity run metadata must be BoundedEvidence")
        _validate_admitted_run_projection(self)


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
    recovery: "RecoveryDecisionRecord | None" = None

    def __post_init__(self) -> None:
        _require_text_fields(
            self,
            excluded={
                "ordinal",
                "kind",
                "activity_id",
                "evidence",
                "failure",
                "recovery",
            },
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
        from control_plane_kit.execution.recovery import RecoveryDecisionRecord

        if self.recovery is not None and not isinstance(
            self.recovery, RecoveryDecisionRecord
        ):
            raise TypeError("activity event recovery must be RecoveryDecisionRecord")
        if self.kind is ActivityEventKind.RECOVERY_DECISION_RECORDED:
            if self.recovery is None:
                raise ExecutionValueError(
                    "recovery decision event requires typed recovery evidence"
                )
        elif self.recovery is not None:
            raise ExecutionValueError(
                "only recovery decision events may carry recovery evidence"
            )
        if self.kind in _STEP_ACTIVITY_EVENT_KINDS:
            if self.activity_id is None:
                raise ExecutionValueError("step event requires activity_id")
        elif self.activity_id is not None:
            raise ExecutionValueError("run event must not carry activity_id")


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
    graph_id: str | None = None
    probe_kind: ProbeKind | None = None
    probe_outcome: ProbeOutcome | None = None
    endpoint_context: EndpointContext | None = None

    def __post_init__(self) -> None:
        _require_text_fields(
            self,
            excluded={
                "status",
                "evidence",
                "freshness",
                "graph_id",
                "probe_kind",
                "probe_outcome",
                "endpoint_context",
            },
        )
        if not isinstance(self.status, ObservationStatus):
            raise TypeError("observation status must be ObservationStatus")
        if not isinstance(self.evidence, BoundedEvidence):
            raise TypeError("observation evidence must be BoundedEvidence")
        if not isinstance(self.freshness, ObservationFreshness):
            raise TypeError("observation freshness must be ObservationFreshness")
        if self.graph_id is not None and not _is_text(self.graph_id):
            raise ExecutionValueError("observation graph_id must be non-empty text")
        if self.probe_kind is not None and not isinstance(self.probe_kind, ProbeKind):
            raise TypeError("observation probe_kind must be ProbeKind")
        if self.probe_outcome is not None and not isinstance(
            self.probe_outcome, ProbeOutcome
        ):
            raise TypeError("observation probe_outcome must be ProbeOutcome")
        if self.endpoint_context is not None and not isinstance(
            self.endpoint_context, EndpointContext
        ):
            raise TypeError("observation endpoint_context must be EndpointContext")
        correlated = (self.graph_id, self.probe_kind, self.probe_outcome)
        if any(value is not None for value in correlated) and any(
            value is None for value in correlated
        ):
            raise ExecutionValueError(
                "correlated observation requires graph, probe kind, and outcome"
            )
        if self.probe_kind is ProbeKind.PROCESS and self.endpoint_context is not None:
            raise ExecutionValueError("process observation cannot claim endpoint context")
        if (
            self.probe_kind is not None
            and self.probe_outcome is not None
            and not probe_outcome_is_valid(self.probe_kind, self.probe_outcome)
        ):
            raise ExecutionValueError(
                f"{self.probe_outcome.value} is not a valid "
                f"{self.probe_kind.value} observation"
            )


ExecutionDescriptorValue: TypeAlias = (
    ExecutionRequestRecord
    | ActivityRunRecord
    | ActivityEventRecord
    | ObservationRecord
    | ClaimIdentity
    | RetryIdentity
    | AdmittedRun
    | FailureEvidence
)


_STARTED_RUN_STATUSES = frozenset(
    {
        ActivityRunStatus.RUNNING,
        ActivityRunStatus.PAUSED,
        ActivityRunStatus.SUCCEEDED,
        ActivityRunStatus.FAILED,
        ActivityRunStatus.COMPENSATING,
        ActivityRunStatus.COMPENSATED,
        ActivityRunStatus.PARTIALLY_FAILED,
        ActivityRunStatus.UNCOMPENSATED_FAILURE,
    }
)
_SETTLED_RUN_STATUSES = frozenset(
    {
        ActivityRunStatus.SUCCEEDED,
        ActivityRunStatus.COMPENSATED,
        ActivityRunStatus.PARTIALLY_FAILED,
        ActivityRunStatus.UNCOMPENSATED_FAILURE,
        ActivityRunStatus.CANCELLED,
    }
)


_STEP_ACTIVITY_EVENT_KINDS = frozenset(
    {
        ActivityEventKind.STEP_STARTED,
        ActivityEventKind.STEP_SUCCEEDED,
        ActivityEventKind.STEP_FAILED,
        ActivityEventKind.STEP_UNSUPPORTED,
        ActivityEventKind.STEP_UNCERTAIN,
        ActivityEventKind.STEP_UNCERTAINTY_RESOLVED_SUCCEEDED,
        ActivityEventKind.STEP_UNCERTAINTY_RESOLVED_FAILED,
        ActivityEventKind.STEP_COMPENSATION_STARTED,
        ActivityEventKind.STEP_COMPENSATION_SUCCEEDED,
        ActivityEventKind.STEP_COMPENSATION_FAILED,
    }
)


def _validate_admitted_run_projection(run: ActivityRunRecord) -> None:
    if run.status is ActivityRunStatus.CLAIMED and run.started_at is not None:
        raise ExecutionValueError("claimed runs must not carry started_at")
    if run.status in _STARTED_RUN_STATUSES and run.started_at is None:
        raise ExecutionValueError(f"{run.status.value} runs require started_at")
    if run.status in _SETTLED_RUN_STATUSES and run.settled_at is None:
        raise ExecutionValueError(f"{run.status.value} runs require settled_at")
    if run.status not in _SETTLED_RUN_STATUSES and run.settled_at is not None:
        raise ExecutionValueError(
            f"{run.status.value} runs must remain unsettled"
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
