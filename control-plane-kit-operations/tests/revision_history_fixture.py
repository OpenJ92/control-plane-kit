"""#1773 uses existing preparation owners and typed durable fixture records."""
from dataclasses import replace

import control_plane_kit_operations as operations
from control_plane_kit_core.approval_subjects import ActivityPlanApprovalSubject
from control_plane_kit_core.operations import ActivityRunStatus, ExecutionRequestStatus
from control_plane_kit_core.planning import RiskLevel
from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_operations.records import (
    ActivityRunRecord, AdmittedRun, ApprovalDecisionKind, ApprovalDecisionRecord,
    ApprovalRequestRecord, ExecutionIdempotency, ExecutionRequestIdentity,
    ExecutionRequestRecord, RetryIdentity,
)
from control_plane_kit_operations.read_pages import ReadCollection, ReadPageRequest
from control_plane_kit_operations.read_services import InstanceReadService
from saved_preparation_fixture import SavedPreparationFixture
from draft_catalogue_fixture import NOW

PREFIX = "read.desired-topology-draft-revision-"


class RevisionHistoryFixture(SavedPreparationFixture):
    def setUp(self):
        super().setUp()
        self.scope_type = getattr(operations, "RevisionReadScope", None)
        self.assertIsNotNone(self.scope_type, "missing revision history scope")
        self.collections = {name: getattr(ReadCollection, "DESIRED_TOPOLOGY_DRAFT_REVISION_" + name.upper(), None)
                            for name in ("preparations", "attempts")}
        self.assertNotIn(None, self.collections.values(), "missing revision history collections")
        with self.unit_of_work() as uow:
            self.assertIsNotNone(getattr(uow.stores, "revision_history", None), "missing revision history owner")

    def request_page(self, draft, kind, *, limit=10, cursor=None, workspace="workspace-a"):
        return ReadPageRequest(self.collections[kind], self.scope_type(workspace, draft.draft_id, draft.revision),
                               limit, cursor)

    def page(self, draft, kind, *, limit=10, cursor=None, uow=None):
        with (uow or self.unit_of_work)() as unit:
            return unit.stores.revision_history.page(self.request_page(draft, kind, limit=limit, cursor=cursor))

    def service_page(self, draft, kind, *, limit=10, cursor=None):
        with self.unit_of_work() as uow:
            store = uow.stores
            service = InstanceReadService(workspace_store=store.workspaces, graph_topology_store=store.graphs,
                desired_topology_draft_store=store.desired_topology_drafts, revision_history_store=store.revision_history)
            return service.desired_topology_draft_revision_history(
                self.request_page(draft, kind, limit=limit, cursor=cursor)).descriptor()

    def wire_page(self, draft, kind, *, surface="http", **values):
        return self.read(PREFIX + kind, surface=surface, draft_id=draft.draft_id,
                         revision=draft.revision, **values)

    def detail(self, draft):
        return self.read("read.desired-topology-draft-revision", draft_id=draft.draft_id, revision=draft.revision)

    def plan(self, result):
        with self.unit_of_work() as uow:
            return uow.stores.activity_history.get_plan(result.reference.plan_id)

    def clone_plan(self, original, *, plan_id, session_id=None, **changes):
        plan = replace(original, plan_id=plan_id, session_id=session_id or original.session_id, **changes)
        with self.unit_of_work() as uow:
            uow.stores.activity_history.add_plan(plan)
            uow.commit()
        return plan

    def add_attempt(self, plan, suffix, *, status=ActivityRunStatus.SUCCEEDED, created_at=NOW):
        """Persist test-only recorded relationships; claim no execution/advancement."""
        settled = status in {ActivityRunStatus.SUCCEEDED, ActivityRunStatus.COMPENSATED,
            ActivityRunStatus.PARTIALLY_FAILED, ActivityRunStatus.UNCOMPENSATED_FAILURE, ActivityRunStatus.CANCELLED}
        run = ActivityRunRecord("run-" + suffix, plan.plan_id, AdmittedRun("request-" + suffix), RetryIdentity(1),
            status, created_at, started_at=None if status is ActivityRunStatus.CLAIMED else created_at,
            settled_at=created_at if settled else None)
        with self.unit_of_work() as uow:
            stores = uow.stores
            stores.activity_history.add_approval_request(ApprovalRequestRecord("approval-" + suffix, plan.session_id,
                ActivityPlanApprovalSubject(plan.plan_id), "operator-a", created_at, PolicyScope.PLAN_APPROVE, RiskLevel.LOW, False))
            stores.activity_history.add_approval_decision(ApprovalDecisionRecord("decision-" + suffix,
                "approval-" + suffix, "operator-a", ApprovalDecisionKind.APPROVED, PolicyScope.PLAN_APPROVE, created_at))
            stores.execution.add_request(ExecutionRequestRecord(
                ExecutionRequestIdentity("request-" + suffix, "workspace-a", plan.session_id, plan.plan_id),
                ExecutionRequestStatus.CANCELLED, "operator-a", created_at, "approval-" + suffix,
                "decision-" + suffix, ExecutionIdempotency("execute-" + suffix, "fixture-" + suffix)))
            stores.execution.add_run(run)
            uow.commit()
        return run

    def history_truth(self):
        return {name: self.rows(name) for name in (
            "cpk_workspaces", "cpk_operation_sessions", "cpk_operation_actions", "cpk_activity_plans",
            "cpk_saved_preparation_sources", "cpk_execution_requests", "cpk_activity_runs", "cpk_activity_events")}
