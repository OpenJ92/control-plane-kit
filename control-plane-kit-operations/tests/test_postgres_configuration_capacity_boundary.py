"""Measure a lawful compact producer boundary; never manufacture accepted history."""
from contextlib import contextmanager
from dataclasses import asdict, replace
import json
import unittest
from unittest import mock

from control_plane_kit_core.algebra import DeploymentTopology, DockerRuntime
from control_plane_kit_core.environment import PublicStaticEnvironmentBinding
from control_plane_kit_core.operations import EffectAttemptIdentity, EffectAttemptTransition, EffectAttemptTransitionKind, RunId
from control_plane_kit_core.planning import NodeTarget, ReconcileNode, RemoveNodeResource, RuntimeTarget, StartNode, StartRuntime
from control_plane_kit_core.products import ProductDescriptorCodec, ProductInstanceConfiguration, instantiate_product
from control_plane_kit_core.runtime_effects import RuntimeEffectResult
from control_plane_kit_core.topology import compile_topology
from control_plane_kit_operations import configuration_preparation as capacity_values
from control_plane_kit_operations.advancement import CurrentGraphAdvancementCommandService, CurrentGraphAdvancementConflict
from control_plane_kit_operations.coordinator import CoordinatorStatus, ExecutionCoordinatorConflict
from control_plane_kit_operations.effect_attempt_start import ExistingAttempt, StartEffectAttempt
from control_plane_kit_operations.effect_attempt_start_interpreter import EffectAttemptStartService
from control_plane_kit_operations.postgres import configuration_evidence as evidence_reads
from control_plane_kit_operations.postgres import configuration_acceptance_store as acceptance_store
from control_plane_kit_operations.postgres.graph_store import PostgresWorkspaceStore
from control_plane_kit_operations.products import RegisteredProduct
from tests import postgres_effect_attempt_coordinator_fixture as coordinator_fixture
from tests import test_postgres_configuration_carry as carry_fixture
from tests.test_runtime_effect_translation import _configuration_product
from tests.test_receiver_execution_scope_queries import plan_paths


@contextmanager
def observe_queries(queries):
    original = evidence_reads._EvidenceRead.query

    def query(reader, statement, params=(), **kwargs):
        rows = original(reader, statement, params, **kwargs)
        queries.append((statement, params, len(rows)))
        return rows

    with mock.patch.object(evidence_reads._EvidenceRead, "query", query):
        yield


@contextmanager
def observe_capacity(rejections):
    """Pass through actual owner decisions and exceptions, without changing limits."""
    original_evidence = evidence_reads.configuration_evidence_capacity
    original_preparation = capacity_values.configuration_preparation_capacity
    original_rows = evidence_reads._EvidenceRead.bounded_rows

    def evidence(footprint):
        decision = original_evidence(footprint)
        if decision is not capacity_values.ConfigurationCapacityDecision.WITHIN_LIMITS:
            rejections.append(dict(owner="evidence-capacity", decision=decision.value,
                footprint=asdict(footprint), accounted_bytes=footprint.accounted_bytes))
        return decision

    def preparation(**kwargs):
        decision = original_preparation(**kwargs)
        if decision is not capacity_values.ConfigurationCapacityDecision.WITHIN_LIMITS:
            rejections.append(dict(owner="preparation", decision=decision.value,
                current=asdict(kwargs["current"]), reserved_future=asdict(kwargs["reserved_future"]),
                existing_claims=len(kwargs["existing_claim_keys"]), existing_total=kwargs["existing_total_claims"]))
        return decision

    def rows(reader, *args, **kwargs):
        try:
            return original_rows(reader, *args, **kwargs)
        except evidence_reads._Capacity as error:
            trace = error.__traceback__
            while trace.tb_next is not None:
                trace = trace.tb_next
            rejections.append(dict(owner="bounded-read", source_line=trace.tb_lineno,
                table=args[0] if args else kwargs.get("table"),
                used=asdict(reader.used), accounted_bytes=reader.used.accounted_bytes))
            raise

    with mock.patch.object(evidence_reads, "configuration_evidence_capacity", evidence), \
            mock.patch.object(capacity_values, "configuration_evidence_capacity", evidence), \
            mock.patch.object(capacity_values, "configuration_preparation_capacity", preparation), \
            mock.patch.object(evidence_reads._EvidenceRead, "bounded_rows", rows):
        yield


class PostgresConfigurationCapacityBoundaryTests(unittest.TestCase):
    def setUp(self):
        registered = _configuration_product()
        original = registered.descriptor_document.product
        self.product = replace(original, runtime_contract=replace(original.runtime_contract,
            configuration_artifacts=original.runtime_contract.configuration_artifacts[:1]))
        self.carry = carry_fixture.PostgresConfigurationCarryTests()
        self.carry.node_ids = ("api",)
        self.carry.registered_product = RegisteredProduct.from_document(workspace_id="workspace-a",
            descriptor_document=ProductDescriptorCodec().encode_document(self.product), source=registered.source,
            imported_by=registered.imported_by, imported_at=registered.imported_at)
        self.addCleanup(self.cleanup_fixture)
        self.carry.setUp()
        self.base, self.fixture, self.reader = self.carry.base, self.carry.fixture, self.carry.reader
        self.refs = self.fixture.original.intent.configuration_instances.instances
        self.assertEqual(len(self.refs), 1)
        self.birth = self.fixture.original.identity

    def cleanup_fixture(self):
        self.assertTrue(self.carry.doCleanups(), "capacity fixture cleanup failed")

    def retained_state(self):
        return (self.base.retained_snapshot(), self.fixture.protective_claims(), tuple(
            (table, self.base.connection.execute(f"SELECT * FROM {table} ORDER BY run_id,activity_id,attempt").fetchall())
            for table in ("cpk_effect_attempts", "cpk_effect_attempt_intents", "cpk_effect_attempt_outcomes")),
            self.base.connection.execute("SELECT * FROM cpk_effect_configuration_refs "
                "ORDER BY run_id,activity_id,attempt,artifact_id").fetchall())

    def changed_graph(self, label):
        configuration = replace(ProductInstanceConfiguration.from_contract(self.product.runtime_contract),
            public_environment=(PublicStaticEnvironmentBinding("HELLO_MESSAGE", label),))
        return compile_topology(DeploymentTopology("configured", DockerRuntime(runtime_id="runtime-a", children=(
            instantiate_product(self.product, "api", configuration),))))

    def assert_cold_current(self, source):
        current = self.reader.read_current()
        self.assertEqual((current.state, current.manifest_slot_count), ("complete", 1))
        self.assertEqual(tuple(binding.ref for binding in current.bindings), self.refs)
        self.assertEqual((current.bindings[0].source.identity, current.bindings[0].birth.identity), (source, self.birth))

    def assert_sentinel(self, queries, rejections, table, prefix):
        selected = [(sql, params, count) for sql, params, count in queries
            if " FROM " + table + " WHERE " + prefix in sql]
        self.assertEqual(len(selected), 1, selected)
        sql, params, count = selected[0]
        self.assertTrue(sql.startswith("SELECT octet_length("), sql)
        self.assertEqual((params[-1], count), (257, 257))
        self.assertTrue(any(item.get("owner") == "bounded-read" and item.get("table") == table
            for item in rejections), rejections)

    def test_defensive_node_257_refuses_before_history_material_and_new_start(self):
        empty = replace(self.carry.graph, nodes={}, runtimes={"runtime-a": replace(
            self.carry.graph.runtimes["runtime-a"], children=())})
        self.carry.advance(self.carry.prepare("depart", "graph-departed",
            RemoveNodeResource(NodeTarget("api")), graph=empty))
        command = self.carry.admit("return", "graph-configured", StartNode(NodeTarget("api")))
        # Explicit invalid retained history: copied index/protection rows are
        # NOT genuine uses. Ordinary constraints and original FKs stay enabled.
        with self.base.connection.transaction():
            self.base.connection.execute("INSERT INTO cpk_effect_configuration_refs "
                "SELECT run_id,activity_id,attempt,'copied-' || n,workspace_id,allocation_id,runtime_id,node_id,"
                "ref_preimage,ref_digest,request_fingerprint,original_event_id,birth_run_id,birth_activity_id,"
                "birth_attempt,birth_artifact_id,false FROM cpk_effect_configuration_refs "
                "CROSS JOIN generate_series(1,256) n WHERE artifact_id=%s", (self.refs[0].artifact_id,))
            self.base.connection.execute("INSERT INTO cpk_configuration_claims "
                "SELECT run_id,activity_id,attempt,artifact_id,workspace_id,allocation_id "
                "FROM cpk_effect_configuration_refs WHERE artifact_id LIKE 'copied-%%'")
        self.assertEqual(self.base.connection.execute("SELECT count(*) FROM cpk_effect_configuration_refs "
            "WHERE workspace_id='workspace-a' AND runtime_id='runtime-a' AND node_id='api'").fetchone(), (257,))
        before, queries, rejections = self.retained_state(), [], []
        harness = coordinator_fixture.PostgresEffectAttemptCoordinatorFixture.coordinator_harness(self.base)
        with observe_queries(queries), observe_capacity(rejections), self.assertRaises(ExecutionCoordinatorConflict):
            harness.coordinator.execute(replace(self.base.engine.command(generation=command.fence.generation,
                idempotency_key="execute-return"), run_id=command.run_id))
        self.assert_sentinel(queries, rejections, "cpk_effect_configuration_refs",
            "workspace_id=%s AND runtime_id=%s AND node_id=%s")
        self.assertEqual(len(harness.start.commands), 1)
        self.assertEqual(harness.start_ids.calls, [])
        self.assertEqual(harness.adapter.runtime_calls, [])
        self.assertEqual(self.retained_state(), before)

    def test_defensive_manifest_257_refuses_advancement_before_ids_and_cas(self):
        extra = compile_topology(DeploymentTopology("extra", DockerRuntime(runtime_id="runtime-b")))
        command = self.carry.prepare("extra", "graph-extra", StartRuntime(RuntimeTarget("runtime-b")),
            graph=self.carry.graph.add_runtime(extra.runtimes["runtime-b"]))
        revision = self.carry.original_acceptance.desired_graph_revision
        # Only slot PK names change. Real receipt/source/birth/outcome FKs and
        # the genuine header/count/digest survive; this is invalid history.
        self.base.connection.execute("INSERT INTO cpk_configuration_accepted_slots "
            "SELECT workspace_id,pinned_revision,runtime_id,node_id,'copied-' || n,source_run_id,"
            "source_activity_id,source_attempt,source_artifact_id,birth_run_id,birth_activity_id,"
            "birth_attempt,birth_artifact_id,full_ref_digest FROM cpk_configuration_accepted_slots "
            "CROSS JOIN generate_series(1,256) n WHERE pinned_revision=%s", (revision,))
        self.assertEqual(self.base.connection.execute("SELECT count(*) FROM cpk_configuration_accepted_slots "
            "WHERE pinned_revision=%s", (revision,)).fetchone(), (257,))
        before, queries, rejections = self.retained_state(), [], []
        service = CurrentGraphAdvancementCommandService(self.base.unit_of_work,
            clock=lambda: self.fail("early manifest refusal read clock"),
            id_factory=lambda: self.fail("early manifest refusal allocated ID"))
        with observe_queries(queries), observe_capacity(rejections), self.assertRaises(CurrentGraphAdvancementConflict):
            service.execute(command)
        self.assert_sentinel(queries, rejections, "cpk_configuration_accepted_slots",
            "workspace_id=%s AND pinned_revision=%s")
        self.assertEqual(self.retained_state(), before)
        self.assertEqual(self.fixture.protective_claims(), self.fixture.claims)

    def test_actual_configuration_queries_have_indexed_lookup_prefixes(self):
        queries = []
        with observe_queries(queries):
            self.assert_cold_current(self.birth)
            self.assertEqual(self.reader.read_use(self.refs).state, "complete")
            with self.base.unit_of_work() as uow:
                self.assertEqual(uow.stores.configuration_preparation.read_allocation_evidence(self.refs[0]).state,
                    "complete")
            command = self.carry.admit("indexed", "graph-indexed", ReconcileNode(NodeTarget("api")),
                graph=self.changed_graph("indexed"))
            harness = coordinator_fixture.PostgresEffectAttemptCoordinatorFixture.coordinator_harness(self.base)
            self.assertIs(harness.coordinator.execute(replace(self.base.engine.command(
                generation=command.fence.generation, idempotency_key="execute-indexed"),
                run_id=command.run_id)).status, CoordinatorStatus.COMPLETED)
        prefixes = {
            "actions": ("cpk_operation_actions", ("advancement_workspace_id",)),
            "events": ("cpk_activity_events", ("advancement_workspace_id",)),
            "header": ("cpk_configuration_acceptances", ("workspace_id", "pinned_revision")),
            "manifest": ("cpk_configuration_accepted_slots", ("workspace_id", "pinned_revision")),
            "node": ("cpk_effect_configuration_refs", ("workspace_id", "runtime_id", "node_id")),
            "birth": ("cpk_effect_configuration_refs", ("workspace_id", "allocation_id")),
            "claims": ("cpk_configuration_claims", ("workspace_id", "allocation_id")),
        }
        # This proves eligibility of real owner SQL on PostgreSQL, not a bound
        # on heap/index work or latency and not an optimizer-choice promise.
        with self.base.connection.transaction():
            self.base.connection.execute("SET LOCAL enable_seqscan=off")
            for label, (table, columns) in prefixes.items():
                with self.subTest(prefix=label):
                    candidates = [(sql, params) for sql, params, _ in queries
                        if " FROM " + table + " WHERE " in sql and all(
                            column + "=%s" in sql.partition(" WHERE ")[2] for column in columns)]
                    self.assertTrue(candidates, label)
                    sql, params = candidates[0]
                    self.assertIn("LIMIT", sql)
                    plan = self.base.connection.execute("EXPLAIN (FORMAT JSON, COSTS FALSE) " + sql,
                        params).fetchone()[0][0]["Plan"]
                    paths = [(node, parents) for node, parents in plan_paths(plan)
                        if node.get("Index Name") and all(column in node.get("Index Cond", "") for column in columns)]
                    self.assertTrue(paths, {"prefix": label, "query": sql, "plan": plan})
                    if label in ("actions", "events"):
                        kind, value, index = (("action_type", "advance-current-graph", "cpk_action_advancement_workspace_revision")
                            if label == "actions" else ("event_type", "current_graph_advanced", "cpk_event_advancement_workspace_revision"))
                        self.assertIn(kind + "=%s ORDER BY advancement_revision DESC,", sql)
                        self.assertEqual(params, ("workspace-a", value))
                        self.assertTrue(any(node["Index Name"] == index for node, _ in paths), plan)
                    self.assertTrue(any(any(parent["Node Type"] == "Limit" for parent in parents)
                        for _, parents in paths), plan)
                    for node, _ in plan_paths(plan):
                        self.assertNotIn(node["Node Type"], ("Seq Scan", "Parallel Seq Scan"), plan)
                        self.assertNotIn("Filter", node, plan)
                        self.assertNotIn("Join Filter", node, plan)
            # Actual allocation discovery is a bounded ref-prefix query plus
            # reciprocal point-key join, not the separate known-birth lookup.
            sql, params = next((sql, params) for sql, params, _ in queries
                if " FROM cpk_effect_configuration_refs r LEFT JOIN cpk_configuration_claims c " in sql
                and " WHERE r.workspace_id=%s AND r.allocation_id=%s" in sql)
            self.assertTrue(sql.endswith("ORDER BY r.run_id,r.activity_id,r.attempt,r.artifact_id LIMIT 65"))
            plan = self.base.connection.execute("EXPLAIN (FORMAT JSON, COSTS FALSE) " + sql,
                params).fetchone()[0][0]["Plan"]
            for alias, columns in (("r", ("workspace_id", "allocation_id")),
                    ("c", ("run_id", "activity_id", "attempt", "artifact_id"))):
                paths = [(node, parents) for node, parents in plan_paths(plan) if node.get("Alias") == alias
                    and node.get("Index Name") and all(column in node.get("Index Cond", "") for column in columns)]
                self.assertTrue(paths, {"alias": alias, "plan": plan})
                self.assertTrue(any(any(parent["Node Type"] == "Limit" for parent in parents)
                    for _, parents in paths), plan)
            for node, _ in plan_paths(plan):
                self.assertNotIn(node["Node Type"], ("Seq Scan", "Parallel Seq Scan"), plan)
                self.assertNotIn("Filter", node, plan)
                self.assertNotIn("Join Filter", node, plan)

    def test_injected_post_preflight_failure_preserves_nonempty_owner_truth_before_cas(self):
        extra = compile_topology(DeploymentTopology("extra", DockerRuntime(runtime_id="runtime-b")))
        command = self.carry.prepare("preflight", "graph-preflight", StartRuntime(RuntimeTarget("runtime-b")),
            graph=self.carry.graph.add_runtime(extra.runtimes["runtime-b"]))
        original = acceptance_store.ConfigurationAcceptanceStore._preflight
        calls = []

        def inject_after_real_preflight(store, prepared):
            original(store, prepared)
            calls.append((prepared.workspace.workspace_id, len(prepared.slots)))
            # Fault injection proves owner ordering/atomicity, not natural
            # capacity exhaustion. The owner-issued prepared value is unchanged.
            raise evidence_reads._Capacity

        before = self.retained_state()
        with mock.patch.object(acceptance_store.ConfigurationAcceptanceStore, "_preflight", inject_after_real_preflight), \
                mock.patch.object(PostgresWorkspaceStore, "_compare_and_set_current_graph", side_effect=AssertionError(
                    "preflight refusal reached current CAS")), self.assertRaises(CurrentGraphAdvancementConflict):
            CurrentGraphAdvancementCommandService(self.base.unit_of_work,
                clock=lambda: "2026-07-22T13:05:00Z", id_factory=iter(("event-preflight", "action-preflight")).__next__
            ).execute(command)
        self.assertEqual(calls, [("workspace-a", 1)])
        self.assertEqual(self.retained_state(), before)
        self.assertEqual(self.fixture.protective_claims(), self.fixture.claims)

    def test_compact_accepted_uses_reach_a_measured_owner_boundary_without_partial_publication(self):
        # Explicit simulated total installation of the one selected artifact.
        def installed(_context, request):
            return RuntimeEffectResult.succeeded(request.effect_id, evidence={"adapter": "capacity-selected-artifact"})

        adapter = coordinator_fixture.RecordingRuntimeAdapter(*(installed for _ in range(64)))
        harness = coordinator_fixture.PostgresEffectAttemptCoordinatorFixture.coordinator_harness(self.base, adapter=adapter)
        claims = list(self.fixture.claims)
        accepted_count, accepted_source, refusal = 1, self.birth, None
        self.assert_cold_current(accepted_source)
        # Each preceding use is accepted before the next real approved use.
        # No raw receipts, fabricated counters, altered budgets or provider data.
        for number in range(2, 66):
            label = f"u{number}"
            command = self.carry.admit(label, "graph-" + label, ReconcileNode(NodeTarget("api")),
                graph=self.changed_graph(label))
            identity = EffectAttemptIdentity(RunId(command.run_id), "activity-" + label, 1)
            before = self.retained_state()
            start_ids, calls = len(harness.start_ids.calls), len(harness.adapter.runtime_calls)
            start_commands = len(harness.start.commands)
            rejections = []
            with observe_capacity(rejections):
                try:
                    result = harness.coordinator.execute(replace(self.base.engine.command(
                        generation=command.fence.generation, idempotency_key="execute-" + label), run_id=command.run_id))
                except ExecutionCoordinatorConflict:
                    self.assertTrue(rejections, "refusal lacked an observed production capacity cause")
                    self.assertEqual(len(harness.start_ids.calls), start_ids)
                    self.assertEqual(len(harness.adapter.runtime_calls), calls)
                    self.assertEqual(self.retained_state(), before)
                    refusal = "guarded-start" if len(harness.start.commands) > start_commands else "proposal"
                else:
                    self.assertIs(result.status, CoordinatorStatus.COMPLETED)
                    self.assertEqual(len(harness.adapter.runtime_calls), calls + 1)
                    with self.base.unit_of_work() as uow:
                        original = uow.stores.effect_attempt_intents.get(identity)
                    self.assertEqual(original.intent.configuration_instances.instances, self.refs)
                    claims += [(identity.run_id.value, identity.activity_id, 1,
                        ref.artifact_id, ref.workspace_id, ref.allocation_id) for ref in self.refs]
                    self.assertEqual(self.fixture.protective_claims(), sorted(claims))
                    after_effect = self.retained_state()
                    ids = iter(("capacity-event-" + label, "capacity-action-" + label))
                    try:
                        CurrentGraphAdvancementCommandService(self.base.unit_of_work,
                            clock=lambda: "2026-07-22T13:05:00Z", id_factory=lambda: next(ids)).execute(command)
                    except CurrentGraphAdvancementConflict:
                        self.assertTrue(rejections, "advancement refusal lacked an observed capacity cause")
                        # The successful effect's new claim remains protective;
                        # only acceptance publication must be unchanged.
                        self.assertEqual(self.retained_state(), after_effect)
                        refusal = "advancement"
                    else:
                        self.assertEqual(rejections, [])
                        accepted_count, accepted_source = number, identity
            self.assert_cold_current(accepted_source)
            if refusal is not None:
                break
        self.assertIsNotNone(refusal, "65 accepted claims bypassed the supported per-allocation boundary")
        self.assertGreaterEqual(accepted_count, 2, "compact history must support a real accepted reuse")
        self.assertLessEqual(accepted_count, 64)
        self.assertEqual(self.fixture.protective_claims(), sorted(claims))
        with self.base.unit_of_work() as uow:
            allocation = uow.stores.configuration_preparation.read_allocation_evidence(self.refs[0])
        self.assertEqual(allocation.state, "complete", "owner history exceeded the mandatory cold-reader boundary")
        self.assertEqual(len(allocation.claims), len(claims))
        self.assertEqual(allocation.birth.identity, self.birth)
        original = self.fixture.original
        with self.base.unit_of_work() as uow:
            retained = uow.stores.effect_attempts.get(original.identity)
        before = self.retained_state()
        replay = EffectAttemptStartService(self.base.unit_of_work,
            id_factory=lambda: self.fail("capacity-boundary original replay allocated an ID")).execute(
                StartEffectAttempt(original.intent.source.request_id,
                    EffectAttemptTransition(EffectAttemptTransitionKind.STARTED, original.identity,
                        request_fingerprint=original.request_fingerprint), original.intent,
                    self.base.engine.authority(), self.fixture.fence))
        self.assertEqual(replay, ExistingAttempt(retained))
        self.assertEqual(self.retained_state(), before)
        # Bounded metrics identify this composition's actual first obstruction.
        # They do not assert universal unreachability of a different composition.
        print("configuration-owner-capacity " + json.dumps(dict(accepted_uses=accepted_count,
            protected_claims=len(claims), stage=refusal, rejections=rejections), sort_keys=True))
