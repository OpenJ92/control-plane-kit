"""Read-only durable context for reconstructing a deployment program."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from control_plane_kit.stores import (
    ActivityPlanRecord,
    ApprovalDecisionRecord,
    ApprovalRequestRecord,
    GraphVersionRecord,
    OperationActionKind,
    OperationActionRecord,
    OperationSessionRecord,
    PostgresUnitOfWork,
)


class DeploymentContextError(RuntimeError):
    """Raised when durable truth cannot reconstruct one deployment plan."""


@dataclass(frozen=True)
class DeploymentApprovalContext:
    """One approval request and its optional durable decision evidence."""

    request: ApprovalRequestRecord
    request_action: OperationActionRecord
    decision: ApprovalDecisionRecord | None = None
    decision_action: OperationActionRecord | None = None

    def __post_init__(self) -> None:
        if self.request_action.action_type is not OperationActionKind.APPROVAL_REQUESTED:
            raise DeploymentContextError(
                "approval context requires APPROVAL_REQUESTED action evidence"
            )
        if self.request_action.payload.get("request_id") != self.request.request_id:
            raise DeploymentContextError("approval request action identity is inconsistent")
        if (self.decision is None) != (self.decision_action is None):
            raise DeploymentContextError(
                "approval decision and action evidence must be present together"
            )
        if self.decision is not None:
            assert self.decision_action is not None
            if self.decision.request_id != self.request.request_id:
                raise DeploymentContextError("approval decision answers another request")
            if self.decision_action.action_type is not OperationActionKind.APPROVAL_DECIDED:
                raise DeploymentContextError(
                    "approval decision requires APPROVAL_DECIDED action evidence"
                )
            if self.decision_action.payload.get("decision_id") != self.decision.decision_id:
                raise DeploymentContextError("approval decision action identity is inconsistent")


@dataclass(frozen=True)
class DeploymentPlanContext:
    """One transactionally read plan and all evidence needed for resumption."""

    session: OperationSessionRecord
    session_action: OperationActionRecord
    plan: ActivityPlanRecord
    plan_action: OperationActionRecord
    base_graph: GraphVersionRecord
    desired_graph: GraphVersionRecord
    desired_graph_action: OperationActionRecord
    approvals: tuple[DeploymentApprovalContext, ...] = ()

    def __post_init__(self) -> None:
        if self.plan.session_id != self.session.session_id:
            raise DeploymentContextError("deployment plan belongs to another session")
        if self.base_graph.graph_id != self.plan.base_graph_id:
            raise DeploymentContextError("base graph does not match deployment plan")
        if self.desired_graph.graph_id != self.plan.desired_graph_id:
            raise DeploymentContextError("desired graph does not match deployment plan")
        actions = (
            self.session_action,
            self.plan_action,
            self.desired_graph_action,
        )
        if any(action.session_id != self.session.session_id for action in actions):
            raise DeploymentContextError("deployment action belongs to another session")
        if self.session_action.action_type is not OperationActionKind.SESSION_STARTED:
            raise DeploymentContextError("deployment context requires session-start evidence")
        if self.plan_action.payload.get("plan_id") != self.plan.plan_id:
            raise DeploymentContextError("plan action identity is inconsistent")
        if (
            self.desired_graph_action.payload.get("desired_graph_id")
            != self.desired_graph.graph_id
        ):
            raise DeploymentContextError("desired graph action identity is inconsistent")
        for approval in self.approvals:
            if (
                approval.request.session_id != self.session.session_id
                or approval.request.plan_id != self.plan.plan_id
            ):
                raise DeploymentContextError(
                    "approval request belongs to another deployment plan"
                )

    def approval(self, request_id: str) -> DeploymentApprovalContext:
        matches = tuple(
            approval
            for approval in self.approvals
            if approval.request.request_id == request_id
        )
        if len(matches) != 1:
            raise DeploymentContextError(
                f"plan {self.plan.plan_id!r} has no approval request {request_id!r}"
            )
        return matches[0]


UnitOfWorkFactory = Callable[[], PostgresUnitOfWork]


class DeploymentPlanContextQueryService:
    """Load one coherent deployment context in a read-only UnitOfWork."""

    def __init__(self, unit_of_work_factory: UnitOfWorkFactory) -> None:
        self._unit_of_work_factory = unit_of_work_factory

    def load(self, plan_id: str) -> DeploymentPlanContext:
        if not isinstance(plan_id, str) or not plan_id.strip():
            raise DeploymentContextError("plan_id must not be empty")
        with self._unit_of_work_factory() as work:
            history = work.stores.activity_history
            graphs = work.stores.graph_topology
            try:
                plan = history.get_plan(plan_id)
                session = history.get_session(plan.session_id)
                base_graph = graphs.get(plan.base_graph_id)
                desired_graph = graphs.get(plan.desired_graph_id)
            except KeyError as error:
                raise DeploymentContextError(
                    f"plan {plan_id!r} references missing durable truth"
                ) from error
            if (
                base_graph.workspace_id != session.workspace_id
                or desired_graph.workspace_id != session.workspace_id
            ):
                raise DeploymentContextError(
                    f"plan {plan_id!r} references graph truth outside its workspace"
                )
            actions = history.actions_for_session(session.session_id)
            session_action = _one_action(
                actions,
                OperationActionKind.SESSION_STARTED,
                lambda action: action.idempotency_key
                == _required_idempotency_key(session.idempotency_key),
                description="session start",
            )
            desired_graph_action = _one_action(
                actions,
                OperationActionKind.SET_DESIRED_GRAPH,
                lambda action: action.payload.get("desired_graph_id") == plan.desired_graph_id,
                description="desired graph",
            )
            plan_action = _one_action(
                actions,
                OperationActionKind.PLAN_REQUESTED,
                lambda action: action.payload.get("plan_id") == plan.plan_id,
                description="activity plan",
            )
            approvals = tuple(
                self._approval_context(history, actions, request)
                for request in history.approval_requests_for_session(session.session_id)
                if request.plan_id == plan.plan_id
            )
            return DeploymentPlanContext(
                session=session,
                session_action=session_action,
                plan=plan,
                plan_action=plan_action,
                base_graph=base_graph,
                desired_graph=desired_graph,
                desired_graph_action=desired_graph_action,
                approvals=tuple(
                    sorted(approvals, key=lambda value: value.request.request_id)
                ),
            )

    @staticmethod
    def _approval_context(
        history,
        actions: tuple[OperationActionRecord, ...],
        request: ApprovalRequestRecord,
    ) -> DeploymentApprovalContext:
        request_action = _one_action(
            actions,
            OperationActionKind.APPROVAL_REQUESTED,
            lambda action: action.payload.get("request_id") == request.request_id,
            description=f"approval request {request.request_id}",
        )
        decision = history.approval_decision_for_request(request.request_id)
        if decision is None:
            return DeploymentApprovalContext(request, request_action)
        decision_action = _one_action(
            actions,
            OperationActionKind.APPROVAL_DECIDED,
            lambda action: action.payload.get("decision_id") == decision.decision_id,
            description=f"approval decision {decision.decision_id}",
        )
        return DeploymentApprovalContext(
            request,
            request_action,
            decision,
            decision_action,
        )


def _one_action(
    actions: tuple[OperationActionRecord, ...],
    kind: OperationActionKind,
    predicate,
    *,
    description: str,
) -> OperationActionRecord:
    matches = tuple(
        action
        for action in actions
        if action.action_type is kind and predicate(action)
    )
    if len(matches) != 1:
        raise DeploymentContextError(
            f"durable deployment context requires exactly one {description} action"
        )
    return matches[0]


def _required_idempotency_key(idempotency_key: str | None) -> str:
    if idempotency_key is None:
        raise DeploymentContextError("deployment session has no idempotency identity")
    return idempotency_key
