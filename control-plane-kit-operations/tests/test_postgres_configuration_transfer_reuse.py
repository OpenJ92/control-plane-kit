"""Genuine ordinary reuse from zero active claims; recorded transfer premise."""
from dataclasses import replace
import unittest
from unittest import mock

from control_plane_kit_operations._configuration_preparation import _require_prepared
from control_plane_kit_operations.postgres.configuration_preparation_store import ConfigurationPreparationStore
from control_plane_kit_operations.records import OperationsRecordError
from tests.configuration_transfer_fixture import ConfigurationTransferredConsumerFixture


class PostgresConfigurationTransferReuseTests(ConfigurationTransferredConsumerFixture, unittest.TestCase):
    def test_zero_outstanding_birth_reuses_and_three_issued_checks_remain_live(self):
        before = self.transfer_snapshot()
        calls = []
        actual = ConfigurationPreparationStore._require_current

        def probe(store, prepared):
            calls.append(prepared.identity)
            actual(store, prepared)
            for forged in (None, replace(prepared)):
                with self.assertRaises(OperationsRecordError):
                    _require_prepared(forged, store._connection, prepared.identity, prepared.intent)
            with self.base.unit_of_work() as foreign:
                with self.assertRaises(OperationsRecordError):
                    _require_prepared(prepared, foreign.stores.connection, prepared.identity, prepared.intent)
            # Probe the actual issued recheck after preparation, then restore
            # the deliberate one-sided damage before the real command resumes.
            connection = store._connection
            connection.execute("SAVEPOINT transferred_pair_probe")
            try:
                connection.execute("UPDATE cpk_configuration_claims SET accepted_revision=NULL WHERE "
                    "(run_id,activity_id,attempt,artifact_id)=(%s,%s,%s,%s)", self.key(self.refs[0]))
                with self.assertRaises(OperationsRecordError):
                    actual(store, prepared)
            finally:
                connection.execute("ROLLBACK TO SAVEPOINT transferred_pair_probe")
                connection.execute("RELEASE SAVEPOINT transferred_pair_probe")
            with self.recorded_exclusion(connection):
                with self.assertRaises(OperationsRecordError):
                    actual(store, prepared)
            return actual(store, prepared)

        with mock.patch.object(ConfigurationPreparationStore, "_require_current", probe):
            command, original, _ = self.reuse.execute_reuse()
        # Intent insertion, attempt insertion, then configuration ref insertion.
        self.assertEqual(calls, [self.reuse.identity] * 3)
        self.assertEqual(original.intent.configuration_instances.instances, self.refs)
        self.assert_original_rows_preserved(before)
        for table in ("cpk_effect_configuration_refs", "cpk_configuration_claims"):
            self.assertEqual(self.connection.execute(f"SELECT accepted_revision,disposition_kind,protective FROM {table} "
                "WHERE run_id=%s ORDER BY artifact_id", (self.reuse.identity.run_id.value,)).fetchall(),
                [(None, "outstanding", True)] * len(self.refs))
        accepted = self.carry.advance(command)
        self.reuse.assert_accepted_reuse(accepted, original)
        self.assert_original_rows_preserved(before)

    def test_current_observation_requires_transferred_source_admission(self):
        self.assert_missing_birth_admission_refuses_reads()

    def test_new_untransferred_source_cannot_hide_missing_transferred_birth_proof(self):
        command, original, _ = self.reuse.execute_reuse()
        self.reuse.assert_accepted_reuse(self.carry.advance(command), original)
        self.assertNotEqual(original.identity, self.original.identity)
        self.assert_missing_birth_admission_refuses_reads()

    def test_v1_allocation_evidence_refuses_a_transferred_root(self):
        with self.base.unit_of_work() as uow:
            for ref in self.refs:
                evidence = uow.stores.configuration_preparation.read_allocation_evidence(ref)
                self.assertEqual(evidence.state, "unavailable")
                self.assertIsNone(evidence.birth)
                self.assertEqual(evidence.claims, ())

    def test_public_v1_inspection_refuses_transfer_but_reads_outstanding_worker(self):
        from control_plane_kit_operations.configuration_cleanup import (
            ConfigurationCleanupExpectedContext, ConfigurationCleanupSourceSelector,
        )
        from control_plane_kit_operations.configuration_cleanup_planning import (
            ConfigurationCleanupPlanningService, InspectConfigurationCleanup,
        )
        from tests.configuration_cleanup_postgres_fixture import command_context
        with self.base.unit_of_work() as uow:
            workspace = uow.stores.workspaces.get("workspace-a")
        expected = ConfigurationCleanupExpectedContext(workspace.current_graph_id,
            workspace.current_realized_projection_id, workspace.desired_graph_id,
            workspace.desired_realized_projection_id, workspace.desired_graph_revision)
        service = ConfigurationCleanupPlanningService(self.base.unit_of_work,
            clock=lambda: self.fail("inspection sampled publication clock"),
            id_factory=lambda: self.fail("inspection allocated publication ID"))
        before = self.proof_snapshot()
        for node, state in (("worker", "complete"), ("api", "unavailable")):
            original = self.membership.originals[node]
            command = InspectConfigurationCleanup("session-config", "workspace-a", expected,
                tuple(ConfigurationCleanupSourceSelector(original.identity, ref.artifact_id, ref)
                    for ref in original.intent.configuration_instances.instances))
            result = service.inspect(command, context=command_context())
            self.assertEqual(result.state, state)
        self.assertEqual(self.proof_snapshot(), before)
