"""Real accepted P/ref1 -> Q -> P/ref2; simulated Operations effects only."""
from dataclasses import replace
import unittest

from control_plane_kit_core.algebra import DeploymentTopology, DockerRuntime
from control_plane_kit_core.configuration_instances import ConfigurationInstanceRefCodec, ConfigurationInstanceSelection
from control_plane_kit_core.environment import PublicStaticEnvironmentBinding
from control_plane_kit_core.operations import EffectAttemptIdentity, EffectAttemptTransition, EffectAttemptTransitionKind, RunId
from control_plane_kit_core.planning import NodeTarget, ReconcileNode, RemoveNodeResource, StartNode
from control_plane_kit_core.products import ProductInstanceConfiguration, instantiate_product
from control_plane_kit_core.runtime_effect_observation import runtime_effect_intent_fingerprint
from control_plane_kit_core.runtime_effects import RuntimeEffectKind
from control_plane_kit_core.topology import DEFAULT_GRAPH_CODEC, compile_topology
from control_plane_kit_operations.configuration_preparation import _birth_selection
from control_plane_kit_operations.coordinator import ExecutionCoordinatorConflict
from control_plane_kit_operations.effect_attempt_start import ExistingAttempt, StartEffectAttempt
from control_plane_kit_operations.effect_attempt_start_interpreter import EffectAttemptStartService
from control_plane_kit_operations.postgres import SchemaInstallationError, install_schema
from tests import test_postgres_configuration_carry as carry_fixture
from tests.test_runtime_effect_translation import _configuration_product


class PostgresConfigurationRecurrenceTests(unittest.TestCase):
    def setUp(self):
        self.carry = carry_fixture.PostgresConfigurationCarryTests()
        self.addCleanup(self.cleanup_fixture)
        self.carry.setUp()
        self.base, self.fixture, self.reader = self.carry.base, self.carry.fixture, self.carry.reader
        self.old_refs = self.fixture.originals["api"].intent.configuration_instances.instances
        self.worker_refs = self.fixture.originals["worker"].intent.configuration_instances.instances
        self.birth_identity = EffectAttemptIdentity(RunId("run-reenter"), "activity-reenter", 1)
        # Existing pure B1 proposal supplies exact expected claim coordinates;
        # this suite tests authority, distinct birth, membership and retention.
        self.new_refs = _birth_selection(self.birth_identity, ConfigurationInstanceSelection(self.old_refs)).instances
        self.assertNotEqual(self.new_refs, self.old_refs)
        self.assertTrue({ref.allocation_id for ref in self.new_refs}.isdisjoint(
            ref.allocation_id for ref in self.old_refs))
        self.assertEqual(tuple(replace(new, allocation_id=old.allocation_id)
            for new, old in zip(self.new_refs, self.old_refs, strict=True)), self.old_refs)
        self.claims = sorted(self.fixture.claims + self.claim_rows(self.birth_identity, self.new_refs))

    def cleanup_fixture(self):
        self.assertTrue(self.carry.doCleanups(), "nested recurrence fixture cleanup failed")

    def claim_rows(self, identity, refs):
        return [(identity.run_id.value, identity.activity_id, identity.attempt,
            ref.artifact_id, ref.workspace_id, ref.allocation_id) for ref in refs]

    def retained_state(self):
        return (self.base.retained_snapshot(), self.fixture.protective_claims(), tuple(
            (table, self.base.connection.execute(f"SELECT * FROM {table} ORDER BY run_id,activity_id,attempt").fetchall())
            for table in ("cpk_effect_attempts", "cpk_effect_attempt_intents", "cpk_effect_attempt_outcomes")),
            self.base.connection.execute("SELECT * FROM cpk_effect_configuration_refs "
                "ORDER BY run_id,activity_id,attempt,artifact_id").fetchall())

    def reenter(self):
        graph = self.carry.graph
        self.assertEqual(graph.edges, {})
        departed = replace(graph, nodes={"worker": graph.nodes["worker"]}, runtimes={"runtime-a":
            replace(graph.runtimes["runtime-a"], children=("worker",))})
        self.assertEqual(DEFAULT_GRAPH_CODEC.decode(DEFAULT_GRAPH_CODEC.encode(departed)), departed)
        self.middle = self.carry.advance(self.carry.prepare("depart", "graph-worker-only",
            RemoveNodeResource(NodeTarget("api")), graph=departed))
        current = self.reader.read_current()
        self.assertEqual((current.state, current.graph_id, current.pinned_revision, current.manifest_slot_count),
            ("complete", "graph-worker-only", self.middle.desired_graph_revision, 2))
        self.assertEqual(tuple(binding.ref for binding in current.bindings), self.worker_refs)
        absent = self.reader.read_use(self.old_refs)
        self.assertEqual((absent.state, absent.bindings), ("complete", ()))
        self.assertEqual(self.fixture.protective_claims(), self.fixture.claims)
        try:
            command = self.carry.prepare("reenter", "graph-configured", StartNode(NodeTarget("api")),
                expected_claims=self.claims)
        except ExecutionCoordinatorConflict as error:
            self.fail("real accepted node departure must permit a distinct guarded birth: " + str(error))
        with self.base.unit_of_work() as uow:
            self.original = uow.stores.effect_attempt_intents.get(self.birth_identity)
            attempt = uow.stores.effect_attempts.get(self.birth_identity)
            outcome = uow.stores.effect_outcomes.get(self.birth_identity, attempt.latest_transition_event.event_id)
        self.assertIs(self.original.intent.kind, RuntimeEffectKind.CONFIGURATION_ACTIVITY_V1)
        self.assertEqual(self.original.intent.configuration_instances.instances, self.new_refs)
        self.assertEqual(attempt.state.request_fingerprint, runtime_effect_intent_fingerprint(self.original.intent))
        self.assertEqual(outcome.outcome.request_fingerprint, runtime_effect_intent_fingerprint(self.original.intent))
        self.assertEqual(outcome.outcome.identity, self.birth_identity)
        self.assertEqual(outcome.outcome.result.effect_id, self.original.original_start_event.event_id)
        self.assert_rows(self.birth_identity, self.birth_identity, True)
        self.latest = self.carry.advance(command)
        first = self.carry.original_acceptance
        self.assertEqual((self.latest.to_authored_graph_id, self.latest.to_realized_projection_id),
            (first.to_authored_graph_id, first.to_realized_projection_id))
        self.assertLess(first.desired_graph_revision, self.middle.desired_graph_revision)
        self.assertLess(self.middle.desired_graph_revision, self.latest.desired_graph_revision)
        self.assert_current(self.latest, self.birth_identity)
        return self.latest

    def assert_rows(self, identity, birth, is_birth):
        rows = self.base.connection.execute("SELECT artifact_id,ref_preimage,birth_run_id,birth_activity_id,"
            "birth_attempt,birth_artifact_id,is_birth FROM cpk_effect_configuration_refs "
            "WHERE run_id=%s AND activity_id=%s AND attempt=%s ORDER BY artifact_id",
            (identity.run_id.value, identity.activity_id, identity.attempt)).fetchall()
        codec = ConfigurationInstanceRefCodec()
        self.assertEqual(rows, [(ref.artifact_id, codec.encode_canonical_bytes(ref), birth.run_id.value,
            birth.activity_id, birth.attempt, ref.artifact_id, is_birth) for ref in self.new_refs])

    def assert_current(self, accepted, source):
        current = self.reader.read_current()
        self.assertEqual((current.state, current.graph_id, current.pinned_revision, current.manifest_slot_count),
            ("complete", accepted.to_authored_graph_id, accepted.desired_graph_revision, 4))
        self.assertEqual(tuple(binding.ref for binding in current.bindings), self.new_refs + self.worker_refs)
        for binding in current.bindings:
            self.assertEqual(binding.source.identity,
                source if binding.ref.node_id == "api" else self.fixture.originals["worker"].identity)
            self.assertEqual(binding.birth.identity,
                self.birth_identity if binding.ref.node_id == "api" else self.fixture.originals["worker"].identity)
        old, new = self.reader.read_use(self.old_refs), self.reader.read_use(self.new_refs)
        for observed in (old, new):
            self.assertEqual((observed.pinned_revision, observed.manifest_slot_count),
                (accepted.desired_graph_revision, 4))
        self.assertEqual((old.state, old.bindings), ("complete", ()))
        self.assertEqual((new.state, tuple(binding.ref for binding in new.bindings)), ("complete", self.new_refs))
        self.assertTrue(all(binding.source.identity == source and binding.birth.identity == self.birth_identity
            for binding in new.bindings))
        self.assertEqual(self.fixture.protective_claims(), self.claims)

    def test_return_to_same_projection_births_ref2_then_reuses_only_current_ref2(self):
        self.reenter()
        # Exact original ref1 replay survives departure/reentry without renewal.
        old = self.fixture.originals["api"]
        with self.base.unit_of_work() as uow:
            retained = uow.stores.effect_attempts.get(old.identity)
        before = self.retained_state()
        command = StartEffectAttempt("request-config", EffectAttemptTransition(EffectAttemptTransitionKind.STARTED,
            old.identity, request_fingerprint=old.request_fingerprint), old.intent,
            self.base.engine.authority(), self.fixture.fence)
        replay = EffectAttemptStartService(self.base.unit_of_work,
            id_factory=lambda: self.fail("old original replay allocated an identity")).execute(command)
        self.assertEqual(replay, ExistingAttempt(retained))
        self.assertEqual(self.retained_state(), before)
        product = _configuration_product().descriptor_document.product
        original = ProductInstanceConfiguration.from_contract(product.runtime_contract)
        changed = replace(original, public_environment=(PublicStaticEnvironmentBinding("HELLO_MESSAGE", "After reentry"),))
        graph = compile_topology(DeploymentTopology("configured", DockerRuntime(runtime_id="runtime-a", children=(
            instantiate_product(product, "api", changed), instantiate_product(product, "worker", original)))))
        self.assertNotEqual(graph.nodes["api"], self.carry.graph.nodes["api"])
        self.assertEqual(graph.nodes["api"].configuration_artifacts, self.carry.graph.nodes["api"].configuration_artifacts)
        identity = EffectAttemptIdentity(RunId("run-reuse-ref2"), "activity-reuse-ref2", 1)
        self.claims = sorted(self.claims + self.claim_rows(identity, self.new_refs))
        accepted = self.carry.advance(self.carry.prepare("reuse-ref2", "graph-after-reentry",
            ReconcileNode(NodeTarget("api")), graph=graph, expected_claims=self.claims))
        self.assert_rows(identity, self.birth_identity, False)
        self.assert_current(accepted, identity)
        before = self.retained_state()
        install_schema(self.base.connection)
        self.assertEqual(self.retained_state(), before)

    def refuse_missing_latest(self, missing):
        latest = self.reenter()
        connection = self.base.connection
        first = self.carry.original_acceptance
        older = connection.execute("SELECT * FROM cpk_configuration_acceptances "
            "WHERE workspace_id=%s AND pinned_revision=%s", ("workspace-a", first.desired_graph_revision)).fetchone()
        self.assertIsNotNone(older)
        counterparts = {
            "action": ("cpk_operation_actions", "action_id", latest.action.action_id),
            "event": ("cpk_activity_events", "event_id", latest.event.event_id),
        }
        originals = {kind: connection.execute(f"SELECT * FROM {table} WHERE {key}=%s", (identity,)).fetchone()
            for kind, (table, key, identity) in counterparts.items()}
        self.assertTrue(all(row is not None for row in originals.values()))
        protected = (connection.execute("SELECT * FROM cpk_workspaces ORDER BY workspace_id").fetchall(),
            self.retained_state()[1:])
        self.assertEqual(connection.execute("SELECT count(*) FROM cpk_configuration_accepted_slots "
            "WHERE workspace_id=%s AND pinned_revision=%s", ("workspace-a", latest.desired_graph_revision)).fetchone(), (4,))
        with connection.transaction():
            connection.execute("DELETE FROM cpk_configuration_accepted_slots WHERE workspace_id=%s AND pinned_revision=%s",
                ("workspace-a", latest.desired_graph_revision))
            connection.execute("DELETE FROM cpk_configuration_acceptances WHERE workspace_id=%s AND pinned_revision=%s",
                ("workspace-a", latest.desired_graph_revision))
            if missing == "action":
                connection.execute("DELETE FROM cpk_operation_actions WHERE action_id=%s", (latest.action.action_id,))
            if missing == "event":
                connection.execute("DELETE FROM cpk_activity_events WHERE event_id=%s", (latest.event.event_id,))
        for table in ("cpk_configuration_accepted_slots", "cpk_configuration_acceptances"):
            self.assertEqual(connection.execute(f"SELECT count(*) FROM {table} "
                "WHERE workspace_id=%s AND pinned_revision=%s", ("workspace-a", latest.desired_graph_revision)).fetchone(), (0,))
        for kind, (table, key, identity) in counterparts.items():
            self.assertEqual(connection.execute(f"SELECT * FROM {table} WHERE {key}=%s", (identity,)).fetchone(),
                None if kind == missing else originals[kind])
        self.assertEqual((connection.execute("SELECT * FROM cpk_workspaces ORDER BY workspace_id").fetchall(),
            self.retained_state()[1:]), protected)
        before = self.retained_state()
        self.assertEqual(connection.execute("SELECT * FROM cpk_configuration_acceptances "
            "WHERE workspace_id=%s AND pinned_revision=%s", ("workspace-a", first.desired_graph_revision)).fetchone(), older)
        for evidence in (self.reader.read_current(), self.reader.read_current(node_id="worker"),
                self.reader.read_use(self.old_refs), self.reader.read_use(self.new_refs)):
            self.reader.assert_unavailable(evidence)
        with self.assertRaisesRegex(SchemaInstallationError, "^operations schema reset is required$"):
            install_schema(connection)
        self.assertEqual(self.retained_state(), before)
        self.assertEqual(self.fixture.protective_claims(), self.claims)

    def test_missing_latest_header_never_resurrects_old_same_projection_ref1(self):
        self.refuse_missing_latest("header")

    def test_missing_latest_action_never_resurrects_old_same_projection_ref1(self):
        self.refuse_missing_latest("action")

    def test_missing_latest_event_never_resurrects_old_same_projection_ref1(self):
        self.refuse_missing_latest("event")
