"""Bounded operator overview; no effects, authority decisions, or durable writes."""

from __future__ import annotations

from datetime import datetime

from control_plane_kit_core.approval_subjects import ActivityPlanApprovalSubject
from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_operations.desired_topology_drafts import (
    DesiredTopologyDraftStore, DesiredTopologyDraftRecord, DesiredTopologyDraftRevisionRecord,
)
from control_plane_kit_operations.read_pages import (
    ReadCollection, ReadCursor, ReadPage, ReadPageError, ReadPageRequest,
    RunReadScope, WorkspaceReadScope,
)
from control_plane_kit_operations.records import (
    ActivityPlanRecord, ActivityPlanStatus, ActivityRunRecord, ActivityRunStatus,
    ApprovalRequestRecord, CoordinatorStatus, ExecutionCommandReceiptRecord,
    ExecutionCommandReceiptStatus, ExecutionRequestRecord, OperationSessionRecord,
    OperationSessionStatus, WorkspaceRecord,
)

from control_plane_kit_operations.saved_deployment_preparation import (
    saved_preparation_session_metadata, validate_saved_preparation_source,
)
from .protocols import SavedPreparationSourceStore

from .errors import ReadModelError
from .models import OperatorOverviewReadModel
from .operations_history import _event_descriptor
from .protocols import ActivityHistoryStore, ExecutionStore, GraphTopologyStore, WorkspaceStore


class _Unavailable(ValueError):
    """A bounded observation cannot substantiate a congruent overview."""


def _draft_state(state):
    return {"state": state, "selected": None, "head": None}


def _coordinate(value: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 512:
        raise _Unavailable()
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise _Unavailable()
    return value


def _command(state: str = "none") -> dict[str, object]:
    return {"receipt_state": state, "coordinator_status": None,
            "effects_attempted": None, "activity_id": None}


def _workflow(state: str = "none") -> dict[str, object]:
    return {"selection": state, "session": None, "plan": None, "approval": None,
            "run_selection": "none" if state == "none" else "unavailable",
            "run": None, "command": _command(),
            "prepared_draft": {"state": "none" if state == "none" else "unavailable", "revision": None}}


def _history(state: str, run_id: str | None = None) -> dict[str, object]:
    return {"state": state, "run_id": run_id, "items": [], "next_cursor": None}


def _action(state: str, coordinates: dict[str, str]) -> dict[str, object]:
    return {"state": state, "operation_id": None, "required_scopes": [],
            "coordinates": coordinates}


def _offer(operation: str, scopes: tuple[PolicyScope, ...],
           coordinates: dict[str, str]) -> dict[str, object]:
    return {"state": "available", "operation_id": operation,
            "required_scopes": sorted(scope.value for scope in scopes),
            "coordinates": coordinates}


class _OperatorOverviewReadProjection:
    def __init__(self, workspaces: WorkspaceStore, graphs: GraphTopologyStore,
                 history: ActivityHistoryStore | None, execution: ExecutionStore | None,
                 drafts: DesiredTopologyDraftStore | None = None,
                 saved_sources: SavedPreparationSourceStore | None = None) -> None:
        self._workspaces = workspaces
        self._graphs = graphs
        self._history = history
        self._execution = execution
        self._drafts = drafts
        self._saved_sources = saved_sources

    def read(self, workspace_id: str, *, limit: int = 50,
             after: ReadCursor | None = None) -> OperatorOverviewReadModel:
        WorkspaceReadScope(workspace_id)
        # Validate page syntax even when there is no selected run. The eventual
        # RunReadScope additionally rejects a cursor for any other run/workspace.
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ReadPageError("read page limit is malformed")
        try:
            workspace = self._workspaces.get(workspace_id)
        except KeyError:
            raise ReadModelError("missing workspace") from None
        except (ValueError, TypeError, AttributeError):
            workspace = None
        graphs = {"current": {"assigned": False, "graph_id": None,
                              "realized_projection_id": None},
                  "desired": {"assigned": False, "graph_id": None,
                              "realized_projection_id": None, "revision": None, "draft": _draft_state("unavailable")},
                  "relation": "unavailable"}
        workflow = _workflow("unavailable")
        history = _history("unavailable")
        coordinates = {"workspace_id": workspace_id}
        next_action = _action("unavailable", coordinates)
        try:
            if not isinstance(workspace, WorkspaceRecord) or workspace.workspace_id != workspace_id:
                raise _Unavailable()
            graphs = self._graph_pointers(workspace)
            navigation, draft_anchor = self._draft_navigation(workspace)
            graphs["desired"]["draft"] = navigation
            if self._history is None or self._execution is None:
                raise _Unavailable()
            plans = (() if workspace.desired_lineage is None else
                     self._history.overview_plans(
                         workspace_id, workspace.desired_graph_id,
                         workspace.desired_realized_projection_id,
                         workspace.desired_graph_revision))
            if not isinstance(plans, tuple) or len(plans) > 2:
                raise _Unavailable()
            sessions = tuple(self._validate_plan(workspace, plan) for plan in plans)
            if len(plans) == 2:
                workflow = _workflow("ambiguous")
                next_action = _action("ambiguous", coordinates)
            elif not plans:
                workflow = _workflow()
                history = _history("none")
                next_action = (_offer("command.deployment.prepare",
                                     (PolicyScope.INSTANCE_WORKSPACE_EDIT, PolicyScope.PLAN_REQUEST),
                                     coordinates)
                               if graphs["relation"] == "diverged" else _action("none", coordinates))
            else:
                plan, session = plans[0], sessions[0]
                workflow = _workflow("selected")
                workflow["session"] = {"session_id": _coordinate(session.session_id),
                                       "status": session.status.value}
                workflow["plan"] = {"plan_id": _coordinate(plan.plan_id), "status": plan.status.value}
                prepared_draft = self._prepared_draft(workspace, plan, session)
                workflow["prepared_draft"] = prepared_draft
                coordinates = {**coordinates, "session_id": session.session_id, "plan_id": plan.plan_id}
                approvals = self._history.overview_pending_approvals(plan.plan_id)
                if not isinstance(approvals, tuple) or len(approvals) > 2:
                    raise _Unavailable()
                for approval in approvals:
                    if (not isinstance(approval, ApprovalRequestRecord)
                        or approval.session_id != session.session_id
                        or not isinstance(approval.subject, ActivityPlanApprovalSubject)
                        or approval.subject.plan_id != plan.plan_id
                        or approval.required_scope is not (
                            PolicyScope.PLAN_APPROVE_DESTRUCTIVE if approval.destructive
                            else PolicyScope.PLAN_APPROVE)
                        or self._history.approval_decision_for_request(approval.request_id) is not None):
                        raise _Unavailable()
                if len(approvals) > 1:
                    workflow = _workflow("ambiguous")
                    next_action = _action("ambiguous", coordinates)
                else:
                    if approvals:
                        approval = approvals[0]
                        workflow["approval"] = {
                            "request_id": _coordinate(approval.request_id), "state": "pending",
                            "required_scope": approval.required_scope.value,
                            "destructive": approval.destructive}
                    runs = self._execution.overview_runs(plan.plan_id)
                    state, run = self._select_run(workspace, plan, runs)
                    workflow["run_selection"] = state
                    if run is not None:
                        coordinates = {**coordinates, "request_id": run.admission.request_id,
                                       "run_id": run.run_id}
                        workflow["run"] = {"run_id": run.run_id, "request_id": run.admission.request_id,
                                           "status": run.status.value, "attempt": run.retry.attempt}
                        workflow["command"], receipts = self._read_command(run)
                        history = self._read_history(workspace_id, run, limit, after)
                        if self._execution.get_run(run.run_id) != run:
                            raise _Unavailable()
                        if self._execution.overview_receipts(run.run_id) != receipts:
                            raise _Unavailable()
                        self._validate_request(workspace, plan, run)
                    else:
                        history = _history("none" if state == "none" else "unavailable")
                    next_action = self._next_action(graphs, workflow, history, coordinates)
                    if self._history.overview_pending_approvals(plan.plan_id) != approvals:
                        raise _Unavailable()
                    if self._execution.overview_runs(plan.plan_id) != runs:
                        raise _Unavailable()
                if prepared_draft != self._prepared_draft(workspace, plan, session):
                    raise _Unavailable()
                if self._history.get_plan(plan.plan_id) != plan or self._history.get_session(session.session_id) != session:
                    raise _Unavailable()
            if workspace.desired_lineage is not None and self._history.overview_plans(
                workspace_id, workspace.desired_graph_id,
                workspace.desired_realized_projection_id, workspace.desired_graph_revision,
            ) != plans:
                raise _Unavailable()
            if self._draft_navigation(workspace)[1] != draft_anchor:
                graphs["desired"]["draft"] = _draft_state("unavailable")
            if self._anchors(self._workspaces.get(workspace_id)) != self._anchors(workspace):
                graphs["desired"]["draft"] = _draft_state("unavailable")
                graphs = {**graphs, "relation": "unavailable"}
                raise _Unavailable()
        except ReadPageError:
            raise
        except (KeyError, ValueError, TypeError, AttributeError):
            graphs["desired"]["draft"] = _draft_state("unavailable")
            # No store exception text, raw payload, or partly selected foreign
            # coordinate escapes the fail-closed response.
            workflow = _workflow("unavailable")
            history = _history("unavailable")
            next_action = _action("unavailable", {"workspace_id": workspace_id})
        if after is not None and workflow["run_selection"] != "selected":
            raise ReadPageError("overview cursor has no selected run")
        return OperatorOverviewReadModel(workspace_id, graphs, workflow, history, next_action)

    @staticmethod
    def _anchors(workspace: WorkspaceRecord) -> tuple[object, ...]:
        return (workspace.workspace_id, workspace.current_graph_id,
                workspace.current_realized_projection_id, workspace.desired_graph_id,
                workspace.desired_realized_projection_id, workspace.desired_graph_revision)

    def _draft_navigation(self, workspace: WorkspaceRecord):
        if workspace.desired_graph_id is None:
            return _draft_state("none"), ()
        try:
            if self._drafts is None:
                raise _Unavailable()
            revisions = self._drafts.revisions_for_graph(workspace.workspace_id, workspace.desired_graph_id)
            if not isinstance(revisions, tuple) or len(revisions) > 1:
                raise _Unavailable()
            if not revisions:
                return _draft_state("none"), ()
            selected = revisions[0]
            if (not isinstance(selected, DesiredTopologyDraftRevisionRecord)
                    or selected.workspace_id != workspace.workspace_id
                    or selected.graph_id != workspace.desired_graph_id):
                raise _Unavailable()
            draft = self._drafts.get(workspace.workspace_id, selected.draft_id)
            if (not isinstance(draft, DesiredTopologyDraftRecord)
                    or (draft.workspace_id, draft.draft_id) != (workspace.workspace_id, selected.draft_id)
                    or draft.deleted_at is not None or draft.head_revision < selected.revision):
                raise _Unavailable()
            head = self._drafts.revision(workspace.workspace_id, draft.draft_id, draft.head_revision)
            if (not isinstance(head, DesiredTopologyDraftRevisionRecord)
                    or (head.workspace_id, head.draft_id, head.revision)
                    != (workspace.workspace_id, draft.draft_id, draft.head_revision)):
                raise _Unavailable()
            def coordinates(revision):
                return {"draft_id": _coordinate(revision.draft_id), "revision": revision.revision,
                        "graph_id": _coordinate(revision.graph_id)}
            return {"state": "selected", "selected": coordinates(selected), "head": coordinates(head)}, (draft, selected, head)
        except (KeyError, ValueError, TypeError, AttributeError):
            return _draft_state("unavailable"), None

    def _graph_pointers(self, workspace: WorkspaceRecord) -> dict[str, object]:
        pointers = {}
        for name in ("current", "desired"):
            graph_id = getattr(workspace, name + "_graph_id")
            projection_id = getattr(workspace, name + "_realized_projection_id")
            if (graph_id is None) != (projection_id is None):
                raise _Unavailable()
            if graph_id is not None:
                graph = self._graphs.get(_coordinate(graph_id))
                if graph.graph_id != graph_id or graph.workspace_id != workspace.workspace_id:
                    raise _Unavailable()
                _coordinate(projection_id)
            pointers[name] = {"assigned": graph_id is not None, "graph_id": graph_id,
                              "realized_projection_id": projection_id}
        pointers["desired"]["revision"] = workspace.desired_graph_revision
        pointers["relation"] = ("unassigned" if workspace.desired_lineage is None else
                                "converged" if workspace.current_lineage == workspace.desired_lineage else "diverged")
        return pointers

    def _prepared_draft(self, workspace, plan, session):
        try:
            source = (None if self._saved_sources is None else
                      self._saved_sources.get(workspace.workspace_id, session.session_id))
            metadata = saved_preparation_session_metadata(session)
            if metadata is None:
                if source is not None:
                    raise _Unavailable()
                return {"state": "none", "revision": None}
            if self._drafts is None:
                raise _Unavailable()
            prefix = "deployment_prepare_saved_"
            if (metadata[prefix + "graph_id"], metadata[prefix + "desired_projection_id"],
                int(metadata[prefix + "desired_generation"]), metadata[prefix + "current_graph_id"],
                metadata[prefix + "current_projection_id"]) != (
                    plan.desired_graph_id, plan.desired_realized_projection_id, plan.desired_graph_revision,
                    plan.base_graph_id, plan.base_realized_projection_id):
                raise _Unavailable()
            revision = self._drafts.revision(workspace.workspace_id, metadata[prefix + "draft_id"],
                                             int(metadata[prefix + "revision"]))
            if self._saved_sources is None:
                raise _Unavailable()
            validate_saved_preparation_source(source, session, revision)
            if (revision.workspace_id != workspace.workspace_id
                or revision.draft_id != metadata[prefix + "draft_id"]
                or revision.revision != int(metadata[prefix + "revision"])
                or revision.graph_id != plan.desired_graph_id):
                raise _Unavailable()
            graph = self._graphs.get(revision.graph_id)
            if graph.workspace_id != workspace.workspace_id or graph.graph_id != revision.graph_id:
                raise _Unavailable()
            return {"state": "prepared", "revision": {"draft_id": _coordinate(revision.draft_id),
                    "revision": revision.revision, "graph_id": _coordinate(revision.graph_id)}}
        except (KeyError, ValueError, TypeError, AttributeError):
            return {"state": "unavailable", "revision": None}

    def _validate_plan(
        self, workspace: WorkspaceRecord, plan: ActivityPlanRecord,
    ) -> OperationSessionRecord:
        if (not isinstance(plan, ActivityPlanRecord) or plan.status is not ActivityPlanStatus.PLANNED
            or (plan.base_graph_id, plan.base_realized_projection_id)
            != (workspace.current_graph_id, workspace.current_realized_projection_id)
            or (plan.desired_graph_id, plan.desired_realized_projection_id, plan.desired_graph_revision)
            != (workspace.desired_graph_id, workspace.desired_realized_projection_id, workspace.desired_graph_revision)):
            raise _Unavailable()
        _coordinate(plan.plan_id)
        session = self._history.get_session(plan.session_id)
        if (session.session_id != plan.session_id or session.workspace_id != workspace.workspace_id
            or session.status is not OperationSessionStatus.OPEN):
            raise _Unavailable()
        base = self._graphs.get(plan.base_graph_id)
        if base.workspace_id != workspace.workspace_id or base.graph_id != plan.base_graph_id:
            raise _Unavailable()
        return session

    def _validate_request(self, workspace: WorkspaceRecord, plan: ActivityPlanRecord,
                          run: ActivityRunRecord) -> None:
        request = self._execution.get_request(run.admission.request_id)
        if not isinstance(request, ExecutionRequestRecord):
            raise _Unavailable()
        identity = request.identity
        if (identity.request_id, identity.workspace_id, identity.session_id, identity.plan_id) != (
            run.admission.request_id, workspace.workspace_id, plan.session_id, plan.plan_id):
            raise _Unavailable()
        _coordinate(identity.request_id)

    def _select_run(
        self, workspace: WorkspaceRecord, plan: ActivityPlanRecord,
        runs: tuple[ActivityRunRecord, ...] | None,
    ) -> tuple[str, ActivityRunRecord | None]:
        if runs is None or not isinstance(runs, tuple) or len(runs) > 100:
            return "unavailable", None
        if not runs:
            return "none", None
        by_id = {}
        for run in runs:
            if not isinstance(run, ActivityRunRecord) or run.plan_id != plan.plan_id:
                raise _Unavailable()
            _coordinate(run.run_id)
            if run.run_id in by_id:
                raise _Unavailable()
            by_id[run.run_id] = run
        if len({run.admission.request_id for run in runs}) != 1:
            return "unavailable", None
        self._validate_request(workspace, plan, runs[0])
        for run in runs:
            prior = run.retry.prior_run_id
            if prior is None:
                if run.retry.attempt != 1:
                    return "unavailable", None
            elif prior not in by_id or run.retry.attempt != by_id[prior].retry.attempt + 1:
                return "unavailable", None
        predecessors = {run.retry.prior_run_id for run in runs}
        leaves = [run for run in runs if run.run_id not in predecessors]
        if len(leaves) > 1:
            return "ambiguous", None
        if len(leaves) != 1:
            return "unavailable", None
        leaf = leaves[0]
        if leaf.retry.attempt != len(runs) or len({run.retry.attempt for run in runs}) != len(runs):
            return "unavailable", None
        return "selected", leaf

    def _read_command(self, run: ActivityRunRecord) -> tuple[
        dict[str, object], tuple[ExecutionCommandReceiptRecord, ...]
    ]:
        receipts = self._execution.overview_receipts(run.run_id)
        if not isinstance(receipts, tuple) or len(receipts) > 2:
            raise _Unavailable()
        for receipt in receipts:
            if not isinstance(receipt, ExecutionCommandReceiptRecord) or receipt.run_id != run.run_id:
                raise _Unavailable()
            for retained in (receipt.initial_run,) + (() if receipt.result is None else (receipt.result.run,)):
                if (retained.run_id, retained.plan_id, retained.admission, retained.retry) != (
                    run.run_id, run.plan_id, run.admission, run.retry):
                    raise _Unavailable()
        if not receipts:
            return _command(), receipts
        incomplete = [receipt for receipt in receipts
                      if receipt.status is ExecutionCommandReceiptStatus.INCOMPLETE]
        if incomplete:
            return _command("incomplete" if len(incomplete) == 1 else "ambiguous"), receipts
        # Historical completions do not compete with the latest result. Only
        # chronology can establish currency; an idempotency key never does.
        completed = [(datetime.fromisoformat(receipt.completed_at), receipt)
                     for receipt in receipts]
        latest = max(instant for instant, receipt in completed)
        current = [receipt for instant, receipt in completed if instant == latest]
        if len(current) != 1:
            return _command("ambiguous"), receipts
        receipt = current[0]
        result = receipt.result
        return {"receipt_state": "completed", "coordinator_status": result.status.value,
                "effects_attempted": result.effects_attempted,
                "activity_id": None if result.activity_id is None else _coordinate(result.activity_id)}, receipts

    def _read_history(
        self, workspace_id: str, run: ActivityRunRecord, limit: int,
        after: ReadCursor | None,
    ) -> dict[str, object]:
        request = ReadPageRequest(ReadCollection.RUN_EVENTS, RunReadScope(workspace_id, run.run_id), limit, after)
        try:
            page = self._execution.event_page(request)
            if (not isinstance(page, ReadPage) or page.request != request
                or len(page.items) > limit or any(event.run_id != run.run_id for event in page.items)):
                raise _Unavailable()
            order = [(event.ordinal, event.event_id) for event in page.items]
            if order != sorted(set(order)):
                raise _Unavailable()
            return {"state": "available", "run_id": run.run_id,
                    "items": [_event_descriptor(event) for event in page.items],
                    "next_cursor": None if page.next_cursor is None else page.next_cursor.descriptor()}
        except (KeyError, ValueError, TypeError, AttributeError):
            return _history("unavailable")

    @staticmethod
    def _next_action(graphs, workflow, history, coordinates):
        state = workflow["run_selection"]
        if state in {"ambiguous", "unavailable"}:
            return _action(state, coordinates)
        if graphs["relation"] != "diverged":
            return _action("none", coordinates)
        command = workflow["command"]
        if (history["state"] == "unavailable" or command["receipt_state"] in {"incomplete", "ambiguous"}
            or command["coordinator_status"] in {"failed", "blocked", "unsupported", "in-flight", "uncertain"}):
            return _action("none", coordinates)
        approval = workflow["approval"]
        run = workflow["run"]
        if run is not None and run["status"] in {
            ActivityRunStatus.FAILED.value, ActivityRunStatus.CANCELLED.value,
            ActivityRunStatus.COMPENSATING.value, ActivityRunStatus.COMPENSATED.value,
            ActivityRunStatus.PARTIALLY_FAILED.value, ActivityRunStatus.UNCOMPENSATED_FAILURE.value,
        }:
            return _action("none", coordinates)
        if approval is not None:
            return _offer("command.approval.decide", (PolicyScope(approval["required_scope"]),),
                          {**coordinates, "approval_request_id": approval["request_id"]})
        if run is not None and graphs["relation"] == "diverged":
            if run["status"] == ActivityRunStatus.CLAIMED.value:
                return _offer("command.run.start", (PolicyScope.EXECUTION_OPERATE,), coordinates)
            if run["status"] == ActivityRunStatus.RUNNING.value:
                return _offer("command.deployment.execute", (PolicyScope.EXECUTION_OPERATE,), coordinates)
            if run["status"] == ActivityRunStatus.SUCCEEDED.value and command["coordinator_status"] == CoordinatorStatus.COMPLETED.value:
                return _offer("command.graph.advance-current", (PolicyScope.EXECUTION_OPERATE,), coordinates)
        return _action("none", coordinates)
