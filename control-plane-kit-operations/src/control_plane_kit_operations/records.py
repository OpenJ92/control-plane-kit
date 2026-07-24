"""Durable operations record shapes."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Mapping

from control_plane_kit_core.operations.commands import OperatorCommandKind
from control_plane_kit_core.operations.lifecycle import (
    ActivityEventKind,
    ActivityEventScope,
    ActivityRunStatus,
    ExecutionRequestStatus,
    FailureCategory,
    LifecycleOperationKind,
    activity_event_scope,
)
from control_plane_kit_core.planning import ActivityPlan
from control_plane_kit_core.planning import RiskLevel
from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_core.probe_intents import (
    EndpointContext,
    ProbeKind,
    ProbeOutcome,
    probe_outcome_is_valid,
)
from control_plane_kit_core.topology import DEFAULT_GRAPH_CODEC, DeploymentGraph
from control_plane_kit_core.types import WorkspaceLifecycle


class OperationsRecordError(ValueError):
    """Raised when a durable operations record is malformed."""


class OperationSessionStatus(StrEnum):
    """Closed lifecycle vocabulary for grouped operator intent."""

    OPEN = "open"
    CLOSED = "closed"
    CANCELLED = "cancelled"


class ActivityPlanStatus(StrEnum):
    """Closed lifecycle vocabulary for persisted activity plans."""

    PLANNED = "planned"
    SUPERSEDED = "superseded"
    CANCELLED = "cancelled"


class ApprovalDecisionKind(StrEnum):
    """Closed approval decision vocabulary."""

    APPROVED = "approved"
    REJECTED = "rejected"


class ObservationStatus(StrEnum):
    """Closed observation vocabulary without optimistic health inference."""

    STARTING = "starting"
    PROCESS_STARTED = "process_started"
    REACHABLE = "reachable"
    HEALTHY = "healthy"
    UNHEALTHY = "unhealthy"
    TIMED_OUT = "timed_out"
    VERIFIED = "verified"
    VERIFICATION_FAILED = "verification_failed"
    UNSUPPORTED = "unsupported"
    REJECTED = "rejected"
    MALFORMED = "malformed"
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


MAX_EVIDENCE_BYTES = 4096
MAX_EVIDENCE_DEPTH = 4
MAX_EVIDENCE_ITEMS = 32
MAX_EVIDENCE_TEXT = 512


@dataclass(frozen=True)
class WorkspaceRecord:
    """Workspace truth and graph pointers owned by operations."""

    workspace_id: str
    name: str
    lifecycle: WorkspaceLifecycle = WorkspaceLifecycle.CREATED
    current_graph_id: str | None = None
    desired_graph_id: str | None = None
    metadata: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _validate_text(self.workspace_id, "workspace_id")
        _validate_text(self.name, "name")
        if not isinstance(self.lifecycle, WorkspaceLifecycle):
            raise OperationsRecordError("workspace lifecycle must be WorkspaceLifecycle")
        _validate_optional_text(self.current_graph_id, "current_graph_id")
        _validate_optional_text(self.desired_graph_id, "desired_graph_id")
        if not isinstance(self.metadata, Mapping):
            raise OperationsRecordError("workspace metadata must be mapping")


@dataclass(frozen=True)
class GraphVersionRecord:
    """One immutable graph descriptor version owned by a workspace."""

    graph_id: str
    workspace_id: str
    version: int
    graph_descriptor: Mapping[str, object]
    created_by: str
    created_at: str
    metadata: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _validate_text(self.graph_id, "graph_id")
        _validate_text(self.workspace_id, "workspace_id")
        if type(self.version) is not int or self.version < 1:
            raise OperationsRecordError("graph version must be a positive integer")
        if not isinstance(self.graph_descriptor, Mapping):
            raise OperationsRecordError("graph_descriptor must be mapping")
        _validate_text(self.created_by, "created_by")
        _validate_text(self.created_at, "created_at")
        if not isinstance(self.metadata, Mapping):
            raise OperationsRecordError("graph metadata must be mapping")

    @classmethod
    def from_graph(
        cls,
        *,
        graph_id: str,
        workspace_id: str,
        version: int,
        graph: DeploymentGraph,
        created_by: str,
        created_at: str,
        metadata: Mapping[str, object] | None = None,
    ) -> "GraphVersionRecord":
        if not isinstance(graph, DeploymentGraph):
            raise OperationsRecordError("graph version requires DeploymentGraph")
        return cls(
            graph_id=graph_id,
            workspace_id=workspace_id,
            version=version,
            graph_descriptor=DEFAULT_GRAPH_CODEC.encode(graph),
            created_by=created_by,
            created_at=created_at,
            metadata={} if metadata is None else metadata,
        )


@dataclass(frozen=True)
class OperationSessionRecord:
    """Grouped operator intent before planning or execution."""

    session_id: str
    workspace_id: str
    actor_id: str
    title: str
    status: OperationSessionStatus
    created_at: str
    closed_at: str | None = None
    metadata: Mapping[str, object] = field(default_factory=dict)
    idempotency_key: str | None = None
    intent_fingerprint: str | None = None

    def __post_init__(self) -> None:
        _validate_text(self.session_id, "session_id")
        _validate_text(self.workspace_id, "workspace_id")
        _validate_text(self.actor_id, "actor_id")
        _validate_text(self.title, "title")
        if not isinstance(self.status, OperationSessionStatus):
            raise OperationsRecordError(
                "operation session status must be OperationSessionStatus"
            )
        _validate_text(self.created_at, "created_at")
        _validate_optional_text(self.closed_at, "closed_at")
        _validate_optional_text(self.idempotency_key, "idempotency_key")
        _validate_optional_text(self.intent_fingerprint, "intent_fingerprint")
        if not isinstance(self.metadata, Mapping):
            raise OperationsRecordError("operation session metadata must be mapping")
        if self.status is OperationSessionStatus.OPEN and self.closed_at is not None:
            raise OperationsRecordError("open operation sessions must not have closed_at")
        if self.status is not OperationSessionStatus.OPEN and self.closed_at is None:
            raise OperationsRecordError(
                "terminal operation sessions require closed_at"
            )


@dataclass(frozen=True)
class OperationActionRecord:
    """One ordered operator action inside a session."""

    action_id: str
    session_id: str
    ordinal: int
    action_type: OperatorCommandKind | LifecycleOperationKind
    actor_id: str
    payload: Mapping[str, object] = field(default_factory=dict)
    created_at: str = ""
    idempotency_key: str | None = None
    intent_fingerprint: str | None = None

    def __post_init__(self) -> None:
        _validate_text(self.action_id, "action_id")
        _validate_text(self.session_id, "session_id")
        if type(self.ordinal) is not int or self.ordinal < 1:
            raise OperationsRecordError("operation action ordinal must be positive")
        if not isinstance(self.action_type, (OperatorCommandKind, LifecycleOperationKind)):
            raise OperationsRecordError(
                "operation action type must be a closed command or lifecycle kind"
            )
        _validate_text(self.actor_id, "actor_id")
        if not isinstance(self.payload, Mapping):
            raise OperationsRecordError("operation action payload must be mapping")
        _validate_text(self.created_at, "created_at")
        _validate_optional_text(self.idempotency_key, "idempotency_key")
        _validate_optional_text(self.intent_fingerprint, "intent_fingerprint")


@dataclass(frozen=True)
class ActivityPlanRecord:
    """Persisted, inspectable plan before execution."""

    plan_id: str
    session_id: str
    base_graph_id: str
    desired_graph_id: str
    status: ActivityPlanStatus
    created_at: str
    plan: ActivityPlan

    def __post_init__(self) -> None:
        _validate_text(self.plan_id, "plan_id")
        _validate_text(self.session_id, "session_id")
        _validate_text(self.base_graph_id, "base_graph_id")
        _validate_text(self.desired_graph_id, "desired_graph_id")
        if not isinstance(self.status, ActivityPlanStatus):
            raise OperationsRecordError("activity plan status must be ActivityPlanStatus")
        _validate_text(self.created_at, "created_at")
        if not isinstance(self.plan, ActivityPlan):
            raise OperationsRecordError("activity plan record requires ActivityPlan")


@dataclass(frozen=True)
class ApprovalRequestRecord:
    """Immutable request for authority over one persisted plan."""

    request_id: str
    session_id: str
    plan_id: str
    requested_by: str
    requested_at: str
    required_scope: PolicyScope
    max_risk: RiskLevel
    destructive: bool
    comment: str | None = None
    idempotency_key: str | None = None
    intent_fingerprint: str | None = None

    def __post_init__(self) -> None:
        _validate_text(self.request_id, "request_id")
        _validate_text(self.session_id, "session_id")
        _validate_text(self.plan_id, "plan_id")
        _validate_text(self.requested_by, "requested_by")
        _validate_text(self.requested_at, "requested_at")
        if not isinstance(self.required_scope, PolicyScope):
            raise OperationsRecordError("approval request scope must be PolicyScope")
        if not isinstance(self.max_risk, RiskLevel):
            raise OperationsRecordError("approval request max_risk must be RiskLevel")
        if type(self.destructive) is not bool:
            raise OperationsRecordError("approval request destructive must be bool")
        _validate_optional_text(self.comment, "comment")
        _validate_optional_text(self.idempotency_key, "idempotency_key")
        _validate_optional_text(self.intent_fingerprint, "intent_fingerprint")


@dataclass(frozen=True)
class ApprovalDecisionRecord:
    """Immutable answer to exactly one approval request."""

    decision_id: str
    request_id: str
    actor_id: str
    decision: ApprovalDecisionKind
    scope: PolicyScope
    decided_at: str
    comment: str | None = None
    idempotency_key: str | None = None
    intent_fingerprint: str | None = None

    def __post_init__(self) -> None:
        _validate_text(self.decision_id, "decision_id")
        _validate_text(self.request_id, "request_id")
        _validate_text(self.actor_id, "actor_id")
        if not isinstance(self.decision, ApprovalDecisionKind):
            raise OperationsRecordError("approval decision must be ApprovalDecisionKind")
        if not isinstance(self.scope, PolicyScope):
            raise OperationsRecordError("approval decision scope must be PolicyScope")
        _validate_text(self.decided_at, "decided_at")
        _validate_optional_text(self.comment, "comment")
        _validate_optional_text(self.idempotency_key, "idempotency_key")
        _validate_optional_text(self.intent_fingerprint, "intent_fingerprint")


@dataclass(frozen=True)
class ExecutionIdempotency:
    """Scoped retry identity plus conflict fingerprint for execution admission."""

    key: str
    intent_fingerprint: str

    def __post_init__(self) -> None:
        _validate_text(self.key, "execution idempotency key")
        _validate_text(self.intent_fingerprint, "execution intent_fingerprint")


@dataclass(frozen=True)
class ClaimIdentity:
    """Worker ownership and bounded lease evidence for a claimed request."""

    worker_id: str
    claimed_at: str
    lease_expires_at: str

    def __post_init__(self) -> None:
        _validate_text(self.worker_id, "worker_id")
        _validate_text(self.claimed_at, "claimed_at")
        _validate_text(self.lease_expires_at, "lease_expires_at")


@dataclass(frozen=True)
class RetryIdentity:
    """Identity of an explicit run attempt."""

    attempt: int
    prior_run_id: str | None = None

    def __post_init__(self) -> None:
        if type(self.attempt) is not int or self.attempt < 1:
            raise OperationsRecordError("retry attempt must be a positive integer")
        if self.attempt == 1 and self.prior_run_id is not None:
            raise OperationsRecordError("first attempt cannot reference a prior run")
        if self.attempt > 1:
            _validate_text(self.prior_run_id, "prior_run_id")


@dataclass(frozen=True)
class AdmittedRun:
    """Run ownership by one durable execution request."""

    request_id: str

    def __post_init__(self) -> None:
        _validate_text(self.request_id, "request_id")


@dataclass(frozen=True)
class BoundedEvidence:
    """Canonical bounded JSON evidence safe for durable operations records."""

    canonical_json: str = "{}"

    @classmethod
    def from_mapping(
        cls,
        value: Mapping[str, object] | None = None,
    ) -> "BoundedEvidence":
        candidate = {} if value is None else dict(value)
        _validate_evidence(candidate, path="evidence", depth=0)
        canonical = json.dumps(candidate, sort_keys=True, separators=(",", ":"))
        if len(canonical.encode("utf-8")) > MAX_EVIDENCE_BYTES:
            raise OperationsRecordError(
                f"evidence must not exceed {MAX_EVIDENCE_BYTES} encoded bytes"
            )
        return cls(canonical)

    def __post_init__(self) -> None:
        try:
            value = json.loads(self.canonical_json)
        except (TypeError, ValueError) as error:
            raise OperationsRecordError("evidence must be canonical JSON") from error
        if not isinstance(value, dict):
            raise OperationsRecordError("evidence must encode an object")
        _validate_evidence(value, path="evidence", depth=0)
        canonical = json.dumps(value, sort_keys=True, separators=(",", ":"))
        if canonical != self.canonical_json:
            raise OperationsRecordError(
                "evidence JSON must be deterministic and canonical"
            )
        if len(canonical.encode("utf-8")) > MAX_EVIDENCE_BYTES:
            raise OperationsRecordError(
                f"evidence must not exceed {MAX_EVIDENCE_BYTES} encoded bytes"
            )

    def descriptor(self) -> dict[str, object]:
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
            raise OperationsRecordError("failure category must be FailureCategory")
        _validate_text(self.code, "failure code")
        _validate_text(self.message, "failure message")
        if len(self.message) > MAX_EVIDENCE_TEXT:
            raise OperationsRecordError(
                f"failure message must not exceed {MAX_EVIDENCE_TEXT} characters"
            )
        if not isinstance(self.details, BoundedEvidence):
            raise OperationsRecordError("failure details must be BoundedEvidence")


@dataclass(frozen=True)
class ExecutionRequestIdentity:
    """Stable ownership coordinates for one execution request."""

    request_id: str
    workspace_id: str
    session_id: str
    plan_id: str

    def __post_init__(self) -> None:
        _validate_text(self.request_id, "request_id")
        _validate_text(self.workspace_id, "workspace_id")
        _validate_text(self.session_id, "session_id")
        _validate_text(self.plan_id, "plan_id")


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
            raise OperationsRecordError("execution request identity must be typed")
        if not isinstance(self.status, ExecutionRequestStatus):
            raise OperationsRecordError(
                "execution request status must be ExecutionRequestStatus"
            )
        _validate_text(self.requested_by, "requested_by")
        _validate_text(self.requested_at, "requested_at")
        _validate_text(self.approval_request_id, "approval_request_id")
        _validate_text(self.approval_decision_id, "approval_decision_id")
        if not isinstance(self.idempotency, ExecutionIdempotency):
            raise OperationsRecordError("execution request idempotency must be typed")
        if self.status is ExecutionRequestStatus.CLAIMED:
            if not isinstance(self.claim, ClaimIdentity):
                raise OperationsRecordError(
                    "claimed execution request requires claim identity"
                )
        elif self.claim is not None:
            raise OperationsRecordError(
                "only a claimed execution request may carry a claim"
            )


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
        _validate_text(self.run_id, "run_id")
        _validate_text(self.plan_id, "plan_id")
        if not isinstance(self.admission, AdmittedRun):
            raise OperationsRecordError("activity run admission must be AdmittedRun")
        if not isinstance(self.retry, RetryIdentity):
            raise OperationsRecordError("activity run retry identity must be typed")
        if not isinstance(self.status, ActivityRunStatus):
            raise OperationsRecordError("activity run status must be ActivityRunStatus")
        _validate_text(self.created_at, "created_at")
        _validate_optional_text(self.started_at, "started_at")
        _validate_optional_text(self.settled_at, "settled_at")
        if not isinstance(self.metadata, BoundedEvidence):
            raise OperationsRecordError("activity run metadata must be BoundedEvidence")
        _validate_run_timing(self)


@dataclass(frozen=True)
class ActivityEventRecord:
    """One ordered canonical event used for history reconstruction."""

    event_id: str
    run_id: str
    ordinal: int
    kind: ActivityEventKind
    occurred_at: str
    activity_id: str | None = None
    evidence: BoundedEvidence = field(default_factory=BoundedEvidence)
    failure: FailureEvidence | None = None

    def __post_init__(self) -> None:
        _validate_text(self.event_id, "event_id")
        _validate_text(self.run_id, "run_id")
        if type(self.ordinal) is not int or self.ordinal < 1:
            raise OperationsRecordError("event ordinal must be a positive integer")
        if not isinstance(self.kind, ActivityEventKind):
            raise OperationsRecordError("activity event kind must be ActivityEventKind")
        _validate_text(self.occurred_at, "occurred_at")
        _validate_optional_text(self.activity_id, "activity_id")
        if not isinstance(self.evidence, BoundedEvidence):
            raise OperationsRecordError("activity event evidence must be BoundedEvidence")
        if self.failure is not None and not isinstance(
            self.failure,
            FailureEvidence,
        ):
            raise OperationsRecordError(
                "activity event failure must be FailureEvidence when present"
            )
        if activity_event_scope(self.kind) is ActivityEventScope.ACTIVITY:
            if self.activity_id is None:
                raise OperationsRecordError("step event requires activity_id")
        elif self.activity_id is not None:
            raise OperationsRecordError("run event must not carry activity_id")


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
        _validate_text(self.observation_id, "observation_id")
        _validate_text(self.workspace_id, "workspace_id")
        _validate_text(self.subject_id, "subject_id")
        if not isinstance(self.status, ObservationStatus):
            raise OperationsRecordError("observation status must be ObservationStatus")
        _validate_text(self.observed_at, "observed_at")
        if not isinstance(self.evidence, BoundedEvidence):
            raise OperationsRecordError("observation evidence must be BoundedEvidence")
        if not isinstance(self.freshness, ObservationFreshness):
            raise OperationsRecordError(
                "observation freshness must be ObservationFreshness"
            )
        _validate_optional_text(self.graph_id, "graph_id")
        if self.probe_kind is not None and not isinstance(self.probe_kind, ProbeKind):
            raise OperationsRecordError("observation probe_kind must be ProbeKind")
        if self.probe_outcome is not None and not isinstance(
            self.probe_outcome,
            ProbeOutcome,
        ):
            raise OperationsRecordError("observation probe_outcome must be ProbeOutcome")
        if self.endpoint_context is not None and not isinstance(
            self.endpoint_context,
            EndpointContext,
        ):
            raise OperationsRecordError(
                "observation endpoint_context must be EndpointContext"
            )
        correlated = (self.graph_id, self.probe_kind, self.probe_outcome)
        if any(value is not None for value in correlated) and any(
            value is None for value in correlated
        ):
            raise OperationsRecordError(
                "correlated observation requires graph, probe kind, and outcome"
            )
        if (
            self.probe_kind in (ProbeKind.PROCESS, ProbeKind.READINESS)
            and self.endpoint_context is not None
        ):
            raise OperationsRecordError(
                f"{self.probe_kind.value} observation cannot claim endpoint context"
            )
        if (
            self.probe_kind is not None
            and self.probe_outcome is not None
            and not probe_outcome_is_valid(self.probe_kind, self.probe_outcome)
        ):
            raise OperationsRecordError(
                f"{self.probe_outcome.value} is not a valid "
                f"{self.probe_kind.value} observation"
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


def _validate_run_timing(record: ActivityRunRecord) -> None:
    if record.status is ActivityRunStatus.CLAIMED and record.started_at is not None:
        raise OperationsRecordError("claimed runs must not carry started_at")
    if record.status in _STARTED_RUN_STATUSES and record.started_at is None:
        raise OperationsRecordError(f"{record.status.value} runs require started_at")
    if record.status in _SETTLED_RUN_STATUSES and record.settled_at is None:
        raise OperationsRecordError(f"{record.status.value} runs require settled_at")
    if record.status not in _SETTLED_RUN_STATUSES and record.settled_at is not None:
        raise OperationsRecordError(f"{record.status.value} runs must remain unsettled")


def _validate_evidence(value: object, *, path: str, depth: int) -> None:
    if depth > MAX_EVIDENCE_DEPTH:
        raise OperationsRecordError(
            f"evidence nesting must not exceed {MAX_EVIDENCE_DEPTH} levels"
        )
    if isinstance(value, dict):
        if len(value) > MAX_EVIDENCE_ITEMS:
            raise OperationsRecordError(
                f"{path} must not contain more than {MAX_EVIDENCE_ITEMS} fields"
            )
        for key, item in value.items():
            if not isinstance(key, str) or not key.strip():
                raise OperationsRecordError(f"{path} keys must be nonempty text")
            if _secret_shaped(key):
                raise OperationsRecordError(
                    f"{path}.{key} is secret-shaped and cannot enter durable evidence"
                )
            _validate_evidence(item, path=f"{path}.{key}", depth=depth + 1)
        return
    if isinstance(value, list):
        if len(value) > MAX_EVIDENCE_ITEMS:
            raise OperationsRecordError(
                f"{path} must not contain more than {MAX_EVIDENCE_ITEMS} items"
            )
        for index, item in enumerate(value):
            _validate_evidence(item, path=f"{path}[{index}]", depth=depth + 1)
        return
    if isinstance(value, str):
        if len(value) > MAX_EVIDENCE_TEXT:
            raise OperationsRecordError(
                f"{path} text must not exceed {MAX_EVIDENCE_TEXT} characters"
            )
        return
    if type(value) is float and not math.isfinite(value):
        raise OperationsRecordError(f"{path} must contain a finite number")
    if value is None or type(value) in {bool, int, float}:
        return
    raise OperationsRecordError(
        f"{path} contains unsupported evidence value {type(value).__name__}"
    )


def _secret_shaped(key: str) -> bool:
    normalized = key.lower().replace("-", "_")
    return any(
        marker in normalized
        for marker in ("password", "secret", "token", "credential", "private_key")
    )


def _validate_text(value: str, field: str) -> None:
    if not isinstance(value, str) or not value or len(value) > 512:
        raise OperationsRecordError(f"{field} must be nonempty bounded text")
    if any(ord(character) < 32 for character in value):
        raise OperationsRecordError(f"{field} must not contain control characters")


def _validate_optional_text(value: str | None, field: str) -> None:
    if value is None:
        return
    _validate_text(value, field)
