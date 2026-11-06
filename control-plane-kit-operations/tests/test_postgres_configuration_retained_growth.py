"""Historical outstanding-claim growth with recorded closure, not C producer evidence."""
from dataclasses import asdict, replace
import json
import unittest

from control_plane_kit_core.operations import EffectAttemptIdentity, RunId
from control_plane_kit_core.planning import NodeTarget, RemoveNodeResource, StartNode
from control_plane_kit_core.products import ProductDescriptorCodec
from control_plane_kit_operations.postgres.configuration_evidence import _joined_read
from control_plane_kit_operations.products import RegisteredProduct
from tests.configuration_cleanup_history_fixture import ConfigurationCleanupHistoryFixture
from tests.configuration_transfer_fixture import historical_transfer_prefix
from tests.test_postgres_configuration_capacity_boundary import observe_queries
from tests.test_receiver_execution_scope_queries import plan_paths
from tests.test_runtime_effect_translation import _configuration_product


class PostgresConfigurationRetainedGrowthTests(ConfigurationCleanupHistoryFixture, unittest.TestCase):
    def setUp(self):
        registered = _configuration_product()
        product = registered.descriptor_document.product
        exemplar = product.runtime_contract.configuration_artifacts[0]
        artifacts = tuple(replace(exemplar, artifact_id=f"config-{number:02}",
            target_path=f"/etc/cpk/config-{number:02}.json") for number in range(8))
        product = replace(product, runtime_contract=replace(product.runtime_contract,
            configuration_artifacts=artifacts))
        self.registered_product = RegisteredProduct.from_document(workspace_id="workspace-a",
            descriptor_document=ProductDescriptorCodec().encode_document(product), source=registered.source,
            imported_by=registered.imported_by, imported_at=registered.imported_at)
        super().setUp()
        self.assertEqual(len(self.refs), 8)
        with historical_transfer_prefix(self):
            self.member.advance()
        self.operator = self.carry_operator()

    def discover(self, ref):
        # Each sample starts a new transaction, immutable cache and charged ledger.
        queries = []
        with self.unit_of_work() as uow, _joined_read(uow.stores.connection) as read:
            with observe_queries(queries):
                rows = uow.stores.configuration_preparation._node_history(ref, read)
            return rows, read.used, queries

    def test_closed_history_beyond_256_preserves_active_discovery_and_new_incarnation(self):
        graph = self.operator.graph
        # This existing fixture exercises ordinary configuration without receiver
        # delegation. Runtime-wide recorded cleanup is not hidden from receivers.
        with self.unit_of_work() as uow:
            workspace = uow.stores.workspaces.get("workspace-a")
            self.assertEqual(uow.stores.graphs.receiver_bindings("workspace-a",
                workspace.current_graph_id, workspace.current_realized_projection_id), ())
        departed = replace(graph, nodes={}, runtimes={"runtime-a": replace(
            graph.runtimes["runtime-a"], children=())})
        retained, footprints = [], []
        for number in range(33):
            original, refs = self.original, self.refs
            self.assertEqual(len(refs), 8)
            with self.unit_of_work() as uow:
                self.assertIsNotNone(uow.stores.configuration_completions.get(original.identity))
            departure = self.operator.prepare(f"growth-depart-{number:02}",
                f"graph-growth-depart-{number:02}", RemoveNodeResource(NodeTarget("api")),
                graph=departed, expected_claims=self.member.protective_claims())
            self.operator.advance(departure)
            cleanup = self.retain_cleanup(label=f"recorded-growth-{number:02}")
            record = self.read_retained()
            self.assertEqual(record.identity, cleanup)
            self.assertEqual({member.ref for member in record.members}, set(refs))
            retained.extend((original.identity, ref) for ref in refs)
            rows, footprint, queries = self.discover(refs[0])
            self.assertEqual(rows, ())
            footprints.append(footprint)
            self.assertEqual(footprint, footprints[0])
            for table in ("cpk_effect_configuration_refs", "cpk_configuration_claims"):
                drivers = [(sql, params, count) for sql, params, count in queries
                    if " FROM " + table + " WHERE " in sql]
                self.assertTrue(drivers)
                for sql, params, count in drivers:
                    self.assertIn("protective AND (workspace_id=%s AND runtime_id=%s AND node_id=%s)", sql)
                    self.assertIn("ORDER BY artifact_id,run_id,activity_id,attempt LIMIT", sql)
                    self.assertEqual(params[:3], ("workspace-a", "runtime-a", "api"))
                    self.assertEqual(count, 0)
            label = f"growth-start-{number + 1:02}"
            command = self.operator.admit(label, "graph-configured", StartNode(NodeTarget("api")))
            self.execute_later(command, label)
            # Retain the pre-C outstanding premise for this v1 history reader.
            # Departure, recorded closure and discovery assertions stay real.
            with historical_transfer_prefix(self):
                self.operator.advance(command)
            identity = EffectAttemptIdentity(RunId(command.run_id), "activity-" + label, 1)
            with self.unit_of_work() as uow:
                self.original = uow.stores.effect_attempt_intents.get(identity)
            self.refs = self.original.intent.configuration_instances.instances
            self.assertTrue(set(self.refs).isdisjoint(ref for _, ref in retained))
        self.assertEqual(len(retained), 264)
        rows, footprint, queries = self.discover(self.refs[0])
        self.assertEqual(len(rows), 8)
        self.assertEqual({row[:3] for row in rows},
            {(self.original.identity.run_id.value, self.original.identity.activity_id, 1)})
        self.assertEqual({row[4] for row in rows}, {ref.allocation_id for ref in self.refs})
        self.assertEqual(self.connection.execute("SELECT count(*),count(*) FILTER (WHERE protective) "
            "FROM cpk_effect_configuration_refs WHERE workspace_id='workspace-a' "
            "AND runtime_id='runtime-a' AND node_id='api'").fetchone(), (272, 8))
        with self.unit_of_work() as uow:
            old_identity, old_ref = retained[0]
            old = uow.stores.configuration_preparation.read_allocation_evidence(old_ref)
            self.assertEqual(old.state, "complete")
            self.assertEqual(old.birth.identity, old_identity)
            self.assertEqual(old.birth.ref, old_ref)
        plans = {}
        for table in ("cpk_effect_configuration_refs", "cpk_configuration_claims"):
            sql, params, _ = next(row for row in queries if " FROM " + table + " WHERE " in row[0])
            plan = self.connection.execute("EXPLAIN (FORMAT JSON, COSTS FALSE) " + sql, params).fetchone()[0][0]["Plan"]
            plans[table] = sorted({node["Index Name"] for node, _ in plan_paths(plan) if "Index Name" in node})
        print("configuration-retained-growth " + json.dumps(dict(closed_claims=264, active_claims=8,
            empty_active_footprint=asdict(footprints[0]), active_footprint=asdict(footprint),
            observed_index_paths=plans), sort_keys=True))
        # Exact reserved-allocation permission refusal remains the separate real
        # coordinator law in PostgresConfigurationCleanupOwnershipTests.
