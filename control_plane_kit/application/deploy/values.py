"""Closed values for the deployment application program.

These values classify intent and suspension points. They do not replace the
canonical graph, approval, run, event, or recovery records owned elsewhere.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TypeAlias

from control_plane_kit.effects import TimeoutPolicy
from control_plane_kit.topology import DeploymentGraph
from control_plane_kit.workflows import (
    ActivityPlanningResult,
    ApprovalDecisionResult,
    ApprovalRequestResult,
    DesiredGraphEditResult,
    CoordinatorStatus,
    CurrentGraphAdvancementResult,
    ExecutionAdmissionResult,
    ExecutionCoordinatorResult,
    ExecutionWorkerAuthority,
    ExternalReadinessAttestation,
    IdempotencyKey,
    OperationCommandResult,
    RunLifecycleResult,
)


@dataclass(frozen=True)
class InitialDeployment:
    """Construct a non-empty desired topology from an empty current graph."""

    current: DeploymentGraph
    desired: DeploymentGraph

    def __post_init__(self) -> None:
        _require_empty("current", self.current)
        _require_non_empty("desired", self.desired)


@dataclass(frozen=True)
class UpdateDeployment:
    """Replace one distinct topology without crossing the empty boundary."""

    current: DeploymentGraph
    desired: DeploymentGraph

    def __post_init__(self) -> None:
        _require_graph("current", self.current)
        _require_graph("desired", self.desired)
        if self.current == self.desired:
            raise ValueError("update deployment requires distinct graphs")
        if _is_empty(self.current) != _is_empty(self.desired):
            raise ValueError(
                "update deployment cannot cross the empty-topology boundary"
            )


@dataclass(frozen=True)
class TeardownDeployment:
    """Move from a non-empty topology to an empty desired topology."""

    current: DeploymentGraph
    desired: DeploymentGraph

    def __post_init__(self) -> None:
        _require_non_empty("current", self.current)
        _require_empty("desired", self.desired)


@dataclass(frozen=True)
class NoOpDeployment:
    """Represent an identical current and desired topology."""

    current: DeploymentGraph
    desired: DeploymentGraph

    def __post_init__(self) -> None:
        _require_graph("current", self.current)
        _require_graph("desired", self.desired)
        if self.current != self.desired:
            raise ValueError("no-op deployment requires identical graphs")


DeploymentTransition: TypeAlias = (
    InitialDeployment | UpdateDeployment | TeardownDeployment | NoOpDeployment
)


@dataclass(frozen=True)
class DeploymentPlanRequest:
    """Operator intent required to prepare one graph transition."""

    transition: DeploymentTransition
    workspace_id: str
    current_graph_id: str
    expected_desired_graph_id: str | None
    actor_id: str
    title: str
    approval_comment: str
    idempotency_prefix: str

    def __post_init__(self) -> None:
        _require_transition(self.transition)
        for name in (
            "workspace_id",
            "current_graph_id",
            "actor_id",
            "title",
            "approval_comment",
            "idempotency_prefix",
        ):
            _require_text(name, getattr(self, name))
        if self.expected_desired_graph_id is not None:
            _require_text("expected_desired_graph_id", self.expected_desired_graph_id)


@dataclass(frozen=True)
class DeploymentPreparation:
    """Canonical durable evidence produced before an approval decision."""

    request: DeploymentPlanRequest
    session: OperationCommandResult
    desired_graph: DesiredGraphEditResult
    plan: ActivityPlanningResult

    def __post_init__(self) -> None:
        if not isinstance(self.request, DeploymentPlanRequest):
            raise TypeError("request must be DeploymentPlanRequest")
        if not isinstance(self.session, OperationCommandResult):
            raise TypeError("session must be OperationCommandResult")
        if not isinstance(self.desired_graph, DesiredGraphEditResult):
            raise TypeError("desired_graph must be DesiredGraphEditResult")
        if not isinstance(self.plan, ActivityPlanningResult):
            raise TypeError("plan must be ActivityPlanningResult")
        session_id = self.session.session.session_id
        if self.desired_graph.action.session_id != session_id:
            raise ValueError("desired graph evidence belongs to another session")
        if self.plan.plan_record.session_id != session_id:
            raise ValueError("plan evidence belongs to another session")
        if self.plan.plan_record.desired_graph_id != self.desired_graph.graph_version.graph_id:
            raise ValueError("plan must target the prepared desired graph")


@dataclass(frozen=True)
class NoDeploymentChanges:
    """Preparation evidence for an empty ActivityPlan."""

    preparation: DeploymentPreparation

    def __post_init__(self) -> None:
        _require_preparation(self.preparation)
        if self.preparation.plan.plan_record.plan.activities:
            raise ValueError("no-changes result requires an empty activity plan")


@dataclass(frozen=True)
class DeploymentReviewBlocked:
    """Preparation evidence that cannot enter approval or execution."""

    preparation: DeploymentPreparation

    def __post_init__(self) -> None:
        _require_preparation(self.preparation)
        if self.preparation.plan.plan_record.plan.ready_for_execution:
            raise ValueError("review-blocked result requires unresolved plan blockers")


@dataclass(frozen=True)
class ApprovalSuspension:
    """Durable boundary where deployment waits for an authorization decision."""

    preparation: DeploymentPreparation
    approval_request: ApprovalRequestResult

    def __post_init__(self) -> None:
        _require_preparation(self.preparation)
        if not isinstance(self.approval_request, ApprovalRequestResult):
            raise TypeError("approval_request must be ApprovalRequestResult")
        if self.approval_request.request.plan_id != self.preparation.plan.plan_record.plan_id:
            raise ValueError("approval request must target the prepared plan")


@dataclass(frozen=True)
class ApprovalGrant:
    """Explicit authority and intent supplied at the approval suspension."""

    actor_id: str
    actor_scopes: tuple[str, ...]
    idempotency_key: IdempotencyKey
    comment: str | None = None

    def __post_init__(self) -> None:
        _require_text("actor_id", self.actor_id)
        _require_scopes(self.actor_scopes)
        if not isinstance(self.idempotency_key, IdempotencyKey):
            raise TypeError("idempotency_key must be IdempotencyKey")


@dataclass(frozen=True)
class ApprovedDeployment:
    """Prepared deployment paired with its canonical approval decision."""

    suspension: ApprovalSuspension
    approval: ApprovalDecisionResult

    def __post_init__(self) -> None:
        if not isinstance(self.suspension, ApprovalSuspension):
            raise TypeError("suspension must be ApprovalSuspension")
        if not isinstance(self.approval, ApprovalDecisionResult):
            raise TypeError("approval must be ApprovalDecisionResult")
        if self.approval.request.request_id != self.suspension.approval_request.request.request_id:
            raise ValueError("approval decision must answer the suspended request")


@dataclass(frozen=True)
class AdmissionGrant:
    """Explicit operator authority and readiness evidence for admission."""

    actor_id: str
    actor_scopes: tuple[str, ...]
    idempotency_key: IdempotencyKey
    readiness: tuple[ExternalReadinessAttestation, ...] = ()

    def __post_init__(self) -> None:
        _require_text("actor_id", self.actor_id)
        _require_scopes(self.actor_scopes)
        if not isinstance(self.idempotency_key, IdempotencyKey):
            raise TypeError("idempotency_key must be IdempotencyKey")
        if not isinstance(self.readiness, tuple) or not all(
            isinstance(value, ExternalReadinessAttestation) for value in self.readiness
        ):
            raise TypeError("readiness must be a tuple of ExternalReadinessAttestation")


@dataclass(frozen=True)
class AdmittedDeployment:
    """Approved deployment paired with canonical durable admission evidence."""

    approved: ApprovedDeployment
    admission: ExecutionAdmissionResult

    def __post_init__(self) -> None:
        if not isinstance(self.approved, ApprovedDeployment):
            raise TypeError("approved must be ApprovedDeployment")
        if not isinstance(self.admission, ExecutionAdmissionResult):
            raise TypeError("admission must be ExecutionAdmissionResult")
        plan_id = self.approved.suspension.preparation.plan.plan_record.plan_id
        if self.admission.request.identity.plan_id != plan_id:
            raise ValueError("execution admission must target the approved plan")


@dataclass(frozen=True)
class ClaimGrant:
    """Worker ownership and lease intent for one admitted execution request."""

    authority: ExecutionWorkerAuthority
    lease_expires_at: str
    claim_idempotency_key: IdempotencyKey
    start_idempotency_key: IdempotencyKey

    def __post_init__(self) -> None:
        if not isinstance(self.authority, ExecutionWorkerAuthority):
            raise TypeError("authority must be ExecutionWorkerAuthority")
        _require_text("lease_expires_at", self.lease_expires_at)
        if not isinstance(self.claim_idempotency_key, IdempotencyKey):
            raise TypeError("claim_idempotency_key must be IdempotencyKey")
        if not isinstance(self.start_idempotency_key, IdempotencyKey):
            raise TypeError("start_idempotency_key must be IdempotencyKey")


@dataclass(frozen=True)
class ClaimedDeployment:
    """Admitted work with separate canonical claim/open and start evidence."""

    admitted: AdmittedDeployment
    opened: RunLifecycleResult
    started: RunLifecycleResult
    authority: ExecutionWorkerAuthority

    def __post_init__(self) -> None:
        if not isinstance(self.admitted, AdmittedDeployment):
            raise TypeError("admitted must be AdmittedDeployment")
        if not isinstance(self.opened, RunLifecycleResult):
            raise TypeError("opened must be RunLifecycleResult")
        if not isinstance(self.started, RunLifecycleResult):
            raise TypeError("started must be RunLifecycleResult")
        if not isinstance(self.authority, ExecutionWorkerAuthority):
            raise TypeError("authority must be ExecutionWorkerAuthority")
        if self.opened.run.run_id != self.started.run.run_id:
            raise ValueError("claim/open and start evidence must identify one run")
        if self.opened.request.identity != self.admitted.admission.request.identity:
            raise ValueError("claim/open evidence belongs to another admission")


@dataclass(frozen=True)
class RecoverySuspension:
    """Durable boundary where execution waits for an operator recovery decision."""

    claimed: ClaimedDeployment
    execution: ExecutionCoordinatorResult

    def __post_init__(self) -> None:
        if not isinstance(self.claimed, ClaimedDeployment):
            raise TypeError("claimed must be ClaimedDeployment")
        if not isinstance(self.execution, ExecutionCoordinatorResult):
            raise TypeError("execution must be ExecutionCoordinatorResult")
        if self.execution.status in (
            CoordinatorStatus.COMPLETED,
            CoordinatorStatus.PROGRESSED,
            CoordinatorStatus.IN_FLIGHT,
        ):
            raise ValueError("recovery suspension requires an operator-recovery outcome")


@dataclass(frozen=True)
class ExecutionLimits:
    """Bounds for one coordinator invocation."""

    timeout: TimeoutPolicy = TimeoutPolicy()
    max_effects: int = 100

    def __post_init__(self) -> None:
        if not isinstance(self.timeout, TimeoutPolicy):
            raise TypeError("timeout must be TimeoutPolicy")
        if type(self.max_effects) is not int or self.max_effects < 1:
            raise ValueError("max_effects must be a positive integer")


@dataclass(frozen=True)
class ExecutedDeployment:
    """Canonical completed execution ready for guarded advancement."""

    claimed: ClaimedDeployment
    execution: ExecutionCoordinatorResult

    def __post_init__(self) -> None:
        if not isinstance(self.claimed, ClaimedDeployment):
            raise TypeError("claimed must be ClaimedDeployment")
        if not isinstance(self.execution, ExecutionCoordinatorResult):
            raise TypeError("execution must be ExecutionCoordinatorResult")
        if self.execution.status is not CoordinatorStatus.COMPLETED:
            raise ValueError("executed deployment requires completed coordinator evidence")


@dataclass(frozen=True)
class AdvancementGrant:
    """Explicit idempotency identity for publishing completed graph truth."""

    idempotency_key: IdempotencyKey

    def __post_init__(self) -> None:
        if not isinstance(self.idempotency_key, IdempotencyKey):
            raise TypeError("idempotency_key must be IdempotencyKey")


@dataclass(frozen=True)
class AdvancedDeployment:
    """Terminal deployment evidence after guarded current-graph advancement."""

    executed: ExecutedDeployment
    advancement: CurrentGraphAdvancementResult

    def __post_init__(self) -> None:
        if not isinstance(self.executed, ExecutedDeployment):
            raise TypeError("executed must be ExecutedDeployment")
        if not isinstance(self.advancement, CurrentGraphAdvancementResult):
            raise TypeError("advancement must be CurrentGraphAdvancementResult")
        preparation = self.executed.claimed.admitted.approved.suspension.preparation
        if self.advancement.plan_id != preparation.plan.plan_record.plan_id:
            raise ValueError("advancement belongs to another plan")
        if self.advancement.run_id != self.executed.execution.run.run_id:
            raise ValueError("advancement belongs to another run")


@dataclass(frozen=True)
class ExecutionContinuation:
    """Bounded progress that may be passed through `Execute` again."""

    claimed: ClaimedDeployment
    execution: ExecutionCoordinatorResult

    def __post_init__(self) -> None:
        if not isinstance(self.claimed, ClaimedDeployment):
            raise TypeError("claimed must be ClaimedDeployment")
        if not isinstance(self.execution, ExecutionCoordinatorResult):
            raise TypeError("execution must be ExecutionCoordinatorResult")
        if self.execution.status not in (
            CoordinatorStatus.PROGRESSED,
            CoordinatorStatus.IN_FLIGHT,
        ):
            raise ValueError("execution continuation requires bounded-progress evidence")


DeploymentSuspension: TypeAlias = ApprovalSuspension | RecoverySuspension
DeploymentExecutionResult: TypeAlias = (
    ExecutedDeployment | ExecutionContinuation | RecoverySuspension
)
DeploymentPreparationResult: TypeAlias = (
    ApprovalSuspension | NoDeploymentChanges | DeploymentReviewBlocked
)


def classify_transition(
    current: DeploymentGraph,
    desired: DeploymentGraph,
) -> DeploymentTransition:
    """Interpret two graph values as one closed deployment-transition form."""

    _require_graph("current", current)
    _require_graph("desired", desired)
    match (_is_empty(current), _is_empty(desired), current == desired):
        case (_, _, True):
            return NoOpDeployment(current, desired)
        case (True, False, False):
            return InitialDeployment(current, desired)
        case (False, True, False):
            return TeardownDeployment(current, desired)
        case (False, False, False):
            return UpdateDeployment(current, desired)
        case (True, True, False):
            # Empty graphs with different names still have a graph-name diff.
            return UpdateDeployment(current, desired)


def _require_transition(value: object) -> None:
    if not isinstance(
        value,
        InitialDeployment | UpdateDeployment | TeardownDeployment | NoOpDeployment,
    ):
        raise TypeError("transition must be a DeploymentTransition")


def _require_preparation(value: object) -> None:
    if not isinstance(value, DeploymentPreparation):
        raise TypeError("preparation must be DeploymentPreparation")


def _require_graph(name: str, value: object) -> None:
    if not isinstance(value, DeploymentGraph):
        raise TypeError(f"{name} must be DeploymentGraph")


def _require_text(name: str, value: object) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be non-empty text")


def _require_scopes(value: object) -> None:
    if not isinstance(value, tuple) or not value or not all(
        isinstance(scope, str) and scope.strip() for scope in value
    ):
        raise ValueError("actor_scopes must be a non-empty tuple of text")
    if len(value) != len(set(value)):
        raise ValueError("actor_scopes must not contain duplicates")


def _require_empty(name: str, graph: DeploymentGraph) -> None:
    _require_graph(name, graph)
    if not _is_empty(graph):
        raise ValueError(f"{name} must be an empty deployment graph")


def _require_non_empty(name: str, graph: DeploymentGraph) -> None:
    _require_graph(name, graph)
    if _is_empty(graph):
        raise ValueError(f"{name} must be a non-empty deployment graph")


def _is_empty(graph: DeploymentGraph) -> bool:
    return not graph.nodes and not graph.edges and not graph.runtimes
