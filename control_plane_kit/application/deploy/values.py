"""Closed values for the deployment application program.

These values classify intent and suspension points. They do not replace the
canonical graph, approval, run, event, or recovery records owned elsewhere.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TypeAlias

from control_plane_kit.topology import DeploymentGraph
from control_plane_kit.workflows import (
    ActivityPlanningResult,
    ApprovalDecisionResult,
    ApprovalRequestResult,
    DesiredGraphEditResult,
    ExecutionCoordinatorResult,
    IdempotencyKey,
    OperationCommandResult,
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
        if not isinstance(self.actor_scopes, tuple) or not self.actor_scopes or not all(
            isinstance(value, str) and value.strip() for value in self.actor_scopes
        ):
            raise ValueError("actor_scopes must be a non-empty tuple of text")
        if len(self.actor_scopes) != len(set(self.actor_scopes)):
            raise ValueError("actor_scopes must not contain duplicates")
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
class RecoverySuspension:
    """Durable boundary where execution waits for an operator recovery decision."""

    transition: DeploymentTransition
    execution: ExecutionCoordinatorResult

    def __post_init__(self) -> None:
        _require_transition(self.transition)
        if not isinstance(self.execution, ExecutionCoordinatorResult):
            raise TypeError("execution must be ExecutionCoordinatorResult")


DeploymentSuspension: TypeAlias = ApprovalSuspension | RecoverySuspension
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
