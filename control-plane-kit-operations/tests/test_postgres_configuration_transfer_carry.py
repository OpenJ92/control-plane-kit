"""Mixed transferred/protective carry retains exact original provenance."""
import unittest
from unittest import mock

from control_plane_kit_operations.postgres.configuration_acceptance_store import ConfigurationAcceptanceStore
from control_plane_kit_operations.postgres.configuration_evidence import _Unavailable
from tests.configuration_transfer_fixture import ConfigurationTransferredConsumerFixture


class PostgresConfigurationTransferCarryTests(ConfigurationTransferredConsumerFixture, unittest.TestCase):
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
            return actual(store, prepared)

        before = self.transfer_snapshot()
        with mock.patch.object(ConfigurationAcceptanceStore, "_require_current", probe):
            self.carry.add_runtime()
        # Current CAS, original event, original action, then receipt insertion.
        self.assertEqual(calls, ["plan-add-runtime"] * 4)
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
