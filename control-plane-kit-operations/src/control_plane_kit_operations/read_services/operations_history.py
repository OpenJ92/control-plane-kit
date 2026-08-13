"""Workspace-contained operational history read projections."""

from __future__ import annotations

from collections.abc import Callable
from typing import Mapping

from control_plane_kit_core.approval_subjects import ActivityPlanApprovalSubject
from control_plane_kit_core.planning import (
    DEFAULT_ACTIVITY_PLAN_CODEC,
    ActivityImpact,
    ReviewChange,
    RiskLevel,
    plan_recovery_transition,
)
from control_plane_kit_core.topology import (
    DEFAULT_GRAPH_CODEC,
    GraphDescriptorCodec,
    GraphDescriptorError,
    validate_graph,
)
from control_plane_kit_operations.read_pages import (
    ReadCollection,
    ReadPage,
    ReadPageError,
    ReadPageRequest,
)
from control_plane_kit_operations.records import (
    ActivityEventRecord,
    ActivityPlanRecord,
    ActivityRunRecord,
    ApprovalRequestRecord,
    FailureEvidence,
    OperationSessionRecord,
    WorkspaceRecord,
)

from ._redaction import _redact_descriptor_value
from .errors import ReadModelError
from .models import FocusedDetailReadModel
from .protocols import ActivityHistoryStore, ExecutionStore, GraphTopologyStore


class _OperationsHistoryReadProjection:
    """Interpret durable activity and execution truth as public read values."""

    def __init__(
        self,
        require_workspace: Callable[[str], WorkspaceRecord],
        graph_topology_store: GraphTopologyStore,
        activity_history_store: ActivityHistoryStore | None = None,
        execution_store: ExecutionStore | None = None,
        *,
        graph_codec: GraphDescriptorCodec = DEFAULT_GRAPH_CODEC,
    ) -> None:
        self._require_workspace = require_workspace
        self._graph_topology_store = graph_topology_store
        self._activity_history_store = activity_history_store
        self._execution_store = execution_store
        self._graph_codec = graph_codec

    def activity_sessions(
        self,
        request: ReadPageRequest,
    ) -> ReadPage[dict[str, object]]:
        if request.collection is not ReadCollection.ACTIVITY_SESSIONS:
            raise ReadPageError("activity session request is incongruent")
        self._require_workspace(request.scope.workspace_id)
        return self._activity_history().session_page(request).map(
            _session_summary_descriptor
        )

    def open_sessions(
        self,
        request: ReadPageRequest,
    ) -> ReadPage[dict[str, object]]:
        if request.collection is not ReadCollection.OPEN_SESSIONS:
            raise ReadPageError("open session request is incongruent")
        self._require_workspace(request.scope.workspace_id)
        return self._activity_history().session_page(request).map(
            _session_summary_descriptor
        )

    def session_detail(
        self,
        workspace_id: str,
        session_id: str,
    ) -> FocusedDetailReadModel:
        self._require_workspace(workspace_id)
        store = self._activity_history()
        session = _session_in_workspace(store, workspace_id, session_id)
        return FocusedDetailReadModel(
            workspace_id=workspace_id,
            kind="session-detail",
            payload={"session": _session_summary_descriptor(session)},
        )

    def session_actions(
        self,
        request: ReadPageRequest,
    ) -> ReadPage[dict[str, object]]:
        if request.collection is not ReadCollection.SESSION_ACTIONS:
            raise ReadPageError("session action request is incongruent")
        self._require_workspace(request.scope.workspace_id)
        store = self._activity_history()
        _session_in_workspace(
            store,
            request.scope.workspace_id,
            request.scope.session_id,
        )
        return store.action_page(request).map(_action_descriptor)

    def run_events(
        self,
        request: ReadPageRequest,
    ) -> ReadPage[dict[str, object]]:
        if request.collection is not ReadCollection.RUN_EVENTS:
            raise ReadPageError("run event request is incongruent")
        workspace_id = request.scope.workspace_id
        self._require_workspace(workspace_id)
        store = self._execution()
        try:
            run = store.get_run(request.scope.run_id)
            execution_request = store.get_request(run.admission.request_id)
        except KeyError:
            raise ReadModelError("missing run in workspace") from None
        identity = getattr(execution_request, "identity", None)
        if getattr(identity, "workspace_id", None) != workspace_id:
            raise ReadModelError("missing run in workspace")
        return store.event_page(request).map(_event_descriptor)

    def session_plans(
        self,
        request: ReadPageRequest,
    ) -> ReadPage[dict[str, object]]:
        if request.collection is not ReadCollection.SESSION_PLANS:
            raise ReadPageError("session plan request is incongruent")
        self._require_workspace(request.scope.workspace_id)
        store = self._activity_history()
        _session_in_workspace(store, request.scope.workspace_id, request.scope.session_id)
        return store.plan_page(request).map(_plan_summary_descriptor)

    def session_approvals(
        self,
        request: ReadPageRequest,
    ) -> ReadPage[dict[str, object]]:
        if request.collection is not ReadCollection.SESSION_APPROVALS:
            raise ReadPageError("session approval request is incongruent")
        self._require_workspace(request.scope.workspace_id)
        store = self._activity_history()
        _session_in_workspace(store, request.scope.workspace_id, request.scope.session_id)
        return store.approval_page(request).map(
            lambda item: _approval_descriptor(item.request, item.decision)
        )

    def plan_detail(
        self,
        workspace_id: str,
        plan_id: str,
    ) -> FocusedDetailReadModel:
        self._require_workspace(workspace_id)
        store = self._activity_history()
        plan = _plan_in_workspace(store, workspace_id, plan_id)
        payload = _plan_summary_descriptor(plan)
        payload["risk_summary"] = _risk_summary(plan)
        payload["recovery"] = self._recovery_for_plan(workspace_id, plan)
        return FocusedDetailReadModel(
            workspace_id=workspace_id,
            kind="plan-detail",
            payload={"plan": payload},
        )

    def pending_approvals(
        self,
        request: ReadPageRequest,
    ) -> ReadPage[dict[str, object]]:
        if request.collection is not ReadCollection.PENDING_APPROVALS:
            raise ReadPageError("pending approval request is incongruent")
        self._require_workspace(request.scope.workspace_id)
        return self._activity_history().pending_approval_page(request).map(
            lambda item: _approval_descriptor(item.request, item.decision)
        )

    def plan_runs(
        self,
        request: ReadPageRequest,
    ) -> ReadPage[dict[str, object]]:
        if request.collection is not ReadCollection.PLAN_RUNS:
            raise ReadPageError("plan run request is incongruent")
        self._require_workspace(request.scope.workspace_id)
        _plan_in_workspace(
            self._activity_history(),
            request.scope.workspace_id,
            request.scope.plan_id,
        )
        return self._execution().run_page(request).map(_run_summary_descriptor)

    def approval_detail(
        self,
        workspace_id: str,
        approval_request_id: str,
    ) -> FocusedDetailReadModel:
        self._require_workspace(workspace_id)
        store = self._activity_history()
        approval = _approval_in_workspace(store, workspace_id, approval_request_id)
        decision = store.approval_decision_for_request(approval.request_id)
        detail: dict[str, object] = {
            "approval": _approval_descriptor(approval, decision)
        }
        if isinstance(approval.subject, ActivityPlanApprovalSubject):
            plan = _plan_in_workspace(store, workspace_id, approval.subject.plan_id)
            if plan.session_id != approval.session_id:
                raise ReadModelError(
                    f"approval {approval_request_id!r} references plan truth outside its session"
                )
            payload = _plan_summary_descriptor(plan)
            payload["risk_summary"] = _risk_summary(plan)
            payload["recovery"] = self._recovery_for_plan(workspace_id, plan)
            detail["plan"] = payload
        else:
            detail["rotation"] = approval.subject.descriptor()
        return FocusedDetailReadModel(
            workspace_id=workspace_id,
            kind="approval-detail",
            payload=detail,
        )

    def _activity_history(self) -> ActivityHistoryStore:
        if self._activity_history_store is None:
            raise ReadModelError("activity history store is not configured")
        return self._activity_history_store

    def _execution(self) -> ExecutionStore:
        if self._execution_store is None:
            raise ReadModelError("execution store is not configured")
        return self._execution_store

    def _recovery_for_plan(
        self,
        workspace_id: str,
        plan: ActivityPlanRecord,
    ) -> Mapping[str, object]:
        missing = False
        try:
            base = self._graph_topology_store.get(plan.base_graph_id)
            desired = self._graph_topology_store.get(plan.desired_graph_id)
        except KeyError:
            missing = True
            base = None
            desired = None
        if (
            missing
            or base is None
            or desired is None
            or base.workspace_id != workspace_id
            or desired.workspace_id != workspace_id
        ):
            raise ReadModelError("plan recovery graph truth is unavailable") from None

        invalid = False
        try:
            target = validate_graph(self._graph_codec.decode(base.graph_descriptor))
            current = validate_graph(self._graph_codec.decode(desired.graph_descriptor))
            candidate = plan_recovery_transition(current, target)
        except (GraphDescriptorError, ValueError, TypeError):
            invalid = True
            candidate = None
        if invalid or candidate is None:
            raise ReadModelError("plan recovery graph truth is invalid") from None
        return candidate.descriptor()


def _session_summary_descriptor(session: OperationSessionRecord) -> dict[str, object]:
    return {
        "session_id": session.session_id,
        "workspace_id": session.workspace_id,
        "actor_id": session.actor_id,
        "title": session.title,
        "status": session.status.value,
        "created_at": session.created_at,
        "closed_at": session.closed_at,
        "metadata": _redact_descriptor_value("metadata", session.metadata),
    }


def _action_descriptor(action: object) -> dict[str, object]:
    return {
        "action_id": getattr(action, "action_id"),
        "session_id": getattr(action, "session_id"),
        "ordinal": getattr(action, "ordinal"),
        "action_type": getattr(action, "action_type").value,
        "actor_id": getattr(action, "actor_id"),
        "payload": _redact_descriptor_value("payload", getattr(action, "payload")),
        "created_at": getattr(action, "created_at"),
    }


def _approval_descriptor(
    approval: ApprovalRequestRecord,
    decision: object | None,
) -> dict[str, object]:
    descriptor = {
        "request_id": approval.request_id,
        "session_id": approval.session_id,
        "requested_by": approval.requested_by,
        "requested_at": approval.requested_at,
        "required_scope": approval.required_scope.value,
        "max_risk": approval.max_risk.value,
        "destructive": approval.destructive,
        "comment": approval.comment,
        "state": "pending" if decision is None else getattr(decision, "decision").value,
        "decision": None if decision is None else {
            "decision_id": getattr(decision, "decision_id"),
            "actor_id": getattr(decision, "actor_id"),
            "decision": getattr(decision, "decision").value,
            "scope": getattr(decision, "scope").value,
            "decided_at": getattr(decision, "decided_at"),
            "comment": getattr(decision, "comment"),
        },
    }
    if isinstance(approval.subject, ActivityPlanApprovalSubject):
        descriptor["plan_id"] = approval.plan_id
    else:
        descriptor["subject"] = approval.subject.descriptor()
        descriptor["review_digest"] = approval.subject.review_digest
    return descriptor


def _plan_summary_descriptor(plan: ActivityPlanRecord) -> dict[str, object]:
    return {
        "plan_id": plan.plan_id,
        "session_id": plan.session_id,
        "base_graph_id": plan.base_graph_id,
        "desired_graph_id": plan.desired_graph_id,
        "base_realized_projection_id": plan.base_realized_projection_id,
        "desired_realized_projection_id": plan.desired_realized_projection_id,
        "desired_graph_revision": plan.desired_graph_revision,
        "status": plan.status.value,
        "created_at": plan.created_at,
        "payload": DEFAULT_ACTIVITY_PLAN_CODEC.encode(plan.plan),
    }


def _run_summary_descriptor(run: ActivityRunRecord) -> dict[str, object]:
    return {
        "run_id": run.run_id,
        "plan_id": run.plan_id,
        "request_id": run.admission.request_id,
        "attempt": run.retry.attempt,
        "prior_run_id": run.retry.prior_run_id,
        "status": run.status.value,
        "created_at": run.created_at,
        "started_at": run.started_at,
        "settled_at": run.settled_at,
        "metadata": _redact_descriptor_value("metadata", run.metadata.descriptor()),
    }


def _event_descriptor(event: ActivityEventRecord) -> dict[str, object]:
    return {
        "event_id": event.event_id,
        "run_id": event.run_id,
        "ordinal": event.ordinal,
        "event_type": event.kind.value,
        "occurred_at": event.occurred_at,
        "activity_id": event.activity_id,
        "payload": _redact_descriptor_value("payload", event.evidence.descriptor()),
        "failure": _failure_descriptor(event.failure),
    }


def _failure_descriptor(failure: FailureEvidence | None) -> dict[str, object] | None:
    if failure is None:
        return None
    return {
        "category": failure.category.value,
        "code": failure.code,
        "message": failure.message,
        "details": _redact_descriptor_value("details", failure.details.descriptor()),
    }


def _session_in_workspace(
    store: ActivityHistoryStore,
    workspace_id: str,
    session_id: str,
) -> OperationSessionRecord:
    try:
        session = store.get_session(session_id)
    except KeyError as exc:
        raise ReadModelError(
            f"missing session {session_id!r} in workspace {workspace_id!r}"
        ) from exc
    if session.workspace_id != workspace_id:
        raise ReadModelError(
            f"missing session {session_id!r} in workspace {workspace_id!r}"
        )
    return session


def _plan_in_workspace(
    store: ActivityHistoryStore,
    workspace_id: str,
    plan_id: str,
) -> ActivityPlanRecord:
    try:
        plan = store.get_plan(plan_id)
        session = store.get_session(plan.session_id)
    except KeyError as exc:
        raise ReadModelError(
            f"missing plan {plan_id!r} in workspace {workspace_id!r}"
        ) from exc
    if session.workspace_id != workspace_id:
        raise ReadModelError(
            f"missing plan {plan_id!r} in workspace {workspace_id!r}"
        )
    return plan


def _approval_in_workspace(
    store: ActivityHistoryStore,
    workspace_id: str,
    approval_request_id: str,
) -> ApprovalRequestRecord:
    try:
        approval = store.get_approval_request(approval_request_id)
        session = store.get_session(approval.session_id)
    except KeyError as exc:
        raise ReadModelError(
            f"missing approval {approval_request_id!r} in workspace {workspace_id!r}"
        ) from exc
    if session.workspace_id != workspace_id:
        raise ReadModelError(
            f"missing approval {approval_request_id!r} in workspace {workspace_id!r}"
        )
    return approval


def _risk_summary(plan: ActivityPlanRecord) -> dict[str, object]:
    counts = {risk.value: 0 for risk in RiskLevel}
    for activity in plan.plan.activities:
        counts[activity.risk.value] += 1
    max_risk = max(
        (activity.risk for activity in plan.plan.activities),
        key=_risk_rank,
        default=RiskLevel.INFORMATIONAL,
    )
    return {
        "max_risk": max_risk.value,
        "counts": counts,
        "destructive_count": sum(
            activity.impact is ActivityImpact.DESTRUCTIVE
            for activity in plan.plan.activities
        ),
        "review_blocker_count": sum(
            isinstance(activity.operation, ReviewChange)
            for activity in plan.plan.activities
        ),
        "ready_for_execution": plan.plan.ready_for_execution,
    }


def _risk_rank(risk: RiskLevel) -> int:
    return tuple(RiskLevel).index(risk)
