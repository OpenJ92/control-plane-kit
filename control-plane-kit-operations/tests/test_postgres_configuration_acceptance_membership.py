"""Nonempty membership from actual new-kind start/fold/advancement owners."""
from dataclasses import replace
from hashlib import sha256
import unittest

import psycopg
import rfc8785

from control_plane_kit_core.algebra import DeploymentTopology, DockerRuntime
from control_plane_kit_core.approval_subjects import ActivityPlanApprovalSubject
from control_plane_kit_core.configuration_instances import ConfigurationInstanceRefCodec
from control_plane_kit_core.operations import EffectAttemptIdentity, EffectAttemptStatus, RunId
from control_plane_kit_core.operations.lifecycle import ActivityRunStatus
from control_plane_kit_core.planning import ActivityId, ActivityPlan, NodeTarget, PlannedActivity, StartNode
from control_plane_kit_core.policies import ApprovalPolicy
from control_plane_kit_core.products import ProductInstanceConfiguration, instantiate_product
from control_plane_kit_core.runtime_effect_observation import runtime_effect_intent_fingerprint
from control_plane_kit_core.runtime_effects import RuntimeEffectKind, RuntimeEffectResult
from control_plane_kit_core.topology import compile_topology
from control_plane_kit_operations.advancement import (
    AdvanceCurrentGraph, CurrentGraphAdvancementCommandService, CurrentGraphAdvancementConflict,
)
from control_plane_kit_operations.coordinator import CoordinatorStatus
from control_plane_kit_operations.effect_outcome_evidence import ExecutionEffectOutcome
from control_plane_kit_operations.execution_leases import ExecutionLeaseFence
from control_plane_kit_operations.lifecycle import ClaimAndOpenActivityRun, ExecutionLeaseDuration, StartActivityRun
from control_plane_kit_operations.postgres import PostgresUnitOfWork, SchemaInstallationError, install_schema
from control_plane_kit_operations.records import (
    ActivityPlanRecord, ActivityPlanStatus, ApprovalDecisionKind, ApprovalDecisionRecord,
    ApprovalRequestRecord, GraphVersionRecord, OperationSessionRecord, OperationSessionStatus,
)
from control_plane_kit_operations.workflows import IdempotencyKey
from tests import test_postgres_configuration_acceptance as acceptance_fixture
from tests import test_execution_coordinator as coordinator_fixture
from tests.receiver_scope_history_fixture import admit_fixture_plan
from tests.test_runtime_effect_translation import _configuration_product


class PostgresConfigurationAcceptanceMembershipTests(unittest.TestCase):
    node_ids = ("api",)

    def setUp(self):
        self.fixture = acceptance_fixture.PostgresConfigurationAcceptanceTests()
        self.addCleanup(self.cleanup_fixture)
        self.fixture.setUp()
        self.connection = self.fixture.connection
        self.fixture.advance()
        registered = getattr(self, "registered_product", None) or _configuration_product()
        product = registered.descriptor_document.product
        blocks = tuple(instantiate_product(product, node, ProductInstanceConfiguration.from_contract(product.runtime_contract))
            for node in self.node_ids)
        desired = compile_topology(DeploymentTopology("configured", DockerRuntime(runtime_id="runtime-a", children=blocks)))
        plan = ActivityPlan(tuple(PlannedActivity(ActivityId("start-" + node), StartNode(NodeTarget(node)))
            for node in self.node_ids))
        requirement = ApprovalPolicy().requirement_for(plan)
        with self.fixture.unit_of_work() as uow:
            stores = uow.stores
            stores.registered_products.register(workspace_id="workspace-a",
                descriptor_document=registered.descriptor_document, source=registered.source,
                imported_by=registered.imported_by, imported_at=registered.imported_at)
            stores.graphs.save(GraphVersionRecord.from_graph(graph_id="graph-configured", workspace_id="workspace-a",
                version=3, graph=desired, created_by="operator-a", created_at="2026-07-22T12:00:30Z"))
            self.workspace = stores.workspaces.set_desired_graph("workspace-a", "graph-configured")
            stores.activity_history.add_session(OperationSessionRecord("session-config", "workspace-a", "operator-a",
                "Install configuration", OperationSessionStatus.OPEN, "2026-07-22T12:01:00Z"))
            stores.activity_history.add_plan(ActivityPlanRecord("plan-config", "session-config",
                self.workspace.current_graph_id, "graph-configured", ActivityPlanStatus.PLANNED,
                "2026-07-22T12:02:00Z", plan, base_realized_projection_id=self.workspace.current_realized_projection_id,
                desired_realized_projection_id=self.workspace.desired_realized_projection_id,
                desired_graph_revision=self.workspace.desired_graph_revision))
            stores.activity_history.add_approval_request(ApprovalRequestRecord("approval-config", "session-config",
                ActivityPlanApprovalSubject("plan-config"), "operator-a", "2026-07-22T12:03:00Z",
                requirement.required_scope, requirement.max_risk, requirement.destructive))
            stores.activity_history.add_approval_decision(ApprovalDecisionRecord("decision-config", "approval-config",
                "manager-a", ApprovalDecisionKind.APPROVED, requirement.required_scope, "2026-07-22T12:03:30Z"))
            uow.commit()
        admit_fixture_plan(self.fixture, request_id="request-config", session_id="session-config", plan_id="plan-config",
            approval_request_id="approval-config", key="admit-config")
        engine = self.fixture.engine
        opened = engine.lifecycle_with_ids("run-config", "open-config", "claim-config").execute(
            ClaimAndOpenActivityRun("request-config", engine.authority(), ExecutionLeaseDuration(600), IdempotencyKey("claim-config")))
        self.fence = ExecutionLeaseFence(opened.request.claim.worker_id, opened.request.claim.generation)
        engine.lifecycle_with_ids("start-event-config", "start-action-config").execute(
            StartActivityRun("run-config", engine.authority(), self.fence, IdempotencyKey("start-config")))
        # This test producer promises total installation of the exact requested
        # selection. It is simulated evidence, not native/provider verification.
        def installed(_context, request):
            if (producer := getattr(self, "configuration_result_for_request", None)) is not None:
                return producer(request)
            return RuntimeEffectResult.succeeded(request.effect_id,
                evidence={"adapter": "total-selected-configuration-test"})

        adapter = coordinator_fixture.RecordingAdapter(engine.tracker, *(installed for _ in self.node_ids))
        result = engine.coordinator(adapter).execute(replace(engine.command(generation=self.fence.generation,
            idempotency_key="execute-config", max_effects=len(self.node_ids)), run_id="run-config"))
        self.assertIs(result.status, CoordinatorStatus.COMPLETED)
        self.assertEqual(adapter.calls, ["start-" + node for node in self.node_ids])
        self.assertEqual(adapter.active_during_calls, [0] * len(self.node_ids))
        self.originals, self.direct_events = {}, {}
        with self.fixture.unit_of_work() as uow:
            stores = uow.stores
            self.assertIs(stores.execution.get_run("run-config").status, ActivityRunStatus.SUCCEEDED)
            for node in self.node_ids:
                identity = EffectAttemptIdentity(RunId("run-config"), "start-" + node, 1)
                original = stores.effect_attempt_intents.get(identity)
                attempt = stores.effect_attempts.get(identity)
                outcome = stores.effect_outcomes.get(identity, attempt.latest_transition_event.event_id)
                self.originals[node] = original
                self.direct_events[node] = attempt.latest_transition_event.event_id
                self.assertIs(original.intent.kind, RuntimeEffectKind.CONFIGURATION_ACTIVITY_V1)
                self.assertIs(type(outcome.outcome), ExecutionEffectOutcome)
                self.assertIs(outcome.attempt.state.status, EffectAttemptStatus.SUCCEEDED)
                self.assertEqual(outcome.attempt, attempt)
                self.assertEqual(outcome.outcome.request_fingerprint, runtime_effect_intent_fingerprint(original.intent))
                self.assertEqual(outcome.outcome.result.effect_id, original.original_start_event.event_id)
                self.assertEqual(outcome.outcome.identity, original.identity)
        self.original, self.direct_event_id = self.originals["api"], self.direct_events["api"]
        refs = self.refs = tuple(ref for node in self.node_ids
            for ref in self.originals[node].intent.configuration_instances.instances)
        self.assertEqual({(ref.runtime_id, ref.node_id, ref.artifact_id) for ref in refs},
            {("runtime-a", node, artifact.artifact_id) for node in self.node_ids
                for artifact in product.runtime_contract.configuration_artifacts})
        raw_refs = self.connection.execute("SELECT activity_id,artifact_id,ref_preimage,birth_run_id,birth_activity_id,birth_attempt,"
            "birth_artifact_id,is_birth FROM cpk_effect_configuration_refs WHERE run_id='run-config' "
            "AND attempt=1 ORDER BY activity_id,artifact_id").fetchall()
        codec = ConfigurationInstanceRefCodec()
        self.assertEqual(raw_refs, [("start-" + ref.node_id, ref.artifact_id, codec.encode_canonical_bytes(ref), "run-config", "start-" + ref.node_id, 1,
            ref.artifact_id, True) for ref in refs])
        self.claims = self.protective_claims()
        self.assertEqual(len(self.claims), len(product.runtime_contract.configuration_artifacts) * len(self.node_ids))
        self.expected_slots = tuple((ref.runtime_id, ref.node_id, ref.artifact_id,
            "run-config", "start-" + ref.node_id, 1, ref.artifact_id, "run-config", "start-" + ref.node_id, 1, ref.artifact_id,
            sha256(codec.encode_canonical_bytes(ref)).hexdigest()) for ref in refs)

    def cleanup_fixture(self):
        self.assertTrue(self.fixture.doCleanups(), "nested acceptance fixture cleanup failed")

    def protective_claims(self):
        return self.connection.execute("SELECT * FROM cpk_configuration_claims "
            "ORDER BY run_id,activity_id,attempt,artifact_id").fetchall()

    def command(self):
        return AdvanceCurrentGraph("workspace-a", "run-config", "plan-config", self.workspace.current_graph_id,
            self.workspace.current_realized_projection_id, "graph-configured", self.workspace.desired_realized_projection_id,
            self.workspace.desired_graph_revision, self.fixture.engine.authority(), self.fence, IdempotencyKey("advance-config"))

    def advance(self, factory=None):
        ids = iter(("event-advance-config", "action-advance-config"))
        try:
            return CurrentGraphAdvancementCommandService(factory or self.fixture.unit_of_work,
                clock=lambda: "2026-07-22T13:05:00Z", id_factory=lambda: next(ids)).execute(self.command())
        except CurrentGraphAdvancementConflict as error:
            self.fail("real completed configuration installation must support complete acceptance: " + str(error))

    def test_direct_success_event_extra_envelope_field_refuses_before_publication(self):
        marker = "untrusted-direct-outcome-marker"
        self.connection.execute("UPDATE cpk_activity_events SET payload=payload || "
            "jsonb_build_object('unexpected',%s::text) WHERE event_id=%s", (marker, self.direct_event_id))
        before, sampled = self.fixture.retained_snapshot(), []
        ids = iter(("event-advance-config", "action-advance-config"))

        def clock():
            sampled.append("clock")
            return "2026-07-22T13:05:00Z"

        def identity():
            sampled.append("identity")
            return next(ids)

        with self.assertRaises(CurrentGraphAdvancementConflict) as caught:
            CurrentGraphAdvancementCommandService(self.fixture.unit_of_work,
                clock=clock, id_factory=identity).execute(self.command())
        self.assertIs(type(caught.exception), CurrentGraphAdvancementConflict)
        self.assertLessEqual(len(str(caught.exception)), 512)
        self.assertNotIn(marker, str(caught.exception))
        self.assertEqual(sampled, [])
        self.assertEqual(self.fixture.retained_snapshot(), before)
        self.assertEqual(self.protective_claims(), self.claims)

    def test_real_installation_accepts_complete_original_slots_and_retains_claims(self):
        result = self.advance()
        self.assertEqual((result.to_authored_graph_id, result.to_realized_projection_id),
            ("graph-configured", self.workspace.desired_realized_projection_id))
        rows = self.connection.execute("SELECT runtime_id,node_id,artifact_id,source_run_id,source_activity_id,source_attempt,"
            "source_artifact_id,birth_run_id,birth_activity_id,birth_attempt,birth_artifact_id,full_ref_digest "
            "FROM cpk_configuration_accepted_slots WHERE workspace_id='workspace-a' AND pinned_revision=%s "
            "ORDER BY runtime_id,node_id,artifact_id", (self.workspace.desired_graph_revision,)).fetchall()
        self.assertEqual(tuple(rows), self.expected_slots)
        membership = [[list(row[:3]), list(row[3:7]), list(row[7:11]), row[11]] for row in self.expected_slots]
        header = self.connection.execute("SELECT slot_count,slot_digest,action_id,event_id FROM cpk_configuration_acceptances "
            "WHERE workspace_id='workspace-a' AND pinned_revision=%s", (self.workspace.desired_graph_revision,)).fetchone()
        self.assertEqual(header, (2, sha256(rfc8785.dumps(membership)).hexdigest(), result.action.action_id, result.event.event_id))
        self.assertEqual(self.protective_claims(), self.claims)
        before = self.fixture.retained_snapshot()
        install_schema(self.connection)
        self.assertEqual(self.fixture.retained_snapshot(), before)
        self.assertEqual(self.protective_claims(), self.claims)

    def test_missing_accepted_slot_refuses_current_schema_without_repair(self):
        self.advance()
        self.connection.execute("DELETE FROM cpk_configuration_accepted_slots WHERE workspace_id='workspace-a' "
            "AND pinned_revision=%s AND artifact_id='settings'", (self.workspace.desired_graph_revision,))
        before = self.fixture.retained_snapshot()
        with self.assertRaisesRegex(SchemaInstallationError, "^operations schema reset is required$"):
            install_schema(self.connection)
        self.assertEqual(self.fixture.retained_snapshot(), before)
        self.assertEqual(self.protective_claims(), self.claims)

    def test_late_commit_failure_rolls_back_complete_slots_and_preserves_claims(self):
        before, tentative = self.fixture.retained_snapshot(), []

        class CommitFailure:
            def __init__(self, connection):
                self.connection = connection

            def __getattr__(self, name):
                return getattr(self.connection, name)

            def commit(self):
                tentative.append(self.connection.execute("SELECT artifact_id FROM cpk_configuration_accepted_slots "
                    "WHERE workspace_id='workspace-a' AND pinned_revision=2 ORDER BY artifact_id").fetchall())
                raise RuntimeError("injected nonempty acceptance commit failure")

        with self.assertRaisesRegex(RuntimeError, "^injected nonempty acceptance commit failure$"):
            self.advance(lambda: PostgresUnitOfWork(lambda: CommitFailure(psycopg.connect(self.fixture.database_url))))
        self.assertEqual(tentative, [[("limits",), ("settings",)]])
        self.assertEqual(self.fixture.retained_snapshot(), before)
        self.assertEqual(self.protective_claims(), self.claims)
