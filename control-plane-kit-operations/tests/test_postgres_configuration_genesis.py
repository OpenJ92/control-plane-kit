"""Real E7 first birth after a same-run simulated runtime predecessor."""
from dataclasses import replace
from itertools import count
import unittest

from control_plane_kit_core.approval_subjects import ActivityPlanApprovalSubject
from control_plane_kit_core.operations import EffectAttemptIdentity, EffectAttemptStatus, RunId
from control_plane_kit_core.operations.lifecycle import ActivityRunStatus
from control_plane_kit_core.planning import (
    ActivityDependency, ActivityId, ActivityPlan, NodeTarget, PlannedActivity,
    RuntimeTarget, StartNode, StartRuntime,
)
from control_plane_kit_core.planning.saga import derive_schedule, project_activity_journal
from control_plane_kit_core.policies import ApprovalPolicy
from control_plane_kit_core.runtime_effect_observation import runtime_effect_intent_fingerprint
from control_plane_kit_core.runtime_effects import RuntimeEffectResult
from control_plane_kit_operations.activity_journal import activity_journal_events
from control_plane_kit_operations.coordinator import CoordinatorStatus
from control_plane_kit_operations.effect_attempt_start import EffectAttemptStartConflict, NewlyStarted
from control_plane_kit_operations.execution_leases import ExecutionLeaseFence
from control_plane_kit_operations.lifecycle import ClaimAndOpenActivityRun, ExecutionLeaseDuration, StartActivityRun
from control_plane_kit_operations.postgres import install_schema
from control_plane_kit_operations.records import (
    ActivityPlanRecord, ActivityPlanStatus, ApprovalDecisionKind, ApprovalDecisionRecord,
    ApprovalRequestRecord, GraphVersionRecord, OperationSessionRecord, OperationSessionStatus,
)
from control_plane_kit_operations.workflows import IdempotencyKey
from control_plane_kit_operations.workspaces import CreateWorkspace, WorkspaceCommandService
from tests import test_execution_coordinator as coordinator_fixture
from tests.configuration_preparation_fixture import ConfigurationPreparationFixture
from tests.execution_lease_recovery_fixture import PostgresExecutionLeaseRecoveryFixture
from tests.receiver_scope_history_fixture import admit_fixture_plan
from tests import test_postgres_configuration_preparation as preparation_fixture
from tests.test_runtime_effect_translation import _configuration_graph, _configuration_product


class PostgresConfigurationGenesisTests(ConfigurationPreparationFixture, unittest.TestCase):
    def authority_snapshot(self):
        return (preparation_fixture.PostgresConfigurationPreparationTests.authority_snapshot(self),
            self.connection.execute("SELECT * FROM cpk_effect_attempt_outcomes "
                "ORDER BY run_id,activity_id,attempt,direct_event_id").fetchall())

    def setUp(self):
        # Only connection/schema/cleanup composition; do not invoke the
        # accepted-runtime fixture or seed a successful predecessor journal.
        PostgresExecutionLeaseRecoveryFixture.setUp(self)
        WorkspaceCommandService(self.unit_of_work,
            clock=lambda: "2026-07-22T12:00:00Z", id_factory=lambda: "graph-initial").create(
                CreateWorkspace("workspace-a", "Configuration genesis", "operator-a", IdempotencyKey("genesis-create")))
        product = _configuration_product()
        desired = _configuration_graph(product)
        runtime = PlannedActivity(ActivityId("start-runtime"), StartRuntime(RuntimeTarget("docker")))
        self.configuration_activity = PlannedActivity(ActivityId("start-api"), StartNode(NodeTarget("api")),
            dependencies=(ActivityDependency(runtime.activity_id),))
        plan = ActivityPlan((runtime, self.configuration_activity))
        requirement = ApprovalPolicy().requirement_for(plan)
        with self.unit_of_work() as uow:
            stores = uow.stores
            self.configuration_product = stores.registered_products.register(workspace_id="workspace-a",
                descriptor_document=product.descriptor_document, source=product.source,
                imported_by=product.imported_by, imported_at=product.imported_at)
            stores.graphs.save(GraphVersionRecord.from_graph(graph_id="graph-desired", workspace_id="workspace-a",
                version=2, graph=desired, created_by="operator-a", created_at="2026-07-22T12:00:30Z"))
            self.workspace = stores.workspaces.set_desired_graph("workspace-a", "graph-desired")
            stores.activity_history.add_session(OperationSessionRecord("session-a", "workspace-a", "operator-a",
                "Initial runtime and node", OperationSessionStatus.OPEN, "2026-07-22T12:01:00Z"))
            stores.activity_history.add_plan(ActivityPlanRecord("plan-a", "session-a", "graph-initial", "graph-desired",
                ActivityPlanStatus.PLANNED, "2026-07-22T12:02:00Z", plan,
                base_realized_projection_id=self.workspace.current_realized_projection_id,
                desired_realized_projection_id=self.workspace.desired_realized_projection_id,
                desired_graph_revision=self.workspace.desired_graph_revision))
            stores.activity_history.add_approval_request(ApprovalRequestRecord("approval-request-a", "session-a",
                ActivityPlanApprovalSubject("plan-a"), "operator-a", "2026-07-22T12:03:00Z",
                requirement.required_scope, requirement.max_risk, requirement.destructive))
            stores.activity_history.add_approval_decision(ApprovalDecisionRecord("approval-decision-a", "approval-request-a",
                "manager-a", ApprovalDecisionKind.APPROVED, requirement.required_scope, "2026-07-22T12:03:30Z"))
            uow.commit()
        admit_fixture_plan(self)
        engine = coordinator_fixture.ExecutionCoordinatorTests()
        engine.database_url, engine.connection = self.database_url, self.connection
        identities = count(1)
        engine.ids = lambda: "genesis-generated-" + str(next(identities))
        engine.tracker = coordinator_fixture.TrackingUnitOfWorkFactory(self.database_url)
        opened = engine.lifecycle_with_ids("run-a", "genesis-open", "genesis-claim").execute(
            ClaimAndOpenActivityRun("request-a", engine.authority(), ExecutionLeaseDuration(600), IdempotencyKey("genesis-claim")))
        self.seeded_fence = ExecutionLeaseFence(opened.request.claim.worker_id, opened.request.claim.generation)
        engine.lifecycle_with_ids("genesis-start-event", "genesis-start-action").execute(
            StartActivityRun("run-a", engine.authority(), self.seeded_fence, IdempotencyKey("genesis-start")))
        adapter = coordinator_fixture.RecordingAdapter(engine.tracker, lambda _context, request:
            RuntimeEffectResult.succeeded(request.effect_id, evidence={"adapter": "genesis-runtime-test"}))
        result = engine.coordinator(adapter).execute(engine.command(generation=self.seeded_fence.generation,
            idempotency_key="genesis-runtime", max_effects=1))
        self.assertIs(result.status, CoordinatorStatus.PROGRESSED)
        self.assertEqual((result.effects_attempted, adapter.calls, adapter.active_during_calls),
            (1, ["start-runtime"], [0]))
        with self.unit_of_work() as uow:
            stores = uow.stores
            run = stores.execution.get_run("run-a")
            self.assertIs(run.status, ActivityRunStatus.RUNNING)
            journal = project_activity_journal(plan, activity_journal_events(stores.execution.events_for_run("run-a")))
            self.assertEqual(tuple(activity.activity_id.value for activity in derive_schedule(plan, journal.state).ready),
                ("start-api",))
            identity = EffectAttemptIdentity(RunId("run-a"), "start-runtime", 1)
            original = stores.effect_attempt_intents.get(identity)
            attempt = stores.effect_attempts.get(identity)
            outcome = stores.effect_outcomes.get(identity, attempt.latest_transition_event.event_id)
            self.assertIs(attempt.state.status, EffectAttemptStatus.SUCCEEDED)
            self.assertEqual(attempt.state.request_fingerprint, runtime_effect_intent_fingerprint(original.intent))
            self.assertEqual(outcome.outcome.identity, identity)
            self.assertEqual(outcome.outcome.request_fingerprint, attempt.state.request_fingerprint)
            self.assertEqual(outcome.outcome.result.effect_id, original.original_start_event.event_id)
            self.assertEqual(stores.workspaces.get("workspace-a"), self.workspace)
            current = stores.configuration_acceptance.read_current_configuration("workspace-a")
        self.assertEqual((current.state, current.graph_id, current.projection_id, current.pinned_revision,
            current.manifest_slot_count, current.bindings),
            ("complete", "graph-initial", self.workspace.current_realized_projection_id, None, 0, ()))
        for table, predicate in (("cpk_configuration_acceptances", "workspace_id='workspace-a'"),
                ("cpk_operation_actions", "action_type='advance-current-graph'"),
                ("cpk_activity_events", "event_type='current_graph_advanced'")):
            self.assertEqual(self.connection.execute(f"SELECT count(*) FROM {table} WHERE {predicate}").fetchone(), (0,))
        self.assertEqual(self.protection_rows(), ([], []))
        # Capture valid pinned material while the real initialization is intact.
        self.start_command = replace(self.configuration_command(), fence=self.seeded_fence)

    def test_real_empty_origin_admits_first_birth_after_same_run_runtime_success(self):
        result = self.start_service("genesis-node-start").execute(self.start_command)
        self.assertIsInstance(result, NewlyStarted)
        refs, claims = self.protection_rows()
        expected = [("run-a", "start-api", 1, ref.artifact_id, ref.workspace_id, ref.allocation_id)
            for ref in sorted(self.start_command.intent.configuration_instances.instances, key=lambda ref: ref.artifact_id)]
        self.assertEqual(claims, expected)
        self.assertEqual([row[:6] for row in refs], expected)
        self.assertTrue(all(row[11] is True and row[12:16] == ("run-a", "start-api", 1, row[3]) for row in refs))
        with self.unit_of_work() as uow:
            self.assertEqual(uow.stores.workspaces.get("workspace-a"), self.workspace)
            self.assertEqual(uow.stores.effect_attempt_intents.get(result.attempt.state.identity).intent, self.start_command.intent)
        before = self.authority_snapshot()
        install_schema(self.connection)
        self.assertEqual(self.authority_snapshot(), before)

    def refuse_initialization(self, *, missing):
        original = self.connection.execute("SELECT * FROM cpk_workspace_initializations WHERE workspace_id=%s",
            ("workspace-a",)).fetchone()
        self.assertIsNotNone(original)
        if missing:
            rows = self.connection.execute("DELETE FROM cpk_workspace_initializations WHERE workspace_id=%s RETURNING *",
                ("workspace-a",)).fetchall()
            self.assertEqual(rows, [original])
        else:
            rows = self.connection.execute("UPDATE cpk_workspace_initializations SET graph_descriptor_sha256=%s "
                "WHERE workspace_id=%s RETURNING graph_descriptor_sha256", ("a" * 64, "workspace-a")).fetchall()
            self.assertEqual(rows, [("a" * 64,)])
            self.assertNotEqual(original[4], "a" * 64)
        before = self.authority_snapshot()
        service, ids = self.start_service_with_sequence("must-not-be-used")
        with self.reject_database_observation("invalid initialization sampled database time"):
            with self.assertRaises(EffectAttemptStartConflict) as caught:
                service.execute(self.start_command)
        self.assert_safe_error(caught.exception)
        self.assertEqual(ids.calls, [])
        self.assertEqual(self.authority_snapshot(), before)

    def test_missing_real_initialization_refuses_before_ids_or_writes(self):
        self.refuse_initialization(missing=True)

    def test_corrupt_real_initialization_refuses_before_ids_or_writes(self):
        self.refuse_initialization(missing=False)
