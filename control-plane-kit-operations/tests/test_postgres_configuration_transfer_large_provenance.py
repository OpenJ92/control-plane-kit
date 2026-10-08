"""B1 defensive transfer: large historical proof, small current material.

Runtime acknowledgements are simulated and transfers are recorded reader-defense
premises, not a writer.
"""
from dataclasses import replace
import json
import unittest
from unittest import mock

import psycopg

from control_plane_kit_core.algebra import DeploymentTopology, DockerRuntime
from control_plane_kit_core.configuration_instances import ConfigurationInstanceRefCodec
from control_plane_kit_core.operations import EffectAttemptIdentity, RunId
from control_plane_kit_core.planning import NodeTarget, RuntimeTarget, StartNode, StartRuntime
from control_plane_kit_core.products import ProductInstanceConfiguration, instantiate_product
from control_plane_kit_core.runtime_effects import RuntimeEffectResult
from control_plane_kit_core.topology import DEFAULT_GRAPH_CODEC, compile_topology
from control_plane_kit_operations._configuration_preparation import _ACCOUNTING
from control_plane_kit_operations.advancement import CurrentGraphAdvancementCommandService
from control_plane_kit_operations.configuration_preparation import (
    ConfigurationCapacityDecision, configuration_evidence_capacity,
)
from control_plane_kit_operations.coordinator import CoordinatorStatus
from control_plane_kit_operations.postgres import PostgresUnitOfWork
from control_plane_kit_operations.postgres.configuration_acceptance_store import ConfigurationAcceptanceStore
from control_plane_kit_operations.records import GraphVersionRecord
from tests.configuration_transfer_fixture import ConfigurationTransferFixture, profiled_configuration_result
from tests import test_execution_coordinator as coordinator_fixture
from tests import test_postgres_configuration_acceptance as acceptance_fixture
from tests import test_postgres_configuration_carry as carry_fixture
from tests.test_postgres_configuration_transfer_capacity import Footprint, difference
from tests.test_postgres_configuration_transfer_publication import PublicationWire, observation, reconciles, within
from tests.test_runtime_effect_translation import _configuration_product


class PostgresConfigurationTransferLargeProvenanceTests(ConfigurationTransferFixture, unittest.TestCase):
    def setUp(self):
        # Each comparison case owns a fresh ordinary acceptance fixture below.
        pass

    def execute_admitted(self, command, *, configuration):
        engine = self.base.engine
        def completed(_context, request):
            return (profiled_configuration_result(request) if configuration else
                RuntimeEffectResult.succeeded(request.effect_id, evidence={"adapter": "carry-test"}))
        adapter = coordinator_fixture.RecordingAdapter(engine.tracker, completed)
        result = engine.coordinator(adapter).execute(replace(engine.command(
            generation=command.fence.generation, idempotency_key="execute-" + command.run_id),
            run_id=command.run_id))
        self.assertIs(result.status, CoordinatorStatus.COMPLETED)
        self.assertEqual(adapter.calls, [command.run_id.replace("run-", "activity-", 1)])
        self.assertEqual(adapter.active_during_calls, [0])

    def measure_final(self, command, originals, refs):
        observed, state = observation(), {}
        preflight, commit = ConfigurationAcceptanceStore._preflight, PostgresUnitOfWork.commit
        def admitted(store, prepared):
            state["prior"] = prepared.evidence_read.used
            state["snapshot"], state["future"], state["publication"] = store._publication_budgets(prepared)
            result = preflight(store, prepared)
            self.assertEqual(prepared.evidence_read.used, state["prior"])
            observed["accounting"] = _ACCOUNTING.get()
            return result
        def committed(uow):
            if observed["accounting"] is not None and _ACCOUNTING.get() is observed["accounting"]:
                state["end"] = observed["accounting"].used
            return commit(uow)
        factory = lambda: PostgresUnitOfWork(lambda: PublicationWire(
            psycopg.connect(self.base.database_url), observed))
        with mock.patch.object(ConfigurationAcceptanceStore, "_preflight", admitted), \
                mock.patch.object(PostgresUnitOfWork, "commit", committed):
            accepted = CurrentGraphAdvancementCommandService(factory,
                clock=lambda: "2026-07-22T13:05:00Z",
                id_factory=iter(("event-final", "action-final")).__next__).execute(command)
        self.assertFalse(accepted.replayed)
        used = difference(state["end"], state["prior"])
        within(self, used, state["publication"].settled)
        reconciles(self, used, observed)
        for entry in observed["queries"]:
            within(self, difference(Footprint(*entry["peak"]), state["prior"]), state["publication"].peak)
        for value in (state["future"].settled, state["future"].peak,
                state["prior"].plus(state["publication"].settled),
                state["prior"].plus(state["publication"].peak), state["end"]):
            self.assertIs(configuration_evidence_capacity(value), ConfigurationCapacityDecision.WITHIN_LIMITS)

        cold, snapshots, graph_widths = observation(), [], {}
        manifest = ConfigurationAcceptanceStore._receipt_manifest
        def measured_manifest(store, workspace, revision, read):
            start = read.used
            result = manifest(store, workspace, revision, read)
            snapshots.append(difference(read.used, start))
            return result
        def graph_row(entry, row):
            # Actual value rows, excluding integer length probes. Retain widths,
            # not the padding, and never add these observations to the ledger.
            if "FROM cpk_graph_versions " in entry["sql"] and len(row) == 8 and type(row[0]) is str:
                graph_widths.setdefault(row[0], set()).add(entry["widths"][-1][6])
                self.assertLessEqual(entry["widths"][-1][3], 32768)
        cold["after_row"] = graph_row
        with mock.patch.object(ConfigurationAcceptanceStore, "_receipt_manifest", measured_manifest):
            with PostgresUnitOfWork(lambda: PublicationWire(
                    psycopg.connect(self.base.database_url), cold, cold=True)) as uow:
                result = uow.stores.configuration_acceptance.read_current_configuration("workspace-a")
        self.assertEqual((result.state, result.graph_id, result.pinned_revision, result.manifest_slot_count),
            ("complete", "graph-final", accepted.desired_graph_revision, 16))
        self.assertEqual(tuple(binding.ref for binding in result.bindings), refs)
        for binding in result.bindings:
            self.assertEqual(binding.source.identity, originals[binding.ref.node_id].identity)
            self.assertEqual(binding.birth.identity, binding.source.identity)
        self.assertEqual(len(snapshots), 1)
        within(self, snapshots[0], state["snapshot"])
        self.assertLess(snapshots[0].accounted_bytes, 3 * 1024 * 1024)
        within(self, cold["accounting"].used, state["future"].settled)
        reconciles(self, cold["accounting"].used, cold)
        for entry in cold["queries"]:
            peak = Footprint(*entry["peak"])
            within(self, peak, state["future"].peak)
            self.assertIs(configuration_evidence_capacity(peak), ConfigurationCapacityDecision.WITHIN_LIMITS)
        state.update(material=snapshots[0], native=cold["accounting"].used,
            provenance=difference(cold["accounting"].used, snapshots[0]), graph_widths=graph_widths)
        return state

    def construct(self, padding):
        self.base = acceptance_fixture.PostgresConfigurationAcceptanceTests()
        try:
            self.base.setUp()
            self.connection = self.base.connection
            self.base.advance()
            carry = carry_fixture.PostgresConfigurationCarryTests()
            carry.base = self.base
            registered = _configuration_product()
            product = registered.descriptor_document.product
            with self.base.unit_of_work() as uow:
                uow.stores.registered_products.register(workspace_id="workspace-a",
                    descriptor_document=registered.descriptor_document, source=registered.source,
                    imported_by=registered.imported_by, imported_at=registered.imported_at)
                uow.commit()
            originals, completions, acceptances, blocks = {}, {}, {}, []
            for index in range(1, 9):
                node = "n" + str(index)
                blocks.append(instantiate_product(product, node,
                    ProductInstanceConfiguration.from_contract(product.runtime_contract)))
                graph = compile_topology(DeploymentTopology("configured", DockerRuntime(
                    runtime_id="runtime-a", children=tuple(blocks))))
                # Historical metadata is present at original graph creation.
                # No accepted graph, action, event, or completion is rewritten.
                with self.base.unit_of_work() as uow:
                    uow.stores.graphs.save(GraphVersionRecord.from_graph(graph_id="graph-" + node,
                        workspace_id="workspace-a", version=index + 2, graph=graph,
                        created_by="operator-a", created_at="2026-07-22T12:00:30Z",
                        metadata={"padding": "a" * padding}))
                    uow.commit()
                command = carry.admit(node, "graph-" + node, StartNode(NodeTarget(node)))
                self.execute_admitted(command, configuration=True)
                with self.base.unit_of_work() as uow:
                    identity = EffectAttemptIdentity(RunId("run-" + node), "activity-" + node, 1)
                    originals[node] = uow.stores.effect_attempt_intents.get(identity)
                    completions[node] = uow.stores.configuration_completions.get(identity)
                    self.assertIsNotNone(completions[node])
                    self.assertEqual(len(originals[node].intent.configuration_instances.instances), 2)
                acceptances[node] = carry.advance(command)

            # B1 has no transfer writer. Reuse exactly the permitted recorded
            # defensive premise, only after all eight ordinary acceptances.
            for node, original in originals.items():
                self.original, self.completion = original, completions[node]
                self.refs = original.intent.configuration_instances.instances
                self.revision = acceptances[node].desired_graph_revision
                self.record_transfer()
            refs = tuple(ref for original in originals.values()
                for ref in original.intent.configuration_instances.instances)
            self.assertEqual(len(refs), 16)
            before = self.transfer_snapshot()
            extra = compile_topology(DeploymentTopology("extra", DockerRuntime(runtime_id="runtime-b")))
            final_graph = graph.add_runtime(extra.runtimes["runtime-b"])
            with self.base.unit_of_work() as uow:
                uow.stores.graphs.save(GraphVersionRecord.from_graph(graph_id="graph-final",
                    workspace_id="workspace-a", version=11, graph=final_graph,
                    created_by="operator-a", created_at="2026-07-22T12:00:30Z"))
                uow.commit()
            command = carry.admit("final", "graph-final", StartRuntime(RuntimeTarget("runtime-b")))
            self.execute_admitted(command, configuration=False)
            measured = self.measure_final(command, originals, refs)
            self.assertEqual(self.transfer_snapshot(), before)
            # Independent diagnostic SQL is outside the command/read accounting.
            widths = self.connection.execute("SELECT graph_id,octet_length(metadata::text) "
                "FROM cpk_graph_versions WHERE graph_id LIKE 'graph-n%' ORDER BY graph_id").fetchall()
            self.assertEqual(widths, [("graph-n" + str(i), padding + 15) for i in range(1, 9)])
            for graph_id, width in widths:
                self.assertEqual(measured["graph_widths"][graph_id], {width})
            self.assertEqual(self.connection.execute("SELECT octet_length(metadata::text) "
                "FROM cpk_graph_versions WHERE graph_id='graph-final'").fetchone(), (2,))
            measured.update(ref_bytes=tuple(ConfigurationInstanceRefCodec().encode_canonical_bytes(ref) for ref in refs),
                graph=DEFAULT_GRAPH_CODEC.encode(final_graph), metadata_octets=sum(width for _, width in widths))
            return measured
        finally:
            self.assertTrue(self.base.doCleanups(), "nested large-provenance fixture cleanup failed")

    def test_large_historical_provenance_keeps_current_material_small(self):
        small = self.construct(0)
        large = self.construct(409585)
        self.assertEqual(large["ref_bytes"], small["ref_bytes"])
        self.assertEqual(large["graph"], small["graph"])
        self.assertEqual(large["material"], small["material"])
        self.assertEqual(large["snapshot"], small["snapshot"])
        self.assertGreater(large["metadata_octets"], 3 * 1024 * 1024)
        self.assertGreater(large["provenance"].value_octets, 3 * 1024 * 1024)
        for key in ("prior", "native", "provenance"):
            self.assertGreater(large[key].value_octets, small[key].value_octets, key)
        for phase in ("settled", "peak"):
            self.assertGreater(getattr(large["future"], phase).value_octets,
                getattr(small["future"], phase).value_octets)
        # Bounded, reproducible evidence in the owning-suite log; no ref payloads.
        def footprint(value):
            return {field: getattr(value, field) for field in
                ("records", "value_octets", "scalar_markers", "statements", "accounted_bytes")}
        print("large-provenance " + json.dumps({label: {
            **{key: footprint(case[key]) for key in ("prior", "material", "native", "provenance")},
            "native_settled": footprint(case["future"].settled),
            "native_peak": footprint(case["future"].peak),
            "publication_settled": footprint(case["publication"].settled),
            "publication_peak": footprint(case["publication"].peak),
            "metadata_octets": case["metadata_octets"],
        } for label, case in (("baseline", small), ("large", large))}, sort_keys=True))
