"""Newest original receipt laws over real zero-slot P -> Q -> P advances."""
from dataclasses import replace
import unittest

from control_plane_kit_core.approval_subjects import ActivityPlanApprovalSubject
from control_plane_kit_core.operations.lifecycle import ActivityRunStatus
from control_plane_kit_core.planning import (
    ActivityId, ActivityPlan, PlannedActivity, RemoveRuntimeResource, RuntimeTarget, StartRuntime,
)
from control_plane_kit_core.policies import ApprovalPolicy
from control_plane_kit_core.runtime_effects import RuntimeEffectResult
from control_plane_kit_operations.advancement import (
    AdvanceCurrentGraph, CurrentGraphAdvancementCommandService, CurrentGraphAdvancementConflict,
)
from control_plane_kit_operations.coordinator import CoordinatorStatus
from control_plane_kit_operations.execution_leases import ExecutionLeaseFence
from control_plane_kit_operations.lifecycle import ClaimAndOpenActivityRun, ExecutionLeaseDuration, StartActivityRun
from control_plane_kit_operations.postgres import install_schema
from control_plane_kit_operations.records import (
    ActivityPlanRecord, ActivityPlanStatus, ApprovalDecisionKind, ApprovalDecisionRecord,
    ApprovalRequestRecord, OperationSessionRecord, OperationSessionStatus,
)
from control_plane_kit_operations.workflows import IdempotencyKey
from tests import test_postgres_configuration_acceptance as acceptance_fixture
from tests import test_execution_coordinator as coordinator_fixture
from tests.receiver_scope_history_fixture import admit_fixture_plan


class PostgresConfigurationAcceptanceLatestTests(unittest.TestCase):
    def setUp(self):
        # Compose the existing real command fixture without inheriting its tests.
        self.fixture = acceptance_fixture.PostgresConfigurationAcceptanceTests()
        self.addCleanup(self.cleanup_fixture)
        self.fixture.setUp()
        self.connection = self.fixture.connection
        self.first = self.fixture.advance()
        self.middle = self.advance(self.prepare("q9", "graph-current", 9))
        self.latest = self.advance(self.prepare("p10", "graph-desired", 10))
        p = ("graph-desired", self.fixture.projection.projection_id)
        q = ("graph-current", self.fixture.created.workspace.current_realized_projection_id)
        self.assertEqual([(r.to_authored_graph_id, r.to_realized_projection_id)
            for r in (self.first, self.middle, self.latest)], [p, q, p])
        self.assertEqual([r.desired_graph_revision for r in (self.first, self.middle, self.latest)], [1, 9, 10])
        self.assertEqual(len({r.action.action_id for r in (self.first, self.middle, self.latest)}), 3)
        self.assertEqual(len({r.event.event_id for r in (self.first, self.middle, self.latest)}), 3)
        with self.fixture.unit_of_work() as uow:
            current = uow.stores.workspaces.get("workspace-a")
        self.assertEqual((current.current_graph_id, current.current_realized_projection_id), p)

    def cleanup_fixture(self):
        self.assertTrue(self.fixture.doCleanups(), "nested acceptance fixture cleanup failed")

    def prepare(self, label, graph_id, revision):
        """Use real desired edits, admission, lifecycle and simulated effects."""
        operation = StartRuntime if graph_id == "graph-desired" else RemoveRuntimeResource
        activity_id = "activity-" + label
        plan = ActivityPlan((PlannedActivity(ActivityId(activity_id), operation(RuntimeTarget("runtime-a"))),))
        requirement = ApprovalPolicy().requirement_for(plan)
        with self.fixture.unit_of_work() as uow:
            stores = uow.stores
            workspace = stores.workspaces.get("workspace-a")
            self.assertLess(workspace.desired_graph_revision, revision)
            for _ in range(revision - workspace.desired_graph_revision):
                workspace = stores.workspaces.set_desired_graph("workspace-a", graph_id)
            self.assertEqual(workspace.desired_graph_revision, revision)
            stores.activity_history.add_session(OperationSessionRecord("session-" + label, "workspace-a",
                "operator-a", "Continue", OperationSessionStatus.OPEN, "2026-07-22T12:01:00Z"))
            stores.activity_history.add_plan(ActivityPlanRecord("plan-" + label, "session-" + label,
                workspace.current_graph_id, workspace.desired_graph_id, ActivityPlanStatus.PLANNED,
                "2026-07-22T12:02:00Z", plan, base_realized_projection_id=workspace.current_realized_projection_id,
                desired_realized_projection_id=workspace.desired_realized_projection_id,
                desired_graph_revision=workspace.desired_graph_revision))
            stores.activity_history.add_approval_request(ApprovalRequestRecord("approval-" + label,
                "session-" + label, ActivityPlanApprovalSubject("plan-" + label), "operator-a",
                "2026-07-22T12:03:00Z", requirement.required_scope, requirement.max_risk, requirement.destructive))
            stores.activity_history.add_approval_decision(ApprovalDecisionRecord("decision-" + label,
                "approval-" + label, "manager-a", ApprovalDecisionKind.APPROVED,
                requirement.required_scope, "2026-07-22T12:03:30Z"))
            uow.commit()
        admit_fixture_plan(self.fixture, request_id="request-" + label, session_id="session-" + label,
            plan_id="plan-" + label, approval_request_id="approval-" + label, key="admit-" + label)
        engine = self.fixture.engine
        opened = engine.lifecycle_with_ids("run-" + label, "open-" + label, "claim-" + label).execute(
            ClaimAndOpenActivityRun("request-" + label, engine.authority(), ExecutionLeaseDuration(600),
                IdempotencyKey("claim-" + label)))
        fence = ExecutionLeaseFence(opened.request.claim.worker_id, opened.request.claim.generation)
        engine.lifecycle_with_ids("start-event-" + label, "start-action-" + label).execute(
            StartActivityRun("run-" + label, engine.authority(), fence, IdempotencyKey("start-" + label)))
        # No provider is called; the existing adapter returns correlated success.
        adapter = coordinator_fixture.RecordingAdapter(engine.tracker, lambda _context, request:
            RuntimeEffectResult.succeeded(request.effect_id, evidence={"adapter": "test"}))
        result = engine.coordinator(adapter).execute(replace(engine.command(generation=fence.generation,
            idempotency_key="execute-" + label), run_id="run-" + label))
        self.assertIs(result.status, CoordinatorStatus.COMPLETED)
        self.assertEqual(adapter.calls, [activity_id])
        self.assertEqual(adapter.active_during_calls, [0])
        with self.fixture.unit_of_work() as uow:
            self.assertIs(uow.stores.execution.get_run("run-" + label).status, ActivityRunStatus.SUCCEEDED)
        return AdvanceCurrentGraph("workspace-a", "run-" + label, "plan-" + label,
            workspace.current_graph_id, workspace.current_realized_projection_id,
            workspace.desired_graph_id, workspace.desired_realized_projection_id,
            revision, engine.authority(), fence, IdempotencyKey("advance-" + label))

    def advance(self, command):
        identities = iter(("event-advance-" + command.run_id, "action-advance-" + command.run_id))
        return CurrentGraphAdvancementCommandService(self.fixture.unit_of_work,
            clock=lambda: "2026-07-22T13:05:00Z", id_factory=lambda: next(identities)).execute(command)

    def test_numeric_newest_pair_supports_real_continuation_and_schema_reentry(self):
        result = self.advance(self.prepare("q100", "graph-current", 100))
        self.assertEqual(result.desired_graph_revision, 100)
        self.assertEqual((result.to_authored_graph_id, result.to_realized_projection_id),
            (self.middle.to_authored_graph_id, self.middle.to_realized_projection_id))
        for table, key, kind, expected in (
            ("cpk_operation_actions", "action_id", "action_type='advance-current-graph'", result.action.action_id),
            ("cpk_activity_events", "event_id", "event_type='current_graph_advanced'", result.event.event_id),
        ):
            rows = self.connection.execute(f"SELECT {key},advancement_revision FROM {table} WHERE {kind} "
                "AND advancement_workspace_id='workspace-a' ORDER BY advancement_revision DESC").fetchall()
            self.assertEqual([row[1] for row in rows], [100, 10, 9, 1])
            self.assertEqual(rows[0][0], expected)
        before = self.fixture.retained_snapshot()
        install_schema(self.connection)
        self.assertEqual(self.fixture.retained_snapshot(), before)

    def refuse_corruption(self, statements):
        # Each test has an independent genuine prefix. Corruption is committed
        # so the ordinary command UoW observes it; fixture cleanup owns removal.
        command = self.prepare("q11", "graph-current", 11)
        original = self.fixture.retained_snapshot()
        older = self.connection.execute("SELECT * FROM cpk_configuration_acceptances "
            "WHERE workspace_id='workspace-a' AND pinned_revision=1").fetchone()
        self.assertIsNotNone(older)
        with self.connection.transaction():
            for query, parameters in statements:
                self.connection.execute(query, parameters)
        before = self.fixture.retained_snapshot()
        self.assertNotEqual(before, original)
        self.assertEqual(self.connection.execute("SELECT * FROM cpk_configuration_acceptances "
            "WHERE workspace_id='workspace-a' AND pinned_revision=1").fetchone(), older)
        with self.assertRaises(CurrentGraphAdvancementConflict) as caught:
            CurrentGraphAdvancementCommandService(self.fixture.unit_of_work,
                clock=lambda: self.fail("corrupt newest receipt sampled time"),
                id_factory=lambda: self.fail("corrupt newest receipt allocated identity")).execute(command)
        self.assertIs(type(caught.exception), CurrentGraphAdvancementConflict)
        self.assertLessEqual(len(str(caught.exception)), 512)
        self.assertNotIn("untrusted-acceptance-marker", str(caught.exception))
        self.assertEqual(self.fixture.retained_snapshot(), before)

    def remove_header(self):
        return ("DELETE FROM cpk_configuration_acceptances WHERE action_id=%s", (self.latest.action.action_id,))

    def test_newest_missing_header_refuses_older_matching_pair(self):
        self.refuse_corruption((self.remove_header(),))

    def test_newest_missing_action_refuses_older_matching_pair(self):
        self.refuse_corruption((self.remove_header(),
            ("DELETE FROM cpk_operation_actions WHERE action_id=%s", (self.latest.action.action_id,))))

    def test_newest_missing_event_refuses_older_matching_pair(self):
        self.refuse_corruption((self.remove_header(),
            ("DELETE FROM cpk_activity_events WHERE event_id=%s", (self.latest.event.event_id,))))

    def test_duplicate_highest_action_refuses_first_valid_candidate(self):
        self.refuse_corruption((("INSERT INTO cpk_operation_actions (action_id,session_id,ordinal,action_type,"
            "actor_id,payload,created_at,idempotency_key,intent_fingerprint,advancement_workspace_id,"
            "advancement_request_id,advancement_plan_id,advancement_run_id,advancement_revision) "
            "SELECT 'zz-duplicate-action',session_id,ordinal+1,action_type,actor_id,payload,created_at,"
            "'duplicate-action-key',intent_fingerprint,advancement_workspace_id,advancement_request_id,"
            "advancement_plan_id,advancement_run_id,advancement_revision FROM cpk_operation_actions WHERE action_id=%s",
            (self.latest.action.action_id,)),))

    def test_duplicate_highest_event_refuses_first_valid_candidate(self):
        self.refuse_corruption((("INSERT INTO cpk_activity_events (event_id,run_id,ordinal,event_type,occurred_at,payload,"
            "advancement_workspace_id,advancement_request_id,advancement_plan_id,advancement_revision) "
            "SELECT 'zz-duplicate-event',run_id,ordinal+1,event_type,occurred_at,payload,advancement_workspace_id,"
            "advancement_request_id,advancement_plan_id,advancement_revision FROM cpk_activity_events WHERE event_id=%s",
            (self.latest.event.event_id,)),))

    def test_newest_wrong_payload_scope_refuses_older_matching_pair(self):
        self.refuse_corruption((("UPDATE cpk_operation_actions SET payload=jsonb_set(payload,'{workspace_id}',"
            "to_jsonb('untrusted-acceptance-marker'::text)) WHERE action_id=%s", (self.latest.action.action_id,)),))
