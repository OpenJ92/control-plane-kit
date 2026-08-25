"""Durable operations record shapes."""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from enum import StrEnum
from hashlib import sha256
from typing import Mapping

from control_plane_kit_core.approval_subjects import (
    ActivityPlanApprovalSubject,
    ApprovalSubject,
    GatewayKeyRotationApprovalSubject,
)
from control_plane_kit_core.operations.commands import OperatorCommandKind
from control_plane_kit_core.operations.lifecycle import (
    ActivityEventKind,
    ActivityEventScope,
    ActivityRunStatus,
    ExecutionRequestStatus,
    FailureCategory,
    LifecycleOperationKind,
    RecoveryDecisionKind,
    activity_event_scope,
    canonical_execution_lifecycle_contract_set,
)
from control_plane_kit_core.operations import RunId
from control_plane_kit_core.planning import ActivityId, ActivityPlan, RiskLevel
from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_core.probe_intents import (
    EndpointContext,
    ProbeKind,
    ProbeOutcome,
    probe_outcome_is_valid,
)
from control_plane_kit_core.topology import DEFAULT_GRAPH_CODEC, DeploymentGraph
from control_plane_kit_core.types import WorkspaceLifecycle
from control_plane_kit_operations.execution_leases import ExecutionLeaseFence


class OperationsRecordError(ValueError):
    """Raised when a durable operations record is malformed."""


_EVENT_KINDS_PERMITTING_FAILURE = frozenset(
    event.kind
    for event in canonical_execution_lifecycle_contract_set().events
    if event.may_carry_failure
)


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
    current_realized_projection_id: str | None = None
    desired_realized_projection_id: str | None = None
    desired_graph_revision: int = 0

    def __post_init__(self) -> None:
        _validate_text(self.workspace_id, "workspace_id")
        _validate_text(self.name, "name")
        if not isinstance(self.lifecycle, WorkspaceLifecycle):
            raise OperationsRecordError("workspace lifecycle must be WorkspaceLifecycle")
        _validate_optional_text(self.current_graph_id, "current_graph_id")
        _validate_optional_text(self.desired_graph_id, "desired_graph_id")
        if not isinstance(self.metadata, Mapping):
            raise OperationsRecordError("workspace metadata must be mapping")
        _validate_optional_text(
            self.current_realized_projection_id,
            "current_realized_projection_id",
        )
        _validate_optional_text(
            self.desired_realized_projection_id,
            "desired_realized_projection_id",
        )
        if type(self.desired_graph_revision) is not int or self.desired_graph_revision < 0:
            raise OperationsRecordError(
                "workspace desired_graph_revision must be a nonnegative integer"
            )

    @property
    def current_lineage(self) -> "GraphProjectionLineage | None":
        if (
            self.current_graph_id is None
            or self.current_realized_projection_id is None
        ):
            return None
        return GraphProjectionLineage(
            self.current_graph_id,
            self.current_realized_projection_id,
        )

    @property
    def desired_lineage(self) -> "GraphProjectionLineage | None":
        if (
            self.desired_graph_id is None
            or self.desired_realized_projection_id is None
        ):
            return None
        return GraphProjectionLineage(
            self.desired_graph_id,
            self.desired_realized_projection_id,
        )


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


class RealizedGraphProjectionKind(StrEnum):
    """Closed reasons an authored graph has one executable projection."""

    IDENTITY = "identity"
    DELEGATION_VERIFIER = "delegation-verifier"


@dataclass(frozen=True)
class GraphProjectionLineage:
    """One authored graph identity paired with exact executable material."""

    authored_graph_id: str
    realized_projection_id: str

    def __post_init__(self) -> None:
        _validate_text(self.authored_graph_id, "authored_graph_id")
        _validate_text(self.realized_projection_id, "realized_projection_id")


@dataclass(frozen=True)
class RealizedGraphProjectionRecord:
    """One immutable executable projection derived from authored graph truth."""

    projection_id: str
    workspace_id: str
    source_authored_graph_id: str
    projection_kind: RealizedGraphProjectionKind
    projection_key: str
    projection_digest: str
    graph_descriptor: Mapping[str, object]
    created_by: str
    created_at: str

    def __post_init__(self) -> None:
        _validate_text(self.projection_id, "projection_id")
        _validate_text(self.workspace_id, "workspace_id")
        _validate_text(self.source_authored_graph_id, "source_authored_graph_id")
        if not isinstance(self.projection_kind, RealizedGraphProjectionKind):
            raise OperationsRecordError("realized projection kind must be closed")
        _validate_text(self.projection_key, "projection_key")
        if len(self.projection_key) > 256:
            raise OperationsRecordError("projection_key is too long")
        if (
            not isinstance(self.projection_digest, str)
            or len(self.projection_digest) != 64
            or any(value not in "0123456789abcdef" for value in self.projection_digest)
        ):
            raise OperationsRecordError("projection_digest must be lowercase sha256")
        if not isinstance(self.graph_descriptor, Mapping):
            raise OperationsRecordError("projection graph_descriptor must be mapping")
        try:
            graph = DEFAULT_GRAPH_CODEC.decode(self.graph_descriptor)
        except ValueError as error:
            raise OperationsRecordError(
                "projection graph_descriptor must be canonical"
            ) from error
        canonical = DEFAULT_GRAPH_CODEC.encode(graph)
        if canonical != self.graph_descriptor:
            raise OperationsRecordError(
                "projection graph_descriptor must be canonical"
            )
        expected_digest = _realized_projection_digest(
            workspace_id=self.workspace_id,
            source_authored_graph_id=self.source_authored_graph_id,
            projection_kind=self.projection_kind,
            projection_key=self.projection_key,
            graph_descriptor=canonical,
        )
        if self.projection_digest != expected_digest:
            raise OperationsRecordError(
                "projection_digest does not match realized graph material"
            )
        _validate_text(self.created_by, "created_by")
        _validate_text(self.created_at, "created_at")

    @classmethod
    def from_graph(
        cls,
        *,
        projection_id: str,
        workspace_id: str,
        source_authored_graph_id: str,
        projection_kind: RealizedGraphProjectionKind,
        projection_key: str,
        graph: DeploymentGraph,
        created_by: str,
        created_at: str,
    ) -> "RealizedGraphProjectionRecord":
        if not isinstance(graph, DeploymentGraph):
            raise OperationsRecordError(
                "realized graph projection requires DeploymentGraph"
            )
        descriptor = DEFAULT_GRAPH_CODEC.encode(graph)
        return cls(
            projection_id=projection_id,
            workspace_id=workspace_id,
            source_authored_graph_id=source_authored_graph_id,
            projection_kind=projection_kind,
            projection_key=projection_key,
            projection_digest=_realized_projection_digest(
                workspace_id=workspace_id,
                source_authored_graph_id=source_authored_graph_id,
                projection_kind=projection_kind,
                projection_key=projection_key,
                graph_descriptor=descriptor,
            ),
            graph_descriptor=descriptor,
            created_by=created_by,
            created_at=created_at,
        )

    @classmethod
    def identity_for_authored(
        cls,
        *,
        authored_record: GraphVersionRecord,
    ) -> "RealizedGraphProjectionRecord":
        if not isinstance(authored_record, GraphVersionRecord):
            raise OperationsRecordError(
                "identity projection requires GraphVersionRecord"
            )
        graph = DEFAULT_GRAPH_CODEC.decode(authored_record.graph_descriptor)
        descriptor = DEFAULT_GRAPH_CODEC.encode(graph)
        digest = _realized_projection_digest(
            workspace_id=authored_record.workspace_id,
            source_authored_graph_id=authored_record.graph_id,
            projection_kind=RealizedGraphProjectionKind.IDENTITY,
            projection_key="identity",
            graph_descriptor=descriptor,
        )
        return cls(
            projection_id=f"projection-{digest}",
            workspace_id=authored_record.workspace_id,
            source_authored_graph_id=authored_record.graph_id,
            projection_kind=RealizedGraphProjectionKind.IDENTITY,
            projection_key="identity",
            projection_digest=digest,
            graph_descriptor=descriptor,
            created_by=authored_record.created_by,
            created_at=authored_record.created_at,
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
class FailedRunCompensationRecord:
    """Durable admission coordinates for one exact compensation program."""

    program_id: str
    workspace_id: str
    request_id: str
    run_id: str
    plan_id: str
    session_id: str
    action_id: str
    event_id: str
    actor_id: str
    reason: str
    source_failure: FailureEvidence
    authority_reference_fingerprint: str
    command_fingerprint: str
    evidence_fingerprint: str
    program_fingerprint: str
    created_at: str

    def __post_init__(self) -> None:
        for name in (
            "program_id",
            "workspace_id",
            "request_id",
            "run_id",
            "plan_id",
            "session_id",
            "action_id",
            "event_id",
            "actor_id",
            "reason",
            "created_at",
        ):
            _validate_text(getattr(self, name), name)
        if type(self.source_failure) is not FailureEvidence:
            raise OperationsRecordError("source_failure must be FailureEvidence")
        for name in (
            "authority_reference_fingerprint",
            "command_fingerprint",
            "evidence_fingerprint",
            "program_fingerprint",
        ):
            value = getattr(self, name)
            if type(value) is not str or re.fullmatch(r"[0-9a-f]{64}", value) is None:
                raise OperationsRecordError(f"{name} must be a lowercase SHA-256")


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
    base_realized_projection_id: str | None = None
    desired_realized_projection_id: str | None = None
    desired_graph_revision: int = 0

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
        _validate_optional_text(
            self.base_realized_projection_id,
            "base_realized_projection_id",
        )
        _validate_optional_text(
            self.desired_realized_projection_id,
            "desired_realized_projection_id",
        )
        if type(self.desired_graph_revision) is not int or self.desired_graph_revision < 0:
            raise OperationsRecordError(
                "activity plan desired_graph_revision must be nonnegative"
            )

    @property
    def base_lineage(self) -> GraphProjectionLineage | None:
        if self.base_realized_projection_id is None:
            return None
        return GraphProjectionLineage(
            self.base_graph_id,
            self.base_realized_projection_id,
        )

    @property
    def desired_lineage(self) -> GraphProjectionLineage | None:
        if self.desired_realized_projection_id is None:
            return None
        return GraphProjectionLineage(
            self.desired_graph_id,
            self.desired_realized_projection_id,
        )


@dataclass(frozen=True)
class ApprovalRequestRecord:
    """Immutable request for authority over one closed persisted subject."""

    request_id: str
    session_id: str
    subject: ApprovalSubject
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
        if not isinstance(
            self.subject,
            (ActivityPlanApprovalSubject, GatewayKeyRotationApprovalSubject),
        ):
            raise OperationsRecordError(
                "approval request requires a closed ApprovalSubject"
            )
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

    @property
    def plan_id(self) -> str | None:
        """Compatibility projection for activity-plan approval consumers."""

        return getattr(self.subject, "plan_id", None)


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
    generation: int
    claimed_at: str
    lease_expires_at: str

    def __post_init__(self) -> None:
        _validate_text(self.worker_id, "worker_id")
        if (
            type(self.generation) is not int
            or self.generation < 1
            or self.generation > 2**63 - 1
        ):
            raise OperationsRecordError("claim generation is invalid")
        _validate_text(self.claimed_at, "claimed_at")
        _validate_text(self.lease_expires_at, "lease_expires_at")

    @property
    def fence(self) -> ExecutionLeaseFence:
        return ExecutionLeaseFence(self.worker_id, self.generation)


@dataclass(frozen=True)
class RetryIdentity:
    """Identity of an explicit run attempt."""

    attempt: int
    prior_run_id: str | None = None

    def __post_init__(self) -> None:
        if (
            type(self.attempt) is not int
            or self.attempt < 1
            or self.attempt > 2_147_483_647
        ):
            raise OperationsRecordError(
                "retry attempt must be an integer from 1 through 2147483647"
            )
        if self.attempt == 1 and self.prior_run_id is not None:
            raise OperationsRecordError("first attempt cannot reference a prior run")
        if self.attempt > 1:
            _validate_run_id(self.prior_run_id, "prior_run_id")


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


_RECOVERY_DECISIONS = frozenset(
    {
        RecoveryDecisionKind.RETRY_AS_NEW_RUN,
        RecoveryDecisionKind.RENEW_ACTIVE_CLAIM,
        RecoveryDecisionKind.RENEW_EXPIRED_CLAIM,
        RecoveryDecisionKind.TAKE_OVER_EXPIRED_CLAIM,
        RecoveryDecisionKind.ABANDON_EXPIRED_CLAIM,
    }
)


@dataclass(frozen=True)
class ExecutionLeaseRecoveryEvidence:
    """Bounded durable evidence for one accepted recovery decision."""

    decision_kind: RecoveryDecisionKind
    retained_run_id: RunId
    prior_fence: ExecutionLeaseFence
    replacement_fence: ExecutionLeaseFence | None

    def __post_init__(self) -> None:
        if (
            type(self.decision_kind) is not RecoveryDecisionKind
            or self.decision_kind not in _RECOVERY_DECISIONS
        ):
            raise OperationsRecordError("recovery decision kind is invalid")
        if type(self.retained_run_id) is not RunId:
            raise OperationsRecordError("recovery retained run identity is invalid")
        if type(self.prior_fence) is not ExecutionLeaseFence:
            raise OperationsRecordError("recovery prior fence is invalid")
        if self.replacement_fence is not None and type(
            self.replacement_fence
        ) is not ExecutionLeaseFence:
            raise OperationsRecordError("recovery replacement fence is invalid")
        self._validate_fence_transition()

    def _validate_fence_transition(self) -> None:
        replacement = self.replacement_fence
        if self.decision_kind is RecoveryDecisionKind.RETRY_AS_NEW_RUN:
            if replacement is None or replacement != self.prior_fence:
                raise OperationsRecordError(
                    "retry recovery fence transition is invalid"
                )
            return
        if self.decision_kind is RecoveryDecisionKind.ABANDON_EXPIRED_CLAIM:
            if replacement is not None:
                raise OperationsRecordError(
                    "claim abandonment must not carry a replacement fence"
                )
            return
        if replacement is None:
            raise OperationsRecordError(
                "claim recovery requires a replacement fence"
            )
        if (
            self.prior_fence.generation == 2**63 - 1
            or replacement.generation != self.prior_fence.generation + 1
        ):
            raise OperationsRecordError("claim recovery generation is invalid")
        takeover = (
            self.decision_kind
            is RecoveryDecisionKind.TAKE_OVER_EXPIRED_CLAIM
        )
        same_worker = replacement.worker_id == self.prior_fence.worker_id
        if takeover == same_worker:
            raise OperationsRecordError("claim recovery worker transition is invalid")

    def descriptor(self) -> dict[str, object]:
        return {
            "decision": self.decision_kind.value,
            "retained_run_id": self.retained_run_id.value,
            "prior_fence": self.prior_fence.descriptor(),
            "replacement_fence": (
                None
                if self.replacement_fence is None
                else self.replacement_fence.descriptor()
            ),
        }


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
        _validate_run_id(self.run_id, "run_id")
        _validate_text(self.plan_id, "plan_id")
        if not isinstance(self.admission, AdmittedRun):
            raise OperationsRecordError("activity run admission must be AdmittedRun")
        if not isinstance(self.retry, RetryIdentity):
            raise OperationsRecordError("activity run retry identity must be typed")
        if self.retry.prior_run_id == self.run_id:
            raise OperationsRecordError("activity run retry identity is incongruent")
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
    recovery: ExecutionLeaseRecoveryEvidence | None = None

    def __post_init__(self) -> None:
        _validate_text(self.event_id, "event_id")
        _validate_run_id(self.run_id, "run_id")
        if type(self.ordinal) is not int or self.ordinal < 1:
            raise OperationsRecordError("event ordinal must be a positive integer")
        if not isinstance(self.kind, ActivityEventKind):
            raise OperationsRecordError("activity event kind must be ActivityEventKind")
        _validate_text(self.occurred_at, "occurred_at")
        _validate_optional_activity_id(self.activity_id)
        if not isinstance(self.evidence, BoundedEvidence):
            raise OperationsRecordError("activity event evidence must be BoundedEvidence")
        if self.failure is not None and not isinstance(
            self.failure,
            FailureEvidence,
        ):
            raise OperationsRecordError(
                "activity event failure must be FailureEvidence when present"
            )
        if self.kind is ActivityEventKind.RECOVERY_DECISION_RECORDED:
            if type(self.recovery) is not ExecutionLeaseRecoveryEvidence:
                raise OperationsRecordError(
                    "recovery decision event requires typed recovery evidence"
                )
            if self.recovery.retained_run_id.value != self.run_id:
                raise OperationsRecordError(
                    "recovery decision event run identity is incongruent"
                )
            if self.evidence != BoundedEvidence() or self.failure is not None:
                raise OperationsRecordError(
                    "recovery decision event carries contradictory evidence"
                )
        elif self.recovery is not None:
            raise OperationsRecordError(
                "only a recovery decision event may carry recovery evidence"
            )
        elif (
            self.failure is not None
            and self.kind not in _EVENT_KINDS_PERMITTING_FAILURE
        ):
            raise OperationsRecordError(
                "event kind does not permit failure evidence"
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


def _realized_projection_digest(
    *,
    workspace_id: str,
    source_authored_graph_id: str,
    projection_kind: RealizedGraphProjectionKind,
    projection_key: str,
    graph_descriptor: Mapping[str, object],
) -> str:
    document = {
        "workspace_id": workspace_id,
        "source_authored_graph_id": source_authored_graph_id,
        "projection_kind": projection_kind.value,
        "projection_key": projection_key,
        "graph_descriptor": graph_descriptor,
    }
    encoded = json.dumps(
        document,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return sha256(encoded).hexdigest()


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


def _validate_run_id(value: object, field: str) -> None:
    try:
        RunId(value)  # type: ignore[arg-type]
    except ValueError:
        pass
    else:
        return
    raise OperationsRecordError(f"{field} is malformed")


def _validate_optional_activity_id(value: object) -> None:
    if value is None:
        return
    try:
        ActivityId(value)  # type: ignore[arg-type]
    except ValueError:
        pass
    else:
        return
    raise OperationsRecordError("activity_id is malformed")
