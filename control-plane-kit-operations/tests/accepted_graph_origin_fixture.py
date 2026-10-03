"""Establish a fixture's selected graph through real, simulated execution.

Callers create the workspace through its command owner, save their exact graph
and product material, and select desired truth before calling this helper with
an explicit bootstrap plan. All origin history remains available to B2 readers.
"""
from dataclasses import replace
from itertools import count

from control_plane_kit_core.approval_subjects import ActivityPlanApprovalSubject
from control_plane_kit_core.policies import ApprovalPolicy
from control_plane_kit_core.runtime_effects import RuntimeEffectResult
from control_plane_kit_operations.advancement import AdvanceCurrentGraph, CurrentGraphAdvancementCommandService
from control_plane_kit_operations.coordinator import CoordinatorStatus
from control_plane_kit_operations.execution_leases import ExecutionLeaseFence
from control_plane_kit_operations.lifecycle import (
    ClaimAndOpenActivityRun, ExecutionLeaseDuration, RunLifecycleCommandService, StartActivityRun,
)
from control_plane_kit_operations.records import (
    ActivityPlanRecord, ActivityPlanStatus, ApprovalDecisionKind, ApprovalDecisionRecord,
    ApprovalRequestRecord, OperationSessionRecord, OperationSessionStatus,
)
from control_plane_kit_operations.workflows import IdempotencyKey
from tests import test_execution_coordinator as coordinator_fixture
from tests.receiver_scope_history_fixture import admit_fixture_plan


def initialize_receiver_fixture_origin(case, *, runtime_graph=None):
    """Create real empty authority, optionally accept the original bare runtime."""
    from control_plane_kit_core.planning import ActivityId, ActivityPlan, PlannedActivity, RuntimeTarget, StartRuntime
    from control_plane_kit_operations.records import GraphVersionRecord
    from control_plane_kit_operations.workspaces import CreateWorkspace, WorkspaceCommandService
    initial_id = "graph-current" if runtime_graph is None else "scope-origin-empty"
    WorkspaceCommandService(case.unit_of_work,
        clock=lambda: "2026-07-22T11:59:00Z", id_factory=lambda: initial_id).create(
            CreateWorkspace("workspace-a", "Workspace A", "operator-a", IdempotencyKey("scope-origin-create")))
    if runtime_graph is None:
        return
    with case.unit_of_work() as uow:
        uow.stores.graphs.save(GraphVersionRecord.from_graph(
            graph_id="graph-current", workspace_id="workspace-a", version=2,
            graph=runtime_graph, created_by="operator-a", created_at="2026-07-22T12:00:00Z"))
        uow.stores.workspaces.set_desired_graph("workspace-a", "graph-current")
        uow.commit()
    case.assertEqual(tuple(runtime_graph.runtimes), ("docker",))
    case.assertEqual(runtime_graph.nodes, {})
    accept_selected_fixture_origin(case, ActivityPlan((PlannedActivity(
        ActivityId("scope-origin-start-runtime"), StartRuntime(RuntimeTarget("docker"))),)),
        prefix="scope-origin", timestamp="2026-07-22T12:00:15Z")


def accept_selected_fixture_origin(case, plan, *, workspace_id="workspace-a",
                                   prefix="fixture-origin", timestamp="2026-07-22T11:00:00Z"):
    """Approve, admit, execute, and accept the caller's explicit zero-slot plan."""
    clock = lambda: timestamp
    identity = lambda suffix: f"{prefix}-{suffix}"
    requirement = ApprovalPolicy().requirement_for(plan)
    with case.unit_of_work() as uow:
        stores = uow.stores
        workspace = stores.workspaces.get(workspace_id)
        stores.activity_history.add_session(OperationSessionRecord(
            identity("session"), workspace_id, "operator-a", "Establish fixture origin",
            OperationSessionStatus.OPEN, timestamp))
        stores.activity_history.add_plan(ActivityPlanRecord(
            identity("plan"), identity("session"), workspace.current_graph_id,
            workspace.desired_graph_id, ActivityPlanStatus.PLANNED, timestamp, plan,
            base_realized_projection_id=workspace.current_realized_projection_id,
            desired_realized_projection_id=workspace.desired_realized_projection_id,
            desired_graph_revision=workspace.desired_graph_revision))
        stores.activity_history.add_approval_request(ApprovalRequestRecord(
            identity("approval"), identity("session"), ActivityPlanApprovalSubject(identity("plan")),
            "operator-a", timestamp, requirement.required_scope, requirement.max_risk, requirement.destructive))
        stores.activity_history.add_approval_decision(ApprovalDecisionRecord(
            identity("decision"), identity("approval"), "manager-a", ApprovalDecisionKind.APPROVED,
            requirement.required_scope, timestamp))
        uow.commit()
    admit_fixture_plan(case, request_id=identity("request"), session_id=identity("session"),
        plan_id=identity("plan"), approval_request_id=identity("approval"), key=identity("admit"),
        requested_at=timestamp, workspace_id=workspace_id)
    engine = coordinator_fixture.ExecutionCoordinatorTests()
    engine.database_url, engine.connection = case.database_url, case.connection
    ids = count(1)
    engine.ids = lambda: identity(f"generated-{next(ids)}")
    engine.tracker = coordinator_fixture.TrackingUnitOfWorkFactory(case.database_url)
    lifecycle = RunLifecycleCommandService(case.unit_of_work, clock=clock, id_factory=engine.ids)
    opened = lifecycle.execute(ClaimAndOpenActivityRun(identity("request"), engine.authority(),
        ExecutionLeaseDuration(600), IdempotencyKey(identity("claim"))))
    run_id = opened.run.run_id
    fence = ExecutionLeaseFence(opened.request.claim.worker_id, opened.request.claim.generation)
    lifecycle.execute(StartActivityRun(run_id, engine.authority(), fence, IdempotencyKey(identity("start"))))
    success = lambda _context, request: RuntimeEffectResult.succeeded(
        request.effect_id, evidence={"adapter": "fixture-origin"})
    adapter = coordinator_fixture.RecordingAdapter(engine.tracker, *((success,) * len(plan.activities)))
    result = engine.coordinator(adapter, lifecycle=lifecycle, clock=clock).execute(replace(
        engine.command(generation=fence.generation, max_effects=len(plan.activities),
            idempotency_key=identity("execute")), run_id=run_id))
    case.assertIs(result.status, CoordinatorStatus.COMPLETED)
    case.assertEqual(adapter.calls, [activity.activity_id.value for activity in plan.activities])
    case.assertEqual(adapter.active_during_calls, [0] * len(plan.activities))
    accepted = CurrentGraphAdvancementCommandService(case.unit_of_work, clock=clock,
        id_factory=engine.ids).execute(AdvanceCurrentGraph(workspace_id, run_id, identity("plan"),
            workspace.current_graph_id, workspace.current_realized_projection_id,
            workspace.desired_graph_id, workspace.desired_realized_projection_id,
            workspace.desired_graph_revision, engine.authority(), fence, IdempotencyKey(identity("advance"))))
    with case.unit_of_work() as uow:
        current = uow.stores.configuration_acceptance.read_current_configuration(workspace_id)
    case.assertEqual((current.state, current.graph_id, current.projection_id, current.manifest_slot_count,
        current.bindings), ("complete", workspace.desired_graph_id, workspace.desired_realized_projection_id, 0, ()))
    return accepted
