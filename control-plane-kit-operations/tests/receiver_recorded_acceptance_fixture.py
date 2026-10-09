"""Explicit C2 provenance premise, not C3 execution or deployment evidence.

The original receiver introduction is real C2 command output. The accepted
current premise is recorded history for an EMPTY legacy plan, which current
real admission refuses. Its typed receipt association and empty success journal
are checked by existing owners. No provider ran and no deployment is claimed.
C3 owns production atomic advancement/receiver acceptance. C1 consumption uses
separate genuine affecting-run fixtures, not this empty-history premise.
Typed original action/event rows below remain explicit SQL history premises;
they do not create B2 initialization, acceptance headers, slots or claims.
"""

from psycopg.types.json import Jsonb

from control_plane_kit_core.approval_subjects import ActivityPlanApprovalSubject
from control_plane_kit_core.operations.lifecycle import ActivityEventKind, ActivityRunStatus, LifecycleOperationKind
from control_plane_kit_core.planning import ActivityPlan, RiskLevel
from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_operations.advancement import _require_complete_success
from control_plane_kit_operations.records import (
    ActivityEventRecord, ActivityPlanRecord, ActivityPlanStatus, ActivityRunRecord,
    AdmittedRun, ApprovalDecisionKind, ApprovalDecisionRecord, ApprovalRequestRecord,
    BoundedEvidence, OperationActionRecord, RetryIdentity,
)
from control_plane_kit_operations.revision_history import historical_advancement
from tests.receiver_scope_history_fixture import insert_recorded_request


def record_accepted_current(case):
    original = case.desired_service().execute(case.desired_command())
    at = "2026-09-06T18:01:00.000001Z"
    with case.unit_of_work() as uow:
        stores = uow.stores
        guard = stores.graphs.lock_receiver_lifecycle("workspace-a")
        workspace = stores.workspaces.get_for_update("workspace-a")
        projection = stores.realized_graphs.get(original.desired_realized_projection_id)
        session = case.sessions["workspace-a"]
        plan = ActivityPlanRecord("recorded-plan", session, workspace.current_graph_id,
            workspace.desired_graph_id, ActivityPlanStatus.PLANNED, at, ActivityPlan(()),
            base_realized_projection_id=workspace.current_realized_projection_id,
            desired_realized_projection_id=workspace.desired_realized_projection_id,
            desired_graph_revision=workspace.desired_graph_revision)
        stores.activity_history.add_plan(plan)
        stores.activity_history.add_approval_request(ApprovalRequestRecord("recorded-approval", session,
            ActivityPlanApprovalSubject(plan.plan_id), "operator-a", at, PolicyScope.PLAN_APPROVE, RiskLevel.LOW, False))
        stores.activity_history.add_approval_decision(ApprovalDecisionRecord("recorded-decision", "recorded-approval",
            "manager-a", ApprovalDecisionKind.APPROVED, PolicyScope.PLAN_APPROVE, at))
        insert_recorded_request(stores.connection, request_id="recorded-request", workspace_id="workspace-a",
            session_id=session, plan_id=plan.plan_id, status="claimed", requested_at=at,
            approval_request_id="recorded-approval", approval_decision_id="recorded-decision",
            claim_worker_id="worker-a", claim_generation=1, claimed_at=at,
            lease_expires_at="2026-09-06T18:11:00Z")
        run = ActivityRunRecord("recorded-run", plan.plan_id, AdmittedRun("recorded-request"), RetryIdentity(1),
            ActivityRunStatus.SUCCEEDED, at, started_at=at, settled_at=at)
        stores.execution._add_run(run)
        journal = tuple(ActivityEventRecord("recorded-event-" + str(ordinal), run.run_id, ordinal, kind, at)
            for ordinal, kind in enumerate((ActivityEventKind.RUN_OPENED, ActivityEventKind.RUN_STARTED,
                                            ActivityEventKind.RUN_SUCCEEDED), 1))
        for event in journal:
            stores.execution.add_event(event)
        _require_complete_success(plan.plan, run, journal)
        evidence = BoundedEvidence.from_mapping(dict(workspace_id="workspace-a", plan_id=plan.plan_id,
            run_id=run.run_id, from_authored_graph_id=plan.base_graph_id,
            from_realized_projection_id=plan.base_realized_projection_id,
            to_authored_graph_id=plan.desired_graph_id, to_realized_projection_id=plan.desired_realized_projection_id,
            to_realized_projection_digest=projection.projection_digest, desired_graph_revision=plan.desired_graph_revision))
        event = ActivityEventRecord("recorded-advancement-event", run.run_id, 4,
            ActivityEventKind.CURRENT_GRAPH_ADVANCED, at, evidence=evidence)
        action = OperationActionRecord("recorded-advancement-action", session,
            stores.activity_history.next_action_ordinal(session), LifecycleOperationKind.ADVANCE_CURRENT_GRAPH,
            "worker-a", payload=evidence.descriptor() | dict(execution_request_id="recorded-request",
                claim_generation=1, event_id=event.event_id), created_at=at)
        # Generic writers correctly reject original advancement publication.
        # This deliberately unsupported legacy history is a reader premise,
        # just like the recorded request above, not an owner-produced receipt.
        stores.connection.execute(
            "INSERT INTO cpk_activity_events (event_id,run_id,ordinal,event_type,occurred_at,payload,"
            "advancement_workspace_id,advancement_request_id,advancement_plan_id,advancement_revision) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (event.event_id, event.run_id, event.ordinal, event.kind.value, event.occurred_at,
             Jsonb({"activity_id": None, "evidence": event.evidence.descriptor(), "failure": None, "recovery": None}),
             "workspace-a", "recorded-request", plan.plan_id, plan.desired_graph_revision))
        stores.connection.execute(
            "INSERT INTO cpk_operation_actions (action_id,session_id,ordinal,action_type,actor_id,payload,"
            "created_at,idempotency_key,intent_fingerprint,advancement_workspace_id,advancement_request_id,"
            "advancement_plan_id,advancement_run_id,advancement_revision) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (action.action_id, action.session_id, action.ordinal, action.action_type.value, action.actor_id,
             Jsonb(action.payload), action.created_at, action.idempotency_key, action.intent_fingerprint,
             "workspace-a", "recorded-request", plan.plan_id, run.run_id, plan.desired_graph_revision))
        receipt = historical_advancement(workspace_id="workspace-a", session_id=session, plan_id=plan.plan_id,
            plan=dict(base_graph_id=plan.base_graph_id, base_realized_projection_id=plan.base_realized_projection_id,
                desired_graph_id=plan.desired_graph_id, desired_realized_projection_id=plan.desired_realized_projection_id,
                desired_graph_revision=plan.desired_graph_revision), request_id="recorded-request", run_id=run.run_id,
            projection_digest=projection.projection_digest, events=(event,), actions=(action,))
        case.assertEqual(receipt["state"], "accepted")
        # These are the explicitly seeded C3-only facts, not supported C2
        # advancement. No production entrypoint receives a fixture bypass.
        stores.connection.execute("UPDATE cpk_workspaces SET current_graph_id=%s,current_realized_projection_id=%s "
            "WHERE workspace_id='workspace-a'", (original.graph_version_id, projection.projection_id))
        stores.graphs._record_receiver_first_acceptance("workspace-a", "a" * 32,
            action_id=action.action_id, session_id=session, lifecycle_guard=guard)
        uow.commit()
    return original, action, event
