"""Pure admission, lifecycle, recovery, and advancement contracts."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Mapping

from control_plane_kit_core.operations.services import (
    ControlPlaneServiceRole,
    DeploymentProgramStage,
)


class InvalidExecutionLifecycleContract(ValueError):
    """Raised when execution lifecycle contract data is incoherent."""


class ExecutionRequestStatus(StrEnum):
    """Closed execution-request states before and during run ownership."""

    QUEUED = "queued"
    CLAIMED = "claimed"
    CANCELLED = "cancelled"
    ABANDONED = "abandoned"


class ActivityRunStatus(StrEnum):
    """Closed public lifecycle states for one admitted run."""

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


class ActivityEventScope(StrEnum):
    """Whether an event belongs to the run or a planned activity."""

    RUN = "run"
    ACTIVITY = "activity"


class ActivityEventKind(StrEnum):
    """Closed event vocabulary shared by operations and read projections."""

    REQUEST_ADMITTED = "request_admitted"
    REQUEST_CLAIMED = "request_claimed"
    REQUEST_CLAIM_RENEWED = "request_claim_renewed"
    REQUEST_CLAIM_TAKEN_OVER = "request_claim_taken_over"
    REQUEST_CLAIM_ABANDONED = "request_claim_abandoned"
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
    STEP_COMPENSATION_UNSUPPORTED = "step_compensation_unsupported"
    STEP_COMPENSATION_UNCERTAIN = "step_compensation_uncertain"
    STEP_COMPENSATION_UNCERTAINTY_RESOLVED_SUCCEEDED = (
        "step_compensation_uncertainty_resolved_succeeded"
    )
    STEP_COMPENSATION_UNCERTAINTY_RESOLVED_FAILED = (
        "step_compensation_uncertainty_resolved_failed"
    )
    RECOVERY_DECISION_RECORDED = "recovery_decision_recorded"
    RUN_COMPENSATION_STARTED = "run_compensation_started"
    RUN_COMPENSATION_SUCCEEDED = "run_compensation_succeeded"
    RUN_COMPENSATION_FAILED = "run_compensation_failed"
    RUN_UNCOMPENSATED_FAILURE_ACCEPTED = "run_uncompensated_failure_accepted"
    RUN_SUCCEEDED = "run_succeeded"
    RUN_FAILED = "run_failed"
    RUN_CANCELLED = "run_cancelled"
    CURRENT_GRAPH_ADVANCED = "current_graph_advanced"


class FailureCategory(StrEnum):
    """Closed failure categories suitable for operator-facing evidence."""

    RETRYABLE = "retryable"
    TERMINAL = "terminal"
    UNCERTAIN = "uncertain"
    OPERATOR_REVIEW = "operator_review"


class RecoveryScope(StrEnum):
    """Closed authorities for consequential recovery decisions."""

    OPERATE = "recovery:operate"
    RESOLVE_UNCERTAINTY = "recovery:resolve-uncertainty"
    COMPENSATE = "recovery:compensate"
    ACCEPT_LOSS = "recovery:accept-loss"
    RENEW_CLAIM = "recovery:renew-claim"
    TAKE_OVER_CLAIM = "recovery:take-over-claim"
    ABANDON_CLAIM = "recovery:abandon-claim"


class RecoveryDecisionKind(StrEnum):
    """Closed recovery choices without a recovery-service callback."""

    CONFIRM_EFFECT_SUCCEEDED = "confirm-effect-succeeded"
    CONFIRM_EFFECT_FAILED = "confirm-effect-failed"
    RESUME_SAME_INTENT = "resume-same-intent"
    RETRY_AS_NEW_RUN = "retry-as-new-run"
    BEGIN_COMPENSATION = "begin-compensation"
    ACCEPT_UNCOMPENSATED_FAILURE = "accept-uncompensated-failure"
    REMAIN_PAUSED = "remain-paused"
    RENEW_EXPIRED_CLAIM = "renew-expired-claim"
    TAKE_OVER_EXPIRED_CLAIM = "take-over-expired-claim"
    ABANDON_EXPIRED_CLAIM = "abandon-expired-claim"


class LifecycleOperationKind(StrEnum):
    """Closed public lifecycle operations."""

    ADMIT_EXECUTION = "admit-execution"
    CLAIM_RUN = "claim-run"
    START_RUN = "start-run"
    PAUSE_RUN = "pause-run"
    RESUME_RUN = "resume-run"
    COMPLETE_RUN = "complete-run"
    FAIL_RUN = "fail-run"
    COMPLETE_COMPENSATION = "complete-compensation"
    FAIL_COMPENSATION = "fail-compensation"
    CANCEL_RUN = "cancel-run"
    RECORD_RECOVERY_DECISION = "record-recovery-decision"
    ADVANCE_CURRENT_GRAPH = "advance-current-graph"


class ContractEnforcementOwner(StrEnum):
    """Where a lifecycle law is enforced."""

    CORE_CONTRACT = "core-contract"
    OPERATIONS = "operations"


@dataclass(frozen=True)
class RunStatusTimingContract:
    """Projection timing law for one run status."""

    status: ActivityRunStatus
    requires_started_at: bool
    requires_settled_at: bool

    def __post_init__(self) -> None:
        if not isinstance(self.status, ActivityRunStatus):
            raise InvalidExecutionLifecycleContract(
                "status must be ActivityRunStatus"
            )
        _validate_bool(self.requires_started_at, "requires_started_at")
        _validate_bool(self.requires_settled_at, "requires_settled_at")
        expected_started = self.status in _STARTED_RUN_STATUSES
        expected_settled = self.status in _SETTLED_RUN_STATUSES
        if self.requires_started_at is not expected_started:
            raise InvalidExecutionLifecycleContract(
                f"{self.status.value} has wrong started_at contract"
            )
        if self.requires_settled_at is not expected_settled:
            raise InvalidExecutionLifecycleContract(
                f"{self.status.value} has wrong settled_at contract"
            )

    def descriptor(self) -> dict[str, object]:
        return {
            "status": self.status.value,
            "requires_started_at": self.requires_started_at,
            "requires_settled_at": self.requires_settled_at,
        }

    @classmethod
    def from_descriptor(
        cls,
        value: Mapping[str, object],
    ) -> "RunStatusTimingContract":
        if set(value) != {"status", "requires_started_at", "requires_settled_at"}:
            raise InvalidExecutionLifecycleContract(
                "run status timing descriptor has unexpected keys"
            )
        try:
            return cls(
                status=ActivityRunStatus(_text(value["status"], "status")),
                requires_started_at=_bool(
                    value["requires_started_at"],
                    "requires_started_at",
                ),
                requires_settled_at=_bool(
                    value["requires_settled_at"],
                    "requires_settled_at",
                ),
            )
        except ValueError as error:
            raise InvalidExecutionLifecycleContract(str(error)) from error


@dataclass(frozen=True)
class ActivityEventContract:
    """One event shape without a journal or store implementation."""

    kind: ActivityEventKind
    scope: ActivityEventScope
    may_carry_failure: bool
    may_carry_recovery: bool

    def __post_init__(self) -> None:
        if not isinstance(self.kind, ActivityEventKind):
            raise InvalidExecutionLifecycleContract(
                "kind must be ActivityEventKind"
            )
        if not isinstance(self.scope, ActivityEventScope):
            raise InvalidExecutionLifecycleContract(
                "scope must be ActivityEventScope"
            )
        if self.scope is not activity_event_scope(self.kind):
            raise InvalidExecutionLifecycleContract(
                f"{self.kind.value} has wrong event scope"
            )
        _validate_bool(self.may_carry_failure, "may_carry_failure")
        _validate_bool(self.may_carry_recovery, "may_carry_recovery")
        if (
            self.may_carry_recovery
            and self.kind is not ActivityEventKind.RECOVERY_DECISION_RECORDED
        ):
            raise InvalidExecutionLifecycleContract(
                "only recovery decision events may carry recovery evidence"
            )

    def descriptor(self) -> dict[str, object]:
        return {
            "kind": self.kind.value,
            "scope": self.scope.value,
            "requires_activity_id": self.scope is ActivityEventScope.ACTIVITY,
            "may_carry_failure": self.may_carry_failure,
            "may_carry_recovery": self.may_carry_recovery,
        }

    @classmethod
    def from_descriptor(
        cls,
        value: Mapping[str, object],
    ) -> "ActivityEventContract":
        if set(value) != {
            "kind",
            "scope",
            "requires_activity_id",
            "may_carry_failure",
            "may_carry_recovery",
        }:
            raise InvalidExecutionLifecycleContract(
                "event contract descriptor has unexpected keys"
            )
        scope = ActivityEventScope(_text(value["scope"], "scope"))
        if _bool(value["requires_activity_id"], "requires_activity_id") is (
            scope is not ActivityEventScope.ACTIVITY
        ):
            raise InvalidExecutionLifecycleContract(
                "requires_activity_id must match event scope"
            )
        try:
            return cls(
                kind=ActivityEventKind(_text(value["kind"], "kind")),
                scope=scope,
                may_carry_failure=_bool(
                    value["may_carry_failure"],
                    "may_carry_failure",
                ),
                may_carry_recovery=_bool(
                    value["may_carry_recovery"],
                    "may_carry_recovery",
                ),
            )
        except ValueError as error:
            raise InvalidExecutionLifecycleContract(str(error)) from error


@dataclass(frozen=True)
class RecoveryDecisionContract:
    """One recovery choice and its pure precondition contract."""

    kind: RecoveryDecisionKind
    required_scope: RecoveryScope
    allowed_run_statuses: tuple[ActivityRunStatus, ...]
    requires_uncertainty: bool = False
    requires_no_uncertainty: bool = False
    requires_compensation_available: bool = False
    requires_intent_match: bool = True
    requires_expired_claim: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.kind, RecoveryDecisionKind):
            raise InvalidExecutionLifecycleContract(
                "kind must be RecoveryDecisionKind"
            )
        expected_scope = _RECOVERY_SCOPE_BY_KIND[self.kind]
        if self.required_scope is not expected_scope:
            raise InvalidExecutionLifecycleContract(
                f"{self.kind.value} requires {expected_scope.value} scope"
            )
        if not isinstance(self.allowed_run_statuses, tuple) or not all(
            isinstance(status, ActivityRunStatus)
            for status in self.allowed_run_statuses
        ):
            raise InvalidExecutionLifecycleContract(
                "allowed_run_statuses must be ActivityRunStatus values"
            )
        if not self.allowed_run_statuses:
            raise InvalidExecutionLifecycleContract(
                "recovery decision must declare allowed statuses"
            )
        _reject_duplicates("allowed_run_statuses", self.allowed_run_statuses)
        _validate_bool(self.requires_uncertainty, "requires_uncertainty")
        _validate_bool(self.requires_no_uncertainty, "requires_no_uncertainty")
        _validate_bool(
            self.requires_compensation_available,
            "requires_compensation_available",
        )
        _validate_bool(self.requires_intent_match, "requires_intent_match")
        _validate_bool(self.requires_expired_claim, "requires_expired_claim")
        if self.requires_uncertainty and self.requires_no_uncertainty:
            raise InvalidExecutionLifecycleContract(
                "recovery decision cannot require both uncertainty and no uncertainty"
            )
        if self.requires_expired_claim and self.kind not in _CLAIM_RECOVERY_KINDS:
            raise InvalidExecutionLifecycleContract(
                "only claim recovery decisions require expired claim"
            )

    def descriptor(self) -> dict[str, object]:
        return {
            "kind": self.kind.value,
            "required_scope": self.required_scope.value,
            "allowed_run_statuses": [
                status.value for status in self.allowed_run_statuses
            ],
            "requires_uncertainty": self.requires_uncertainty,
            "requires_no_uncertainty": self.requires_no_uncertainty,
            "requires_compensation_available": self.requires_compensation_available,
            "requires_intent_match": self.requires_intent_match,
            "requires_expired_claim": self.requires_expired_claim,
        }

    @classmethod
    def from_descriptor(
        cls,
        value: Mapping[str, object],
    ) -> "RecoveryDecisionContract":
        if set(value) != {
            "kind",
            "required_scope",
            "allowed_run_statuses",
            "requires_uncertainty",
            "requires_no_uncertainty",
            "requires_compensation_available",
            "requires_intent_match",
            "requires_expired_claim",
        }:
            raise InvalidExecutionLifecycleContract(
                "recovery decision descriptor has unexpected keys"
            )
        statuses = value["allowed_run_statuses"]
        if not isinstance(statuses, list):
            raise InvalidExecutionLifecycleContract(
                "allowed_run_statuses must be a list"
            )
        try:
            return cls(
                kind=RecoveryDecisionKind(_text(value["kind"], "kind")),
                required_scope=RecoveryScope(
                    _text(value["required_scope"], "required_scope")
                ),
                allowed_run_statuses=tuple(
                    ActivityRunStatus(_text(status, "allowed_run_status"))
                    for status in statuses
                ),
                requires_uncertainty=_bool(
                    value["requires_uncertainty"],
                    "requires_uncertainty",
                ),
                requires_no_uncertainty=_bool(
                    value["requires_no_uncertainty"],
                    "requires_no_uncertainty",
                ),
                requires_compensation_available=_bool(
                    value["requires_compensation_available"],
                    "requires_compensation_available",
                ),
                requires_intent_match=_bool(
                    value["requires_intent_match"],
                    "requires_intent_match",
                ),
                requires_expired_claim=_bool(
                    value["requires_expired_claim"],
                    "requires_expired_claim",
                ),
            )
        except ValueError as error:
            raise InvalidExecutionLifecycleContract(str(error)) from error


@dataclass(frozen=True)
class LifecycleOperationContract:
    """One public lifecycle operation and its operations-owned obligations."""

    operation_id: str
    kind: LifecycleOperationKind
    stage: DeploymentProgramStage
    service_role: ControlPlaneServiceRole
    request_schema: str
    response_schema: str
    accepted_run_statuses: tuple[ActivityRunStatus, ...]
    result_run_status: ActivityRunStatus | None
    event_kinds: tuple[ActivityEventKind, ...]
    requires_current_approval: bool
    requires_worker_scope: bool
    requires_current_graph_match: bool
    writes_current_graph: bool
    enforcement_owner: ContractEnforcementOwner = ContractEnforcementOwner.OPERATIONS

    def __post_init__(self) -> None:
        _validate_identity(self.operation_id, "operation_id")
        if not isinstance(self.kind, LifecycleOperationKind):
            raise InvalidExecutionLifecycleContract(
                "kind must be LifecycleOperationKind"
            )
        if not isinstance(self.stage, DeploymentProgramStage):
            raise InvalidExecutionLifecycleContract(
                "stage must be DeploymentProgramStage"
            )
        if not isinstance(self.service_role, ControlPlaneServiceRole):
            raise InvalidExecutionLifecycleContract(
                "service_role must be ControlPlaneServiceRole"
            )
        _validate_identity(self.request_schema, "request_schema")
        _validate_identity(self.response_schema, "response_schema")
        if not isinstance(self.accepted_run_statuses, tuple) or not all(
            isinstance(status, ActivityRunStatus)
            for status in self.accepted_run_statuses
        ):
            raise InvalidExecutionLifecycleContract(
                "accepted_run_statuses must be ActivityRunStatus values"
            )
        _reject_duplicates("accepted_run_statuses", self.accepted_run_statuses)
        if self.result_run_status is not None and not isinstance(
            self.result_run_status,
            ActivityRunStatus,
        ):
            raise InvalidExecutionLifecycleContract(
                "result_run_status must be ActivityRunStatus"
            )
        if not isinstance(self.event_kinds, tuple) or not all(
            isinstance(kind, ActivityEventKind)
            for kind in self.event_kinds
        ):
            raise InvalidExecutionLifecycleContract(
                "event_kinds must be ActivityEventKind values"
            )
        _reject_duplicates("event_kinds", self.event_kinds)
        _validate_bool(self.requires_current_approval, "requires_current_approval")
        _validate_bool(self.requires_worker_scope, "requires_worker_scope")
        _validate_bool(
            self.requires_current_graph_match,
            "requires_current_graph_match",
        )
        _validate_bool(self.writes_current_graph, "writes_current_graph")
        if not isinstance(self.enforcement_owner, ContractEnforcementOwner):
            raise InvalidExecutionLifecycleContract(
                "enforcement_owner must be ContractEnforcementOwner"
            )
        if self.enforcement_owner is not ContractEnforcementOwner.OPERATIONS:
            raise InvalidExecutionLifecycleContract(
                "durable lifecycle enforcement remains operations-owned"
            )
        if (
            self.writes_current_graph
            and self.kind is not LifecycleOperationKind.ADVANCE_CURRENT_GRAPH
        ):
            raise InvalidExecutionLifecycleContract(
                "only advancement may write current graph truth"
            )

    def descriptor(self) -> dict[str, object]:
        return {
            "operation_id": self.operation_id,
            "kind": self.kind.value,
            "stage": self.stage.value,
            "service_role": self.service_role.value,
            "request_schema": self.request_schema,
            "response_schema": self.response_schema,
            "accepted_run_statuses": [
                status.value for status in self.accepted_run_statuses
            ],
            "result_run_status": (
                None
                if self.result_run_status is None
                else self.result_run_status.value
            ),
            "event_kinds": [kind.value for kind in self.event_kinds],
            "requires_current_approval": self.requires_current_approval,
            "requires_worker_scope": self.requires_worker_scope,
            "requires_current_graph_match": self.requires_current_graph_match,
            "writes_current_graph": self.writes_current_graph,
            "enforcement_owner": self.enforcement_owner.value,
        }

    @classmethod
    def from_descriptor(
        cls,
        value: Mapping[str, object],
    ) -> "LifecycleOperationContract":
        if set(value) != {
            "operation_id",
            "kind",
            "stage",
            "service_role",
            "request_schema",
            "response_schema",
            "accepted_run_statuses",
            "result_run_status",
            "event_kinds",
            "requires_current_approval",
            "requires_worker_scope",
            "requires_current_graph_match",
            "writes_current_graph",
            "enforcement_owner",
        }:
            raise InvalidExecutionLifecycleContract(
                "lifecycle operation descriptor has unexpected keys"
            )
        statuses = value["accepted_run_statuses"]
        events = value["event_kinds"]
        if not isinstance(statuses, list) or not isinstance(events, list):
            raise InvalidExecutionLifecycleContract(
                "lifecycle operation descriptor uses invalid sequence fields"
            )
        result = value["result_run_status"]
        try:
            return cls(
                operation_id=_text(value["operation_id"], "operation_id"),
                kind=LifecycleOperationKind(_text(value["kind"], "kind")),
                stage=DeploymentProgramStage(_text(value["stage"], "stage")),
                service_role=ControlPlaneServiceRole(
                    _text(value["service_role"], "service_role")
                ),
                request_schema=_text(value["request_schema"], "request_schema"),
                response_schema=_text(value["response_schema"], "response_schema"),
                accepted_run_statuses=tuple(
                    ActivityRunStatus(_text(status, "accepted_run_status"))
                    for status in statuses
                ),
                result_run_status=(
                    None
                    if result is None
                    else ActivityRunStatus(_text(result, "result_run_status"))
                ),
                event_kinds=tuple(
                    ActivityEventKind(_text(kind, "event_kind"))
                    for kind in events
                ),
                requires_current_approval=_bool(
                    value["requires_current_approval"],
                    "requires_current_approval",
                ),
                requires_worker_scope=_bool(
                    value["requires_worker_scope"],
                    "requires_worker_scope",
                ),
                requires_current_graph_match=_bool(
                    value["requires_current_graph_match"],
                    "requires_current_graph_match",
                ),
                writes_current_graph=_bool(
                    value["writes_current_graph"],
                    "writes_current_graph",
                ),
                enforcement_owner=ContractEnforcementOwner(
                    _text(value["enforcement_owner"], "enforcement_owner")
                ),
            )
        except ValueError as error:
            raise InvalidExecutionLifecycleContract(str(error)) from error


@dataclass(frozen=True)
class ExecutionLifecycleContractSet:
    """Closed lifecycle vocabulary and operations-owned enforcement handoff."""

    timing: tuple[RunStatusTimingContract, ...]
    events: tuple[ActivityEventContract, ...]
    recovery_decisions: tuple[RecoveryDecisionContract, ...]
    operations: tuple[LifecycleOperationContract, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.timing, tuple) or not all(
            isinstance(item, RunStatusTimingContract)
            for item in self.timing
        ):
            raise InvalidExecutionLifecycleContract(
                "timing must contain RunStatusTimingContract values"
            )
        if not isinstance(self.events, tuple) or not all(
            isinstance(item, ActivityEventContract)
            for item in self.events
        ):
            raise InvalidExecutionLifecycleContract(
                "events must contain ActivityEventContract values"
            )
        if not isinstance(self.recovery_decisions, tuple) or not all(
            isinstance(item, RecoveryDecisionContract)
            for item in self.recovery_decisions
        ):
            raise InvalidExecutionLifecycleContract(
                "recovery_decisions must contain RecoveryDecisionContract values"
            )
        if not isinstance(self.operations, tuple) or not all(
            isinstance(item, LifecycleOperationContract)
            for item in self.operations
        ):
            raise InvalidExecutionLifecycleContract(
                "operations must contain LifecycleOperationContract values"
            )
        _require_exact_member_set(
            "timing",
            (item.status for item in self.timing),
            tuple(ActivityRunStatus),
        )
        _require_exact_member_set(
            "events",
            (item.kind for item in self.events),
            tuple(ActivityEventKind),
        )
        _require_exact_member_set(
            "recovery_decisions",
            (item.kind for item in self.recovery_decisions),
            tuple(RecoveryDecisionKind),
        )
        _require_exact_member_set(
            "operations",
            (item.kind for item in self.operations),
            tuple(LifecycleOperationKind),
        )
        object.__setattr__(
            self,
            "timing",
            tuple(sorted(self.timing, key=lambda item: item.status.value)),
        )
        object.__setattr__(
            self,
            "events",
            tuple(sorted(self.events, key=lambda item: item.kind.value)),
        )
        object.__setattr__(
            self,
            "recovery_decisions",
            tuple(
                sorted(
                    self.recovery_decisions,
                    key=lambda item: item.kind.value,
                )
            ),
        )
        object.__setattr__(
            self,
            "operations",
            tuple(sorted(self.operations, key=lambda item: item.operation_id)),
        )

    def event(self, kind: ActivityEventKind) -> ActivityEventContract:
        if not isinstance(kind, ActivityEventKind):
            raise InvalidExecutionLifecycleContract("kind must be ActivityEventKind")
        for event in self.events:
            if event.kind is kind:
                return event
        raise InvalidExecutionLifecycleContract(f"unknown event kind {kind.value!r}")

    def operation(self, operation_id: str) -> LifecycleOperationContract:
        _validate_identity(operation_id, "operation_id")
        for operation in self.operations:
            if operation.operation_id == operation_id:
                return operation
        raise InvalidExecutionLifecycleContract(
            f"unknown operation_id {operation_id!r}"
        )

    def recovery_decision(
        self,
        kind: RecoveryDecisionKind,
    ) -> RecoveryDecisionContract:
        if not isinstance(kind, RecoveryDecisionKind):
            raise InvalidExecutionLifecycleContract(
                "kind must be RecoveryDecisionKind"
            )
        for decision in self.recovery_decisions:
            if decision.kind is kind:
                return decision
        raise InvalidExecutionLifecycleContract(
            f"unknown recovery decision {kind.value!r}"
        )

    def descriptor(self) -> dict[str, object]:
        return {
            "kind": "execution-lifecycle-contract-set",
            "request_statuses": [status.value for status in ExecutionRequestStatus],
            "failure_categories": [category.value for category in FailureCategory],
            "timing": [item.descriptor() for item in self.timing],
            "events": [item.descriptor() for item in self.events],
            "recovery_decisions": [
                item.descriptor() for item in self.recovery_decisions
            ],
            "operations": [item.descriptor() for item in self.operations],
        }

    @classmethod
    def from_descriptor(
        cls,
        value: Mapping[str, object],
    ) -> "ExecutionLifecycleContractSet":
        if set(value) != {
            "kind",
            "request_statuses",
            "failure_categories",
            "timing",
            "events",
            "recovery_decisions",
            "operations",
        }:
            raise InvalidExecutionLifecycleContract(
                "execution lifecycle descriptor has unexpected keys"
            )
        if value["kind"] != "execution-lifecycle-contract-set":
            raise InvalidExecutionLifecycleContract(
                "execution lifecycle descriptor has wrong kind"
            )
        _check_closed_values(
            "request_statuses",
            value["request_statuses"],
            tuple(ExecutionRequestStatus),
        )
        _check_closed_values(
            "failure_categories",
            value["failure_categories"],
            tuple(FailureCategory),
        )
        return cls(
            tuple(
                RunStatusTimingContract.from_descriptor(_mapping(item, "timing"))
                for item in _list(value["timing"], "timing")
            ),
            tuple(
                ActivityEventContract.from_descriptor(_mapping(item, "event"))
                for item in _list(value["events"], "events")
            ),
            tuple(
                RecoveryDecisionContract.from_descriptor(
                    _mapping(item, "recovery_decision")
                )
                for item in _list(
                    value["recovery_decisions"],
                    "recovery_decisions",
                )
            ),
            tuple(
                LifecycleOperationContract.from_descriptor(
                    _mapping(item, "operation")
                )
                for item in _list(value["operations"], "operations")
            ),
        )


def activity_event_scope(kind: ActivityEventKind) -> ActivityEventScope:
    """Return the canonical scope for an event kind."""

    if not isinstance(kind, ActivityEventKind):
        raise InvalidExecutionLifecycleContract("kind must be ActivityEventKind")
    if kind in _STEP_EVENT_KINDS:
        return ActivityEventScope.ACTIVITY
    return ActivityEventScope.RUN


def canonical_execution_lifecycle_contract_set() -> ExecutionLifecycleContractSet:
    """Return the pure lifecycle contract for admission through advancement."""

    return ExecutionLifecycleContractSet(
        timing=tuple(
            RunStatusTimingContract(
                status,
                requires_started_at=status in _STARTED_RUN_STATUSES,
                requires_settled_at=status in _SETTLED_RUN_STATUSES,
            )
            for status in ActivityRunStatus
        ),
        events=tuple(
            ActivityEventContract(
                kind,
                activity_event_scope(kind),
                may_carry_failure=kind in _FAILURE_EVENT_KINDS,
                may_carry_recovery=kind is ActivityEventKind.RECOVERY_DECISION_RECORDED,
            )
            for kind in ActivityEventKind
        ),
        recovery_decisions=_canonical_recovery_decisions(),
        operations=_canonical_operations(),
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

_STEP_EVENT_KINDS = frozenset(
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
        ActivityEventKind.STEP_COMPENSATION_UNSUPPORTED,
        ActivityEventKind.STEP_COMPENSATION_UNCERTAIN,
        ActivityEventKind.STEP_COMPENSATION_UNCERTAINTY_RESOLVED_SUCCEEDED,
        ActivityEventKind.STEP_COMPENSATION_UNCERTAINTY_RESOLVED_FAILED,
    }
)

_FAILURE_EVENT_KINDS = frozenset(
    {
        ActivityEventKind.STEP_FAILED,
        ActivityEventKind.STEP_UNSUPPORTED,
        ActivityEventKind.STEP_UNCERTAIN,
        ActivityEventKind.STEP_UNCERTAINTY_RESOLVED_FAILED,
        ActivityEventKind.STEP_COMPENSATION_FAILED,
        ActivityEventKind.STEP_COMPENSATION_UNSUPPORTED,
        ActivityEventKind.STEP_COMPENSATION_UNCERTAIN,
        ActivityEventKind.STEP_COMPENSATION_UNCERTAINTY_RESOLVED_FAILED,
        ActivityEventKind.RUN_FAILED,
        ActivityEventKind.RUN_COMPENSATION_FAILED,
    }
)

_RECOVERY_SCOPE_BY_KIND = {
    RecoveryDecisionKind.CONFIRM_EFFECT_SUCCEEDED: RecoveryScope.RESOLVE_UNCERTAINTY,
    RecoveryDecisionKind.CONFIRM_EFFECT_FAILED: RecoveryScope.RESOLVE_UNCERTAINTY,
    RecoveryDecisionKind.RESUME_SAME_INTENT: RecoveryScope.OPERATE,
    RecoveryDecisionKind.RETRY_AS_NEW_RUN: RecoveryScope.OPERATE,
    RecoveryDecisionKind.BEGIN_COMPENSATION: RecoveryScope.COMPENSATE,
    RecoveryDecisionKind.ACCEPT_UNCOMPENSATED_FAILURE: RecoveryScope.ACCEPT_LOSS,
    RecoveryDecisionKind.REMAIN_PAUSED: RecoveryScope.OPERATE,
    RecoveryDecisionKind.RENEW_EXPIRED_CLAIM: RecoveryScope.RENEW_CLAIM,
    RecoveryDecisionKind.TAKE_OVER_EXPIRED_CLAIM: RecoveryScope.TAKE_OVER_CLAIM,
    RecoveryDecisionKind.ABANDON_EXPIRED_CLAIM: RecoveryScope.ABANDON_CLAIM,
}

_CLAIM_RECOVERY_KINDS = frozenset(
    {
        RecoveryDecisionKind.RENEW_EXPIRED_CLAIM,
        RecoveryDecisionKind.TAKE_OVER_EXPIRED_CLAIM,
        RecoveryDecisionKind.ABANDON_EXPIRED_CLAIM,
    }
)

def _canonical_recovery_decisions() -> tuple[RecoveryDecisionContract, ...]:
    return (
        RecoveryDecisionContract(
        RecoveryDecisionKind.CONFIRM_EFFECT_SUCCEEDED,
        RecoveryScope.RESOLVE_UNCERTAINTY,
        (ActivityRunStatus.PAUSED,),
        requires_uncertainty=True,
    ),
    RecoveryDecisionContract(
        RecoveryDecisionKind.CONFIRM_EFFECT_FAILED,
        RecoveryScope.RESOLVE_UNCERTAINTY,
        (ActivityRunStatus.PAUSED,),
        requires_uncertainty=True,
    ),
    RecoveryDecisionContract(
        RecoveryDecisionKind.RESUME_SAME_INTENT,
        RecoveryScope.OPERATE,
        (ActivityRunStatus.PAUSED,),
        requires_no_uncertainty=True,
    ),
    RecoveryDecisionContract(
        RecoveryDecisionKind.RETRY_AS_NEW_RUN,
        RecoveryScope.OPERATE,
        (ActivityRunStatus.FAILED,),
        requires_no_uncertainty=True,
    ),
    RecoveryDecisionContract(
        RecoveryDecisionKind.BEGIN_COMPENSATION,
        RecoveryScope.COMPENSATE,
        (ActivityRunStatus.FAILED,),
        requires_no_uncertainty=True,
        requires_compensation_available=True,
    ),
    RecoveryDecisionContract(
        RecoveryDecisionKind.ACCEPT_UNCOMPENSATED_FAILURE,
        RecoveryScope.ACCEPT_LOSS,
        (ActivityRunStatus.FAILED,),
        requires_no_uncertainty=True,
    ),
    RecoveryDecisionContract(
        RecoveryDecisionKind.REMAIN_PAUSED,
        RecoveryScope.OPERATE,
        (
            ActivityRunStatus.PAUSED,
            ActivityRunStatus.FAILED,
            ActivityRunStatus.PARTIALLY_FAILED,
        ),
        requires_intent_match=False,
    ),
    RecoveryDecisionContract(
        RecoveryDecisionKind.RENEW_EXPIRED_CLAIM,
        RecoveryScope.RENEW_CLAIM,
        (ActivityRunStatus.FAILED,),
        requires_expired_claim=True,
    ),
    RecoveryDecisionContract(
        RecoveryDecisionKind.TAKE_OVER_EXPIRED_CLAIM,
        RecoveryScope.TAKE_OVER_CLAIM,
        (ActivityRunStatus.FAILED,),
        requires_expired_claim=True,
    ),
    RecoveryDecisionContract(
        RecoveryDecisionKind.ABANDON_EXPIRED_CLAIM,
        RecoveryScope.ABANDON_CLAIM,
        (ActivityRunStatus.FAILED,),
        requires_expired_claim=True,
    ),
    )


def _canonical_operations() -> tuple[LifecycleOperationContract, ...]:
    return (
        LifecycleOperationContract(
        "execution.admit",
        LifecycleOperationKind.ADMIT_EXECUTION,
        DeploymentProgramStage.ADMIT,
        ControlPlaneServiceRole.ADMISSION,
        "AdmitExecutionRequest",
        "ExecutionAdmissionResult",
        (),
        None,
        (ActivityEventKind.REQUEST_ADMITTED,),
        requires_current_approval=True,
        requires_worker_scope=False,
        requires_current_graph_match=True,
        writes_current_graph=False,
    ),
    LifecycleOperationContract(
        "execution.claim",
        LifecycleOperationKind.CLAIM_RUN,
        DeploymentProgramStage.CLAIM,
        ControlPlaneServiceRole.LIFECYCLE,
        "ClaimAndOpenActivityRun",
        "ActivityRunClaimResult",
        (),
        ActivityRunStatus.CLAIMED,
        (ActivityEventKind.REQUEST_CLAIMED, ActivityEventKind.RUN_OPENED),
        requires_current_approval=True,
        requires_worker_scope=True,
        requires_current_graph_match=False,
        writes_current_graph=False,
    ),
    LifecycleOperationContract(
        "run.start",
        LifecycleOperationKind.START_RUN,
        DeploymentProgramStage.EXECUTE,
        ControlPlaneServiceRole.EXECUTION,
        "StartActivityRun",
        "ActivityRunTransitionResult",
        (ActivityRunStatus.CLAIMED,),
        ActivityRunStatus.RUNNING,
        (ActivityEventKind.RUN_STARTED,),
        requires_current_approval=True,
        requires_worker_scope=True,
        requires_current_graph_match=False,
        writes_current_graph=False,
    ),
    LifecycleOperationContract(
        "run.pause",
        LifecycleOperationKind.PAUSE_RUN,
        DeploymentProgramStage.EXECUTE,
        ControlPlaneServiceRole.EXECUTION,
        "PauseActivityRun",
        "ActivityRunTransitionResult",
        (ActivityRunStatus.RUNNING,),
        ActivityRunStatus.PAUSED,
        (ActivityEventKind.RUN_PAUSED,),
        requires_current_approval=True,
        requires_worker_scope=True,
        requires_current_graph_match=False,
        writes_current_graph=False,
    ),
    LifecycleOperationContract(
        "run.resume",
        LifecycleOperationKind.RESUME_RUN,
        DeploymentProgramStage.EXECUTE,
        ControlPlaneServiceRole.EXECUTION,
        "ResumeActivityRun",
        "ActivityRunTransitionResult",
        (ActivityRunStatus.PAUSED,),
        ActivityRunStatus.RUNNING,
        (ActivityEventKind.RUN_RESUMED,),
        requires_current_approval=True,
        requires_worker_scope=True,
        requires_current_graph_match=False,
        writes_current_graph=False,
    ),
    LifecycleOperationContract(
        "run.complete",
        LifecycleOperationKind.COMPLETE_RUN,
        DeploymentProgramStage.EXECUTE,
        ControlPlaneServiceRole.EXECUTION,
        "CompleteActivityRun",
        "ActivityRunTransitionResult",
        (ActivityRunStatus.RUNNING,),
        ActivityRunStatus.SUCCEEDED,
        (ActivityEventKind.RUN_SUCCEEDED,),
        requires_current_approval=True,
        requires_worker_scope=True,
        requires_current_graph_match=False,
        writes_current_graph=False,
    ),
    LifecycleOperationContract(
        "run.fail",
        LifecycleOperationKind.FAIL_RUN,
        DeploymentProgramStage.EXECUTE,
        ControlPlaneServiceRole.EXECUTION,
        "FailActivityRun",
        "ActivityRunTransitionResult",
        (ActivityRunStatus.RUNNING, ActivityRunStatus.PAUSED),
        ActivityRunStatus.FAILED,
        (ActivityEventKind.RUN_FAILED,),
        requires_current_approval=True,
        requires_worker_scope=True,
        requires_current_graph_match=False,
        writes_current_graph=False,
    ),
    LifecycleOperationContract(
        "compensation.complete",
        LifecycleOperationKind.COMPLETE_COMPENSATION,
        DeploymentProgramStage.EXECUTE,
        ControlPlaneServiceRole.EXECUTION,
        "CompleteActivityRunCompensation",
        "ActivityRunTransitionResult",
        (ActivityRunStatus.COMPENSATING,),
        ActivityRunStatus.COMPENSATED,
        (ActivityEventKind.RUN_COMPENSATION_SUCCEEDED,),
        requires_current_approval=True,
        requires_worker_scope=True,
        requires_current_graph_match=False,
        writes_current_graph=False,
    ),
    LifecycleOperationContract(
        "compensation.fail",
        LifecycleOperationKind.FAIL_COMPENSATION,
        DeploymentProgramStage.EXECUTE,
        ControlPlaneServiceRole.EXECUTION,
        "FailActivityRunCompensation",
        "ActivityRunTransitionResult",
        (ActivityRunStatus.COMPENSATING,),
        ActivityRunStatus.PARTIALLY_FAILED,
        (ActivityEventKind.RUN_COMPENSATION_FAILED,),
        requires_current_approval=True,
        requires_worker_scope=True,
        requires_current_graph_match=False,
        writes_current_graph=False,
    ),
    LifecycleOperationContract(
        "run.cancel",
        LifecycleOperationKind.CANCEL_RUN,
        DeploymentProgramStage.CLAIM,
        ControlPlaneServiceRole.LIFECYCLE,
        "CancelActivityRun",
        "ActivityRunTransitionResult",
        (
            ActivityRunStatus.CLAIMED,
            ActivityRunStatus.RUNNING,
            ActivityRunStatus.PAUSED,
        ),
        ActivityRunStatus.CANCELLED,
        (ActivityEventKind.RUN_CANCELLED,),
        requires_current_approval=True,
        requires_worker_scope=True,
        requires_current_graph_match=False,
        writes_current_graph=False,
    ),
    LifecycleOperationContract(
        "recovery.decide",
        LifecycleOperationKind.RECORD_RECOVERY_DECISION,
        DeploymentProgramStage.EXECUTE,
        ControlPlaneServiceRole.RECOVERY,
        "DecideActivityRunRecovery",
        "RecoveryDecisionResult",
        (
            ActivityRunStatus.PAUSED,
            ActivityRunStatus.FAILED,
            ActivityRunStatus.PARTIALLY_FAILED,
        ),
        None,
        (ActivityEventKind.RECOVERY_DECISION_RECORDED,),
        requires_current_approval=True,
        requires_worker_scope=True,
        requires_current_graph_match=False,
        writes_current_graph=False,
    ),
    LifecycleOperationContract(
        "graph.advance-current",
        LifecycleOperationKind.ADVANCE_CURRENT_GRAPH,
        DeploymentProgramStage.ADVANCE,
        ControlPlaneServiceRole.LIFECYCLE,
        "AdvanceCurrentGraph",
        "CurrentGraphAdvancementResult",
        (ActivityRunStatus.SUCCEEDED,),
        ActivityRunStatus.SUCCEEDED,
        (ActivityEventKind.CURRENT_GRAPH_ADVANCED,),
        requires_current_approval=True,
        requires_worker_scope=True,
        requires_current_graph_match=True,
        writes_current_graph=True,
    ),
    )


def _require_exact_member_set(
    field: str,
    actual_values: object,
    expected_values: tuple[object, ...],
) -> None:
    actual = tuple(actual_values)
    if set(actual) != set(expected_values) or len(actual) != len(expected_values):
        raise InvalidExecutionLifecycleContract(
            f"{field} must cover the canonical closed set"
        )


def _check_closed_values(
    field: str,
    value: object,
    expected_values: tuple[StrEnum, ...],
) -> None:
    if not isinstance(value, list):
        raise InvalidExecutionLifecycleContract(f"{field} must be a list")
    if value != [item.value for item in expected_values]:
        raise InvalidExecutionLifecycleContract(
            f"{field} must match the canonical closed values"
        )


def _reject_duplicates(field: str, values: object) -> None:
    sequence = tuple(values)
    if len(sequence) != len(set(sequence)):
        raise InvalidExecutionLifecycleContract(f"{field} values must be unique")


def _validate_identity(value: str, field: str) -> None:
    if not isinstance(value, str) or not value:
        raise InvalidExecutionLifecycleContract(f"{field} must be non-empty text")
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-")
    if any(character not in allowed for character in value):
        raise InvalidExecutionLifecycleContract(
            f"{field} must contain only letters, numbers, dots, dashes, or underscores"
        )


def _validate_bool(value: object, field: str) -> None:
    if type(value) is not bool:
        raise InvalidExecutionLifecycleContract(f"{field} must be bool")


def _bool(value: object, field: str) -> bool:
    if type(value) is not bool:
        raise InvalidExecutionLifecycleContract(f"{field} must be bool")
    return value


def _text(value: object, field: str) -> str:
    if not isinstance(value, str):
        raise InvalidExecutionLifecycleContract(f"{field} must be text")
    return value


def _list(value: object, field: str) -> list[object]:
    if not isinstance(value, list):
        raise InvalidExecutionLifecycleContract(f"{field} must be a list")
    return value


def _mapping(value: object, field: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise InvalidExecutionLifecycleContract(f"{field} must be a descriptor")
    return value
