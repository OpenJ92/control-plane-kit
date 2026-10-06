"""Accepted-ref reuse at Operations owners; not planner or provider evidence."""
from dataclasses import replace
import re
import unittest

import psycopg

from control_plane_kit_core.algebra import DeploymentTopology, DockerRuntime
from control_plane_kit_core.configuration_instances import ConfigurationInstanceRefCodec
from control_plane_kit_core.environment import PublicStaticEnvironmentBinding
from control_plane_kit_core.operations import EffectAttemptIdentity, EffectAttemptTransition, EffectAttemptTransitionKind, RunId
from control_plane_kit_core.planning import NodeTarget, ReconcileNode
from control_plane_kit_core.products import ProductInstanceConfiguration, instantiate_product
from control_plane_kit_core.runtime_effect_observation import runtime_effect_intent_fingerprint
from control_plane_kit_core.runtime_effects import RuntimeEffectKind
from control_plane_kit_core.topology import compile_topology
from control_plane_kit_operations.coordinator import ExecutionCoordinatorConflict
from control_plane_kit_operations.effect_attempt_start import ExistingAttempt, StartEffectAttempt
from control_plane_kit_operations.effect_attempt_start_interpreter import EffectAttemptStartService
from control_plane_kit_operations.postgres import PostgresUnitOfWork, install_schema
from tests import test_postgres_configuration_carry as carry_fixture
from tests.test_runtime_effect_translation import _configuration_product


class PostgresConfigurationReuseTests(unittest.TestCase):
    def setUp(self):
        self.carry = carry_fixture.PostgresConfigurationCarryTests()
        self.carry.configuration_result_for_request = getattr(self, "configuration_result_for_request", None)
        self.addCleanup(self.cleanup_fixture)
        self.carry.setUp()
        self.base, self.fixture, self.reader = self.carry.base, self.carry.fixture, self.carry.reader
        self.api_refs = self.fixture.originals["api"].intent.configuration_instances.instances
        self.identity = EffectAttemptIdentity(RunId("run-reuse"), "activity-reuse", 1)
        self.expected_claims = sorted(self.fixture.claims + [
            ("run-reuse", "activity-reuse", 1, ref.artifact_id, ref.workspace_id, ref.allocation_id, ref.runtime_id, ref.node_id, None, None, None, True)
            for ref in self.api_refs])

    def cleanup_fixture(self):
        self.assertTrue(self.carry.doCleanups(), "nested reuse fixture cleanup failed")

    def reuse_graph(self):
        product = _configuration_product().descriptor_document.product
        original = ProductInstanceConfiguration.from_contract(product.runtime_contract)
        changed = replace(original, public_environment=(PublicStaticEnvironmentBinding("HELLO_MESSAGE", "New deployment message"),))
        graph = compile_topology(DeploymentTopology("configured", DockerRuntime(runtime_id="runtime-a", children=(
            instantiate_product(product, "api", changed), instantiate_product(product, "worker", original)))))
        self.assertNotEqual(graph.nodes["api"], self.carry.graph.nodes["api"])
        self.assertEqual(graph.nodes["api"].configuration_artifacts, self.carry.graph.nodes["api"].configuration_artifacts)
        self.assertEqual(graph.nodes["worker"], self.carry.graph.nodes["worker"])
        return graph

    def execute_reuse(self):
        graph = self.reuse_graph()
        # Explicitly approved, nonempty Operations plan for a real environment
        # change. No planner-generated/end-to-end or native-effect claim.
        try:
            command = self.carry.prepare("reuse", "graph-reuse", ReconcileNode(NodeTarget("api")),
                graph=graph, expected_claims=self.expected_claims)
        except ExecutionCoordinatorConflict as error:
            self.fail("authorized reconciliation must reuse exact accepted configuration: " + str(error))
        with self.base.unit_of_work() as uow:
            original = uow.stores.effect_attempt_intents.get(self.identity)
            attempt = uow.stores.effect_attempts.get(self.identity)
            outcome = uow.stores.effect_outcomes.get(self.identity, attempt.latest_transition_event.event_id)
        self.assertIs(original.intent.kind, RuntimeEffectKind.CONFIGURATION_ACTIVITY_V1)
        self.assertIs(type(original.intent.operation), ReconcileNode)
        self.assertEqual(original.intent.configuration_instances.instances, self.api_refs)
        fingerprint = runtime_effect_intent_fingerprint(original.intent)
        self.assertEqual(attempt.state.request_fingerprint, fingerprint)
        self.assertEqual(outcome.outcome.request_fingerprint, fingerprint)
        self.assertEqual(outcome.outcome.identity, self.identity)
        self.assertEqual(outcome.outcome.result.effect_id, original.original_start_event.event_id)
        self.assertEqual(self.fixture.protective_claims(), self.expected_claims)
        codec = ConfigurationInstanceRefCodec()
        rows = self.base.connection.execute("SELECT artifact_id,ref_preimage,birth_run_id,birth_activity_id,birth_attempt,"
            "birth_artifact_id,is_birth FROM cpk_effect_configuration_refs WHERE run_id='run-reuse' "
            "AND activity_id='activity-reuse' AND attempt=1 ORDER BY artifact_id").fetchall()
        self.assertEqual(rows, [(ref.artifact_id, codec.encode_canonical_bytes(ref), "run-config", "start-api", 1,
            ref.artifact_id, False) for ref in self.api_refs])
        return command, original, attempt

    def assert_accepted_reuse(self, result, original):
        expected = tuple(((*row[:3], "run-reuse", "activity-reuse", 1, row[2], *row[7:])
            if row[1] == "api" else row) for row in self.fixture.expected_slots)
        rows = self.base.connection.execute("SELECT runtime_id,node_id,artifact_id,source_run_id,source_activity_id,source_attempt,"
            "source_artifact_id,birth_run_id,birth_activity_id,birth_attempt,birth_artifact_id,full_ref_digest "
            "FROM cpk_configuration_accepted_slots WHERE workspace_id='workspace-a' AND pinned_revision=%s "
            "ORDER BY runtime_id,node_id,artifact_id", (result.desired_graph_revision,)).fetchall()
        self.assertEqual(tuple(rows), expected)
        value = self.reader.read_current()
        self.assertEqual((value.state, value.pinned_revision, value.manifest_slot_count),
            ("complete", result.desired_graph_revision, 4))
        self.assertEqual(tuple(binding.ref for binding in value.bindings), self.fixture.refs)
        for binding in value.bindings:
            old = self.fixture.originals[binding.ref.node_id]
            self.assertEqual(binding.birth.identity, old.identity)
            self.assertEqual(binding.source.identity, self.identity if binding.ref.node_id == "api" else old.identity)
            if binding.ref.node_id == "api":
                self.assertEqual(binding.source.source.original_event_id, original.original_start_event.event_id)
        inverse = self.reader.read_use(self.api_refs)
        self.assertEqual((inverse.state, inverse.manifest_slot_count), ("complete", 4))
        self.assertEqual(tuple(binding.ref for binding in inverse.bindings), self.api_refs)
        self.assertTrue(all(binding.source.identity == self.identity for binding in inverse.bindings))
        self.assertEqual(self.fixture.protective_claims(), self.expected_claims)

    def retained_state(self):
        return (self.base.retained_snapshot(), self.fixture.protective_claims(),
            self.base.connection.execute("SELECT * FROM cpk_effect_attempt_intents ORDER BY run_id,activity_id,attempt").fetchall(),
            self.base.connection.execute("SELECT * FROM cpk_effect_configuration_refs ORDER BY run_id,activity_id,attempt,artifact_id").fetchall())

    def test_new_use_retains_exact_allocation_birth_and_adds_its_own_claim(self):
        command, original, _ = self.execute_reuse()
        result = self.carry.advance(command)
        self.assert_accepted_reuse(result, original)
        before = self.retained_state()
        install_schema(self.base.connection)
        self.assertEqual(self.retained_state(), before)

    def test_exact_reuse_start_replay_ignores_later_desired_selection(self):
        command, original, attempt = self.execute_reuse()
        result = self.carry.advance(command)
        self.assert_accepted_reuse(result, original)
        with self.base.unit_of_work() as uow:
            changed = uow.stores.workspaces.set_desired_graph("workspace-a", "graph-current")
            uow.commit()
        self.assertGreater(changed.desired_graph_revision, command.expected_desired_graph_revision)
        before, statements = self.retained_state(), []

        class ObservedConnection:
            def __init__(self, connection):
                self.connection = connection

            def __getattr__(self, name):
                return getattr(self.connection, name)

            def execute(self, query, parameters=None):
                statements.append((str(query), tuple(parameters or ())))
                return self.connection.execute(query, parameters)

        start = StartEffectAttempt("request-reuse", EffectAttemptTransition(EffectAttemptTransitionKind.STARTED,
            self.identity, request_fingerprint=original.request_fingerprint), original.intent, command.authority, command.fence)
        service = EffectAttemptStartService(lambda: PostgresUnitOfWork(lambda:
            ObservedConnection(psycopg.connect(self.base.database_url))), id_factory=lambda: self.fail("replay allocated an ID"))
        replay = service.execute(start)
        self.assertEqual(replay, ExistingAttempt(attempt))
        allowed_locks = {
            ("SELECT 1 FROM cpk_execution_requests WHERE request_id=%s FOR UPDATE", ("request-reuse",)),
            ("SELECT 1 FROM cpk_activity_runs WHERE request_id=%s AND run_id=%s LIMIT 1 FOR UPDATE",
                ("request-reuse", "run-reuse")),
            ("SELECT 1 FROM cpk_effect_attempts WHERE run_id=%s AND activity_id=%s AND attempt=%s FOR UPDATE",
                ("run-reuse", "activity-reuse", 1)),
        }
        observed_locks = set()
        for query, parameters in statements:
            normalized = " ".join(query.split())
            if re.search(r"\bFOR\s+(UPDATE|SHARE|KEY|NO)\b", normalized, re.IGNORECASE):
                self.assertIn((normalized, parameters), allowed_locks)
                observed_locks.add((normalized, parameters))
                normalized = normalized.removesuffix(" FOR UPDATE")
            self.assertFalse(re.search(r"\b(INSERT|UPDATE|DELETE|MERGE|TRUNCATE|ALTER|CREATE|DROP|LOCK)\b",
                normalized, re.IGNORECASE), "replay dispatched mutation or unexpected locking SQL")
            self.assertNotIn("pg_advisory", normalized.lower(), "replay acquired a fresh lifecycle lock")
        self.assertEqual(observed_locks, allowed_locks)
        self.assertFalse(any("cpk_registered_products" in query for query, _ in statements))
        self.assertFalse(any(re.search(r"CURRENT_TIMESTAMP|clock_timestamp|statement_timestamp|transaction_timestamp|\bnow\s*\(",
            query, re.IGNORECASE) for query, _ in statements))
        self.assertEqual(self.retained_state(), before)
        self.assert_accepted_reuse(result, original)
