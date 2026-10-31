"""Mixed transferred/protective carry retains exact original provenance."""
import unittest
from dataclasses import replace
from unittest import mock

from control_plane_kit_operations.postgres.configuration_acceptance_store import ConfigurationAcceptanceStore
from control_plane_kit_operations.postgres.configuration_evidence import _Unavailable
from tests.configuration_transfer_fixture import ConfigurationTransferredConsumerFixture


class PostgresConfigurationTransferCarryTests(ConfigurationTransferredConsumerFixture, unittest.TestCase):
    def test_missing_latest_aba_receipt_does_not_fall_back_to_original_transfer(self):
        _, latest = self.carry.carry_twice()
        self.carry.remove_occurrence(latest)
        before = self.proof_snapshot()
        with self.base.unit_of_work() as uow:
            store = uow.stores.configuration_acceptance
            self.assertEqual(store.read_current_configuration("workspace-a", node_id="api").state, "unavailable")
            self.assertEqual(store.read_configuration_use("workspace-a", self.refs).state, "unavailable")
            for ref in self.refs:
                self.assertEqual(self.prove_transfer(uow, ref).acceptance_revision, self.revision)
        self.assertEqual(self.proof_snapshot(), before)

    def test_departed_transfer_remains_known_provenance_without_current_membership(self):
        from control_plane_kit_core.planning import NodeTarget, RemoveNodeResource
        graph = replace(self.carry.graph, nodes={"worker": self.carry.graph.nodes["worker"]},
            runtimes={"runtime-a": replace(self.carry.graph.runtimes["runtime-a"], children=("worker",))})
        self.carry.advance(self.carry.prepare("remove-api", "graph-worker-only",
            RemoveNodeResource(NodeTarget("api")), graph=graph))
        before = self.proof_snapshot()
        with self.base.unit_of_work() as uow:
            value = uow.stores.configuration_acceptance.read_configuration_use("workspace-a", self.refs)
            self.assertEqual((value.state, value.bindings), ("complete", ()))
            for ref in self.refs:
                self.assertEqual(self.prove_transfer(uow, ref).acceptance_revision, self.revision)
        self.assertEqual(self.proof_snapshot(), before)

    def test_issued_carry_requires_live_pair_and_existing_proof_memo(self):
        actual = ConfigurationAcceptanceStore._require_current
        calls = []

        def probe(store, prepared):
            actual(store, prepared)
            calls.append(prepared.plan.plan_id)
            sources = prepared.evidence_read.sources
            keys = tuple(key for key in sources if key[0] == "configuration-accepted-transfer")
            self.assertEqual(len(keys), len(self.refs))
            key = keys[0]
            proof = sources.pop(key)
            try:
                # The issued publication path must refuse a missing immutable
                # proof, rather than start a fresh unreserved cold read.
                with self.assertRaises(_Unavailable):
                    actual(store, prepared)
                self.assertNotIn(key, sources)
            finally:
                sources[key] = proof
            connection = store._connection
            connection.execute("SAVEPOINT transferred_carry_pair_probe")
            try:
                connection.execute("UPDATE cpk_configuration_claims SET accepted_revision=NULL WHERE "
                    "(run_id,activity_id,attempt,artifact_id)=(%s,%s,%s,%s)", self.key(self.refs[0]))
                with self.assertRaises(_Unavailable):
                    actual(store, prepared)
            finally:
                connection.execute("ROLLBACK TO SAVEPOINT transferred_carry_pair_probe")
                connection.execute("RELEASE SAVEPOINT transferred_carry_pair_probe")
            with self.recorded_exclusion(connection):
                with self.assertRaises(_Unavailable):
                    actual(store, prepared)
            return actual(store, prepared)

        before = self.transfer_snapshot()
        with mock.patch.object(ConfigurationAcceptanceStore, "_require_current", probe):
            self.carry.add_runtime()
        # Current CAS, original event, original action, receipt insertion,
        # then C's transfer phase each require the same live owner proof.
        self.assertEqual(calls, ["plan-add-runtime"] * 5)
        self.assertEqual(self.transfer_snapshot(), before)

    def test_unrelated_runtime_round_trip_carries_transferred_and_protective_sources(self):
        before = self.transfer_snapshot()
        middle, latest = self.carry.carry_twice()
        self.assertNotEqual(middle.desired_graph_revision, self.revision)
        self.assertNotEqual(latest.desired_graph_revision, self.revision)
        self.assertEqual(self.transfer_snapshot(), before)
        self.assert_zero_active_api()
        with self.base.unit_of_work() as uow:
            current = uow.stores.configuration_acceptance.read_current_configuration("workspace-a", node_id="api")
            self.assertEqual(current.state, "complete")
            self.assertEqual(tuple(binding.ref for binding in current.bindings), self.refs)
            self.assertTrue(all(binding.source.identity == self.original.identity for binding in current.bindings))
            for ref in self.refs:
                self.assertEqual(self.prove_transfer(uow, ref).acceptance_revision, self.revision)
