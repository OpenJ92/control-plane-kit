"""Carry and removal through genuine later accepted graph occurrences."""
from dataclasses import replace
from hashlib import sha256
import unittest

import rfc8785

from control_plane_kit_core.algebra import DeploymentTopology, DockerRuntime
from control_plane_kit_core.approval_subjects import ActivityPlanApprovalSubject
from control_plane_kit_core.operations.lifecycle import ActivityRunStatus
from control_plane_kit_core.planning import (
    ActivityId, ActivityImpact, ActivityPlan, NodeTarget, PlannedActivity, ReconcileNode, RemoveNodeResource,
    RemoveRuntimeResource, RiskLevel, RuntimeTarget, StartRuntime,
)
from control_plane_kit_core.policies import ApprovalPolicy, PolicyScope
from control_plane_kit_core.runtime_effects import RuntimeEffectResult
from control_plane_kit_core.topology import DEFAULT_GRAPH_CODEC, compile_topology
from control_plane_kit_operations.advancement import (
    AdvanceCurrentGraph, CurrentGraphAdvancementCommandService, CurrentGraphAdvancementConflict,
)
from control_plane_kit_operations.coordinator import CoordinatorStatus
from control_plane_kit_operations.execution_leases import ExecutionLeaseFence
from control_plane_kit_operations.lifecycle import ClaimAndOpenActivityRun, ExecutionLeaseDuration, RunLifecycleCommandService, StartActivityRun
from control_plane_kit_operations.postgres import SchemaInstallationError, install_schema
from control_plane_kit_operations.records import (
    ActivityPlanRecord, ActivityPlanStatus, ApprovalDecisionKind, ApprovalDecisionRecord,
    ApprovalRequestRecord, GraphVersionRecord, OperationSessionRecord, OperationSessionStatus,
)
from control_plane_kit_operations.workflows import IdempotencyKey
from tests import test_execution_coordinator as coordinator_fixture
from tests import test_postgres_configuration_current_reads as read_fixture
from tests.receiver_scope_history_fixture import admit_fixture_plan


class PostgresConfigurationCarryTests(unittest.TestCase):
    def setUp(self):
        self.reader = read_fixture.PostgresConfigurationCurrentReadTests()
        self.reader.node_ids = getattr(self, "node_ids", ("api", "worker"))
        self.reader.registered_product = getattr(self, "registered_product", None)
        self.addCleanup(self.cleanup_fixture)
        self.reader.setUp()
        self.fixture, self.base = self.reader.fixture, self.reader.base
        self.original_acceptance = self.fixture.advance()
        with self.base.unit_of_work() as uow:
            self.graph = DEFAULT_GRAPH_CODEC.decode(uow.stores.graphs.get("graph-configured").graph_descriptor)
        self.graph_version = 3
        self.assert_membership(self.original_acceptance, self.fixture.refs)

    def cleanup_fixture(self):
        self.assertTrue(self.reader.doCleanups(), "nested carry fixture cleanup failed")

    def admit(self, label, graph_id, operation, *, graph=None, plan=None, reuse_selected_desired=False, clock=None):
        activity_id = "activity-" + label
        destructive = type(operation) in (RemoveNodeResource, RemoveRuntimeResource)
        reconcile = type(operation) is ReconcileNode
        risk = RiskLevel.HIGH if destructive else RiskLevel.MEDIUM if reconcile else RiskLevel.LOW
        impact = ActivityImpact.DESTRUCTIVE if destructive else ActivityImpact.DISRUPTIVE if reconcile else ActivityImpact.NON_DESTRUCTIVE
        plan = plan or ActivityPlan((PlannedActivity(ActivityId(activity_id), operation,
            risk=risk, impact=impact),))
        requirement = ApprovalPolicy().requirement_for(plan)
        self.assertEqual(requirement.destructive, destructive)
        self.assertIs(requirement.max_risk, risk)
        self.assertIs(requirement.required_scope,
            PolicyScope.PLAN_APPROVE_DESTRUCTIVE if destructive else PolicyScope.PLAN_APPROVE)
        with self.base.unit_of_work() as uow:
            stores = uow.stores
            if graph is not None:
                self.graph_version += 1
                stores.graphs.save(GraphVersionRecord.from_graph(graph_id=graph_id, workspace_id="workspace-a",
                    version=self.graph_version, graph=graph, created_by="operator-a", created_at="2026-07-22T12:00:30Z"))
            if reuse_selected_desired:
                self.assertIsNone(graph, "already selected desired graph must not be replaced")
                workspace = stores.workspaces.get("workspace-a")
                self.assertEqual(workspace.desired_graph_id, graph_id)
            else:
                workspace = stores.workspaces.set_desired_graph("workspace-a", graph_id)
            stores.activity_history.add_session(OperationSessionRecord("session-" + label, "workspace-a", "operator-a",
                "Change unrelated material or remove a node", OperationSessionStatus.OPEN, "2026-07-22T12:01:00Z"))
            stores.activity_history.add_plan(ActivityPlanRecord("plan-" + label, "session-" + label,
                workspace.current_graph_id, workspace.desired_graph_id, ActivityPlanStatus.PLANNED,
                "2026-07-22T12:02:00Z", plan, base_realized_projection_id=workspace.current_realized_projection_id,
                desired_realized_projection_id=workspace.desired_realized_projection_id,
                desired_graph_revision=workspace.desired_graph_revision))
            stores.activity_history.add_approval_request(ApprovalRequestRecord("approval-" + label, "session-" + label,
                ActivityPlanApprovalSubject("plan-" + label), "operator-a", "2026-07-22T12:03:00Z",
                requirement.required_scope, requirement.max_risk, requirement.destructive))
            stores.activity_history.add_approval_decision(ApprovalDecisionRecord("decision-" + label, "approval-" + label,
                "manager-a", ApprovalDecisionKind.APPROVED, requirement.required_scope, "2026-07-22T12:03:30Z"))
            uow.commit()
        admit_fixture_plan(self.base, request_id="request-" + label, session_id="session-" + label,
            plan_id="plan-" + label, approval_request_id="approval-" + label, key="admit-" + label)
        engine = self.base.engine
        def lifecycle(*ids):
            return (engine.lifecycle_with_ids(*ids) if clock is None else
                RunLifecycleCommandService(self.base.unit_of_work, clock=clock, id_factory=iter(ids).__next__))

        opened = lifecycle("run-" + label, "open-" + label, "claim-" + label).execute(
            ClaimAndOpenActivityRun("request-" + label, engine.authority(), ExecutionLeaseDuration(3600 if clock else 600),
                IdempotencyKey("claim-" + label)))
        fence = ExecutionLeaseFence(opened.request.claim.worker_id, opened.request.claim.generation)
        lifecycle("start-event-" + label, "start-action-" + label).execute(
            StartActivityRun("run-" + label, engine.authority(), fence, IdempotencyKey("start-" + label)))
        return AdvanceCurrentGraph("workspace-a", "run-" + label, "plan-" + label,
            workspace.current_graph_id, workspace.current_realized_projection_id, workspace.desired_graph_id,
            workspace.desired_realized_projection_id, workspace.desired_graph_revision, engine.authority(), fence,
            IdempotencyKey("advance-" + label))

    def prepare(self, label, graph_id, operation, *, graph=None, expected_claims=None):
        command = self.admit(label, graph_id, operation, graph=graph)
        engine = self.base.engine
        adapter = coordinator_fixture.RecordingAdapter(engine.tracker, lambda _context, request:
            RuntimeEffectResult.succeeded(request.effect_id, evidence={"adapter": "carry-test"}))
        result = engine.coordinator(adapter).execute(replace(engine.command(generation=command.fence.generation,
            idempotency_key="execute-" + label), run_id="run-" + label))
        self.assertIs(result.status, CoordinatorStatus.COMPLETED)
        self.assertEqual(adapter.calls, ["activity-" + label])
        self.assertEqual(adapter.active_during_calls, [0])
        with self.base.unit_of_work() as uow:
            self.assertIs(uow.stores.execution.get_run("run-" + label).status, ActivityRunStatus.SUCCEEDED)
        self.assertEqual(self.fixture.protective_claims(),
            self.fixture.claims if expected_claims is None else expected_claims)
        return command

    def advance(self, command):
        identities = iter(("event-advance-" + command.run_id, "action-advance-" + command.run_id))
        try:
            return CurrentGraphAdvancementCommandService(self.base.unit_of_work,
                clock=lambda: "2026-07-22T13:05:00Z", id_factory=lambda: next(identities)).execute(command)
        except CurrentGraphAdvancementConflict as error:
            self.fail("completed later execution must carry unchanged accepted configuration: " + str(error))

    def add_runtime(self):
        extra = compile_topology(DeploymentTopology("extra", DockerRuntime(runtime_id="runtime-b")))
        graph = self.graph.add_runtime(extra.runtimes["runtime-b"])
        self.assertEqual(graph.nodes, self.graph.nodes)
        return self.advance(self.prepare("add-runtime", "graph-extra-runtime", StartRuntime(RuntimeTarget("runtime-b")),
            graph=graph))

    def carry_twice(self):
        middle = self.add_runtime()
        self.assert_membership(middle, self.fixture.refs)
        latest = self.advance(self.prepare("remove-runtime", "graph-configured", RemoveRuntimeResource(RuntimeTarget("runtime-b"))))
        self.assert_membership(latest, self.fixture.refs)
        self.assertEqual((latest.to_authored_graph_id, latest.to_realized_projection_id),
            (self.original_acceptance.to_authored_graph_id, self.original_acceptance.to_realized_projection_id))
        self.assertEqual(len({value.desired_graph_revision for value in (self.original_acceptance, middle, latest)}), 3)
        self.assertEqual(len({value.event.event_id for value in (self.original_acceptance, middle, latest)}), 3)
        self.assertEqual(len({value.action.action_id for value in (self.original_acceptance, middle, latest)}), 3)
        return middle, latest

    def assert_membership(self, result, refs):
        selected = {(ref.runtime_id, ref.node_id, ref.artifact_id) for ref in refs}
        expected = tuple(row for row in self.fixture.expected_slots if row[:3] in selected)
        rows = self.base.connection.execute("SELECT runtime_id,node_id,artifact_id,source_run_id,source_activity_id,source_attempt,"
            "source_artifact_id,birth_run_id,birth_activity_id,birth_attempt,birth_artifact_id,full_ref_digest "
            "FROM cpk_configuration_accepted_slots WHERE workspace_id='workspace-a' AND pinned_revision=%s "
            "ORDER BY runtime_id,node_id,artifact_id", (result.desired_graph_revision,)).fetchall()
        self.assertEqual(tuple(rows), expected)
        digest = sha256(rfc8785.dumps([[list(row[:3]), list(row[3:7]), list(row[7:11]), row[11]] for row in expected])).hexdigest()
        header = self.base.connection.execute("SELECT slot_count,slot_digest,action_id,event_id FROM cpk_configuration_acceptances "
            "WHERE workspace_id='workspace-a' AND pinned_revision=%s", (result.desired_graph_revision,)).fetchone()
        self.assertEqual(header, (len(refs), digest, result.action.action_id, result.event.event_id))
        for value in (self.reader.read_current(), self.reader.read_use(refs)):
            self.assertEqual((value.state, value.pinned_revision, value.manifest_slot_count),
                ("complete", result.desired_graph_revision, len(refs)))
            self.assertEqual(tuple(binding.ref for binding in value.bindings), refs)
            for binding in value.bindings:
                self.assertEqual(binding.source.identity, self.fixture.originals[binding.ref.node_id].identity)
                self.assertEqual(binding.birth.identity, binding.source.identity)
        self.assertEqual(self.fixture.protective_claims(), self.fixture.claims)

    def test_unrelated_runtime_changes_carry_original_membership_across_occurrences(self):
        self.carry_twice()
        before = self.base.retained_snapshot()
        install_schema(self.base.connection)
        self.assertEqual(self.base.retained_snapshot(), before)
        self.assertEqual(self.fixture.protective_claims(), self.fixture.claims)

    def test_partial_removal_retains_unchanged_binding_and_every_removed_claim(self):
        self.assertEqual(self.graph.edges, {})
        runtime = replace(self.graph.runtimes["runtime-a"], children=("api",))
        graph = replace(self.graph, nodes={"api": self.graph.nodes["api"]}, runtimes={"runtime-a": runtime})
        self.assertEqual(DEFAULT_GRAPH_CODEC.decode(DEFAULT_GRAPH_CODEC.encode(graph)), graph)
        result = self.advance(self.prepare("remove-worker", "graph-api-only", RemoveNodeResource(NodeTarget("worker")), graph=graph))
        api_refs = self.fixture.originals["api"].intent.configuration_instances.instances
        worker_refs = self.fixture.originals["worker"].intent.configuration_instances.instances
        self.assert_membership(result, api_refs)
        before = self.base.retained_snapshot()
        absent = self.reader.read_use(worker_refs)
        self.assertEqual((absent.state, absent.pinned_revision, absent.manifest_slot_count, absent.bindings),
            ("complete", result.desired_graph_revision, 2, ()))
        self.assertEqual(self.base.retained_snapshot(), before)
        self.assertEqual(self.fixture.protective_claims(), self.fixture.claims)
        install_schema(self.base.connection)

    def test_missing_intermediate_header_does_not_create_a_carry_chain(self):
        middle, latest = self.carry_twice()
        self.remove_occurrence(middle)
        before = self.base.retained_snapshot()
        self.assert_membership(latest, self.fixture.refs)
        # Current requested provenance is direct; the separate schema-wide
        # contract must still reject the deliberately missing retained receipt.
        with self.assertRaisesRegex(SchemaInstallationError, "^operations schema reset is required$"):
            install_schema(self.base.connection)
        self.assertEqual(self.base.retained_snapshot(), before)

    def test_missing_original_acceptance_refuses_requested_carried_bindings(self):
        result = self.add_runtime()
        self.assert_membership(result, self.fixture.refs)
        self.remove_occurrence(self.original_acceptance)
        self.assertEqual(self.base.connection.execute("SELECT count(*) FROM cpk_effect_attempt_outcomes "
            "WHERE run_id='run-config'").fetchone(), (2,))
        before = self.base.retained_snapshot()
        api_refs = self.fixture.originals["api"].intent.configuration_instances.instances
        self.reader.assert_unavailable(self.reader.read_current(node_id="api"))
        self.reader.assert_unavailable(self.reader.read_use(api_refs))
        self.assertEqual(self.base.retained_snapshot(), before)
        self.assertEqual(self.fixture.protective_claims(), self.fixture.claims)

    def remove_occurrence(self, result):
        # Deliberate missing occurrence header + owned membership. Foreign-key
        # order is explicit; immutable original/source/protection rows survive.
        def retained_originals():
            return tuple(self.base.connection.execute(query).fetchall() for query in (
                "SELECT * FROM cpk_workspaces ORDER BY workspace_id",
                "SELECT * FROM cpk_operation_actions ORDER BY action_id",
                "SELECT * FROM cpk_activity_events ORDER BY event_id",
                "SELECT * FROM cpk_effect_attempt_outcomes ORDER BY run_id,activity_id,attempt",
                "SELECT * FROM cpk_effect_attempt_intents ORDER BY run_id,activity_id,attempt",
                "SELECT * FROM cpk_effect_configuration_refs ORDER BY run_id,activity_id,attempt,artifact_id",
                "SELECT * FROM cpk_configuration_claims ORDER BY run_id,activity_id,attempt,artifact_id"))

        original = retained_originals()
        with self.base.connection.transaction():
            self.base.connection.execute("DELETE FROM cpk_configuration_accepted_slots WHERE workspace_id='workspace-a' "
                "AND pinned_revision=%s", (result.desired_graph_revision,))
            self.base.connection.execute("DELETE FROM cpk_configuration_acceptances WHERE workspace_id='workspace-a' "
                "AND pinned_revision=%s", (result.desired_graph_revision,))
        for table in ("cpk_configuration_acceptances", "cpk_configuration_accepted_slots"):
            self.assertEqual(self.base.connection.execute("SELECT count(*) FROM " + table
                + " WHERE workspace_id='workspace-a' AND pinned_revision=%s",
                (result.desired_graph_revision,)).fetchone(), (0,))
        self.assertEqual(retained_originals(), original)
