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
from control_plane_kit_operations import advancement as advancement_module
from control_plane_kit_operations import configuration_preparation as capacity_values
from control_plane_kit_operations.advancement import CurrentGraphAdvancementCommandService, CurrentGraphAdvancementConflict
from control_plane_kit_operations.configuration_preparation import (
    ConfigurationCapacityDecision, configuration_evidence_capacity,
)
from control_plane_kit_operations.coordinator import CoordinatorStatus
from control_plane_kit_operations.postgres import PostgresUnitOfWork
from control_plane_kit_operations.postgres.configuration_acceptance_store import ConfigurationAcceptanceStore
from control_plane_kit_operations.postgres.activity_history import PostgresActivityHistoryStore
from control_plane_kit_operations.postgres.execution import PostgresExecutionStore
from control_plane_kit_operations.postgres.graph_store import PostgresWorkspaceStore
from control_plane_kit_operations.records import GraphVersionRecord
from tests.configuration_transfer_fixture import ConfigurationTransferFixture, profiled_configuration_result
from tests import test_execution_coordinator as coordinator_fixture
from tests import test_postgres_configuration_acceptance as acceptance_fixture
from tests import test_postgres_configuration_carry as carry_fixture
from tests import test_postgres_configuration_transfer_publication as publication_fixture
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

    def measure_final(self, command, originals, refs, *, refuses=False):
        observed, state = observation(), {}
        decisions, checking = [], False
        preflight, commit = ConfigurationAcceptanceStore._preflight, PostgresUnitOfWork.commit
        def capacity(value):
            result = configuration_evidence_capacity(value)
            if checking:
                decisions.append((value, result))
            return result
        def admitted(store, prepared):
            nonlocal checking
            self.assertLessEqual(len(str(prepared.read_bounds.transaction_id)), 20)
            self.assertGreater(prepared.read_bounds.transaction_id, 0)
            state["prior"] = prepared.evidence_read.used
            state["snapshot"], state["future"], state["publication"] = store._publication_budgets(prepared)
            checking = True
            try:
                result = preflight(store, prepared)
            finally:
                checking = False
            self.assertEqual(prepared.evidence_read.used, state["prior"])
            observed["accounting"] = _ACCOUNTING.get()
            return result
        def committed(uow):
            self.assertFalse(refuses, "capacity refusal requested commit")
            if observed["accounting"] is not None and _ACCOUNTING.get() is observed["accounting"]:
                state["end"] = observed["accounting"].used
            return commit(uow)
        factory = lambda: PostgresUnitOfWork(lambda: PublicationWire(
            psycopg.connect(self.base.database_url), observed))
        def advance():
            return CurrentGraphAdvancementCommandService(factory,
                clock=lambda: "2026-07-22T13:05:00Z",
                id_factory=iter(("event-final", "action-final")).__next__).execute(command)
        durable_snapshot = lambda: publication_fixture.PostgresConfigurationTransferPublicationTests.fetch_failure_snapshot(self)
        before = durable_snapshot() if refuses else None
        with mock.patch.object(ConfigurationAcceptanceStore, "_preflight", admitted), \
                mock.patch.object(capacity_values, "configuration_evidence_capacity", capacity), \
                mock.patch.object(PostgresUnitOfWork, "commit", committed):
            if refuses:
                with mock.patch.object(PostgresWorkspaceStore, "_compare_and_set_current_graph", side_effect=AssertionError(
                        "capacity refusal reached current-graph mutation")), \
                        mock.patch.object(ConfigurationAcceptanceStore, "_insert", side_effect=AssertionError(
                        "capacity refusal reached receipt insertion")), \
                        mock.patch.object(PostgresExecutionStore, "_add_advancement_event", side_effect=AssertionError(
                            "capacity refusal reached advancement event insertion")), \
                        mock.patch.object(PostgresActivityHistoryStore, "_add_advancement_action", side_effect=AssertionError(
                            "capacity refusal reached advancement action insertion")), \
                        mock.patch.object(advancement_module, "_finish_receiver_advancement", side_effect=AssertionError(
                            "capacity refusal reached receiver finish")), \
                        self.assertRaises(CurrentGraphAdvancementConflict) as caught:
                    advance()
                self.assertIs(type(caught.exception), CurrentGraphAdvancementConflict)
            else:
                accepted = advance()
        self.assertEqual([value for value, _ in decisions], [state["future"].settled,
            state["future"].peak, state["prior"].plus(state["publication"].settled),
            state["prior"].plus(state["publication"].peak)])
        self.assertEqual([result for _, result in decisions],
            [ConfigurationCapacityDecision.WITHIN_LIMITS] * 3 +
            [ConfigurationCapacityDecision.BYTE_LIMIT if refuses else ConfigurationCapacityDecision.WITHIN_LIMITS])
        if refuses:
            self.assertLessEqual(state["snapshot"].accounted_bytes, 3 * 1024 * 1024)
            self.assertLessEqual(decisions[-1][0].records, 4096)
            self.assertEqual(durable_snapshot(), before)
            return state
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

    def construct(self, padding, *, final_metadata_width=None, outstanding_tail=False, refuses=False):
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
                self.record_transfer(self.refs[:-1] if outstanding_tail and node == "n8" else self.refs)
            refs = tuple(ref for original in originals.values()
                for ref in original.intent.configuration_instances.instances)
            self.assertEqual(len(refs), 16)
            before = self.transfer_snapshot()
            extra = compile_topology(DeploymentTopology("extra", DockerRuntime(runtime_id="runtime-b")))
            final_graph = graph.add_runtime(extra.runtimes["runtime-b"])
            with self.base.unit_of_work() as uow:
                uow.stores.graphs.save(GraphVersionRecord.from_graph(graph_id="graph-final",
                    workspace_id="workspace-a", version=11, graph=final_graph,
                    created_by="operator-a", created_at="2026-07-22T12:00:30Z",
                    metadata={} if final_metadata_width is None else
                        {"padding": "a" * (final_metadata_width - 15)}))
                uow.commit()
            command = carry.admit("final", "graph-final", StartRuntime(RuntimeTarget("runtime-b")))
            self.execute_admitted(command, configuration=False)
            # Diagnostic SQL outside command accounting checks the temporal
            # width premise; real database clocks and stored history stay intact.
            temporal_widths = [width for row in self.connection.execute(
                "SELECT octet_length(claimed_at::text),octet_length(lease_expires_at::text) "
                "FROM cpk_execution_requests WHERE workspace_id='workspace-a' AND claimed_at IS NOT NULL"
            ).fetchall() for width in row]
            temporal_widths += [row[0] for row in self.connection.execute(
                "SELECT octet_length(occurred_at::text) FROM cpk_activity_events").fetchall()]
            temporal_widths += [row[0] for row in self.connection.execute(
                "SELECT octet_length(created_at::text) FROM cpk_activity_runs").fetchall()]
            self.assertTrue(temporal_widths)
            self.assertTrue(all(22 <= width <= 29 for width in temporal_widths))
            final_claim_widths = self.connection.execute(
                "SELECT octet_length(claimed_at::text),octet_length(lease_expires_at::text) "
                "FROM cpk_execution_requests WHERE request_id='request-final'").fetchone()
            self.assertTrue(all(22 <= width <= 29 for width in final_claim_widths))
            final_run_width = self.connection.execute("SELECT octet_length(created_at::text) "
                "FROM cpk_activity_runs WHERE run_id='run-final'").fetchone()[0]
            self.assertTrue(22 <= final_run_width <= 29)
            measured = self.measure_final(command, originals, refs, refuses=refuses)
            measured["final_context_time_octets"] = sum(final_claim_widths) + final_run_width
            self.assertEqual(self.transfer_snapshot(), before)
            # Independent diagnostic SQL is outside the command/read accounting.
            widths = self.connection.execute("SELECT graph_id,octet_length(metadata::text) "
                "FROM cpk_graph_versions WHERE graph_id LIKE 'graph-n%' ORDER BY graph_id").fetchall()
            self.assertEqual(widths, [("graph-n" + str(i), padding + 15) for i in range(1, 9)])
            if not refuses:
                for graph_id, width in widths:
                    self.assertEqual(measured["graph_widths"][graph_id], {width})
            self.assertEqual(self.connection.execute("SELECT octet_length(metadata::text) "
                "FROM cpk_graph_versions WHERE graph_id='graph-final'").fetchone(),
                (2 if final_metadata_width is None else final_metadata_width,))
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
        # The material context reads one final request and run. Attribute exactly
        # their three measured database timestamp cells without changing either
        # raw footprint: claim time, lease expiry and run creation time.
        temporal_delta = large["final_context_time_octets"] - small["final_context_time_octets"]
        for key in ("material", "snapshot"):
            self.assertEqual(large[key].value_octets - small[key].value_octets, temporal_delta)
            for field in ("records", "scalar_markers", "statements"):
                self.assertEqual(getattr(large[key], field), getattr(small[key], field))
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
            "final_context_time_octets": case["final_context_time_octets"],
        } for label, case in (("baseline", small), ("large", large))}, sort_keys=True))

    def test_nearby_legal_metadata_widths_fit_settled_but_refuse_publication_peak(self):
        # Exact accounted B/B+1 predicate laws live in the isolated preflight
        # tests. Metadata contributes +5 prior/+1 publication bytes per input
        # byte; fresh database timestamps/txids can also change actual widths.
        limit, calibration_width = 16 * 1024 * 1024, 1000000
        prior_variation, publication_variation, margin = 874, 105, 1000
        self.assertGreater(margin, prior_variation + publication_variation)
        def build(width, *, refuses=False):
            self.assertGreaterEqual(width, 1000000)
            self.assertLessEqual(width, 1048576)
            return self.construct(409585, final_metadata_width=width,
                outstanding_tail=True, refuses=refuses)
        calibration = build(calibration_width)
        def total(case, phase):
            return case["prior"].plus(getattr(case["publication"], phase))
        gap = total(calibration, "peak").accounted_bytes - total(calibration, "settled").accounted_bytes
        self.assertGreater(gap, margin + prior_variation + publication_variation + 5)
        fit_width = calibration_width + (limit - margin - total(calibration, "peak").accounted_bytes) // 6
        refuse_width = calibration_width + (limit + margin - total(calibration, "peak").accounted_bytes + 5) // 6
        self.assertGreater(fit_width, calibration_width)
        self.assertGreater(refuse_width, fit_width)
        fits = build(fit_width)
        refuses = build(refuse_width, refuses=True)
        for case, width in ((fits, fit_width), (refuses, refuse_width)):
            delta = width - calibration_width
            self.assertEqual(case["ref_bytes"], calibration["ref_bytes"])
            self.assertEqual(case["graph"], calibration["graph"])
            self.assertLessEqual(abs(case["prior"].value_octets - calibration["prior"].value_octets - 5 * delta),
                prior_variation)
            for field in ("records", "scalar_markers", "statements"):
                self.assertEqual(getattr(case["prior"], field), getattr(calibration["prior"], field))
            for phase in ("settled", "peak"):
                actual, original = getattr(case["publication"], phase), getattr(calibration["publication"], phase)
                self.assertLessEqual(abs(actual.value_octets - original.value_octets - delta), publication_variation)
                for field in ("records", "scalar_markers", "statements"):
                    self.assertEqual(getattr(actual, field), getattr(original, field))
            self.assertEqual(total(case, "peak").accounted_bytes - total(case, "settled").accounted_bytes, gap)
        self.assertLessEqual(total(fits, "peak").accounted_bytes, limit)
        self.assertGreater(total(refuses, "peak").accounted_bytes, limit)
        self.assertLessEqual(total(refuses, "settled").accounted_bytes, limit)
        print("natural-publication-threshold " + json.dumps({label: {
            "final_metadata_width": width,
            "prior_bytes": case["prior"].accounted_bytes,
            "prior_plus_settled_bytes": total(case, "settled").accounted_bytes,
            "prior_plus_peak_bytes": total(case, "peak").accounted_bytes,
            "prior_plus_peak_records": total(case, "peak").records,
            "nonmetadata_prior_delta": case["prior"].value_octets - calibration["prior"].value_octets - 5 * (width - calibration_width),
            "nonmetadata_publication_delta": case["publication"].peak.value_octets - calibration["publication"].peak.value_octets - (width - calibration_width),
            "peak_minus_settled_bytes": gap,
            "input_selection_margin": margin,
        } for label, case, width in (("calibration", calibration, calibration_width),
            ("fits", fits, fit_width), ("refuses", refuses, refuse_width))}, sort_keys=True))
