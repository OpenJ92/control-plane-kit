"""B1 typed transfer schema and point proofs; no lawful transfer producer."""
import unittest

import psycopg

from control_plane_kit_operations.postgres import install_schema
from control_plane_kit_operations.postgres.configuration_evidence import _Unavailable
from tests.configuration_transfer_fixture import ConfigurationTransferFixture, TRANSFER_TABLE


class PostgresConfigurationTransferTests(ConfigurationTransferFixture, unittest.TestCase):
    def test_genuine_completion_and_acceptance_do_not_produce_transfers(self):
        self.require_transfer_schema()
        self.assertEqual(self.connection.execute(f"SELECT count(*) FROM {TRANSFER_TABLE}").fetchone(), (0,))
        self.assertEqual(self.membership.protective_claims(), self.membership.claims)
        replay = self.membership.advance()
        self.assertTrue(replay.replayed)
        self.assertEqual(self.connection.execute(f"SELECT count(*) FROM {TRANSFER_TABLE}").fetchone(), (0,))

    def test_recorded_transfer_point_proves_own_completion_and_original_receipt(self):
        self.record_transfer()
        before = self.transfer_snapshot()
        with self.base.unit_of_work() as uow:
            for ref in self.refs:
                proof = self.prove_transfer(uow, ref)
                self.assertEqual((proof.identity, proof.ref, proof.acceptance_revision,
                    proof.request_fingerprint, proof.selection_fingerprint, proof.outcome_fingerprint),
                    (self.original.identity, ref, self.revision, self.completion.request_fingerprint,
                     self.completion.selection_fingerprint, self.completion.outcome_fingerprint))
        self.assertEqual(self.transfer_snapshot(), before)
        for table in ("cpk_effect_configuration_refs", "cpk_configuration_claims"):
            self.assertEqual(self.connection.execute(f"SELECT count(*) FROM {table} WHERE protective").fetchone(), (0,))

    def test_one_sided_locator_or_orphan_transfer_cannot_commit(self):
        self.record_transfer()
        before = self.transfer_snapshot()
        ref = self.refs[0]
        statements = (
            ("UPDATE cpk_configuration_claims SET accepted_revision=NULL WHERE "
             "(run_id,activity_id,attempt,artifact_id)=(%s,%s,%s,%s)", self.key(ref)),
            (f"DELETE FROM {TRANSFER_TABLE} WHERE (run_id,activity_id,attempt,artifact_id)=(%s,%s,%s,%s)", self.key(ref)),
            ("DELETE FROM cpk_effect_configuration_refs WHERE "
             "(run_id,activity_id,attempt,artifact_id)=(%s,%s,%s,%s)", self.key(ref)),
        )
        for statement, parameters in statements:
            with self.subTest(statement=statement), self.assertRaises(psycopg.IntegrityError):
                with self.base.unit_of_work() as uow:
                    uow.stores.connection.execute(statement, parameters)
                    uow.commit()
            self.assertEqual(self.transfer_snapshot(), before)

    def test_wrong_commitments_material_scope_and_revision_cannot_commit(self):
        self.record_transfer()
        before = self.transfer_snapshot()
        for column, invalid in (("request_fingerprint", "f" * 64), ("selection_fingerprint", "f" * 64),
                ("outcome_fingerprint", "f" * 64), ("ref_digest", "f" * 64),
                ("allocation_id", "different-allocation"), ("node_id", "different-node"),
                ("acceptance_revision", self.revision + 1), ("acceptance_revision", 9007199254740992)):
            with self.subTest(column=column), self.assertRaises(psycopg.IntegrityError):
                with self.base.unit_of_work() as uow:
                    uow.stores.connection.execute(f"UPDATE {TRANSFER_TABLE} SET {column}=%s WHERE "
                        "(run_id,activity_id,attempt,artifact_id)=(%s,%s,%s,%s)", (invalid, *self.key(self.refs[0])))
                    uow.commit()
            self.assertEqual(self.transfer_snapshot(), before)

    def test_changed_original_acceptance_payload_refuses_fresh_full_proof(self):
        self.record_transfer()
        with self.base.unit_of_work() as uow:
            self.prove_transfer(uow)
        before = self.base.retained_snapshot(), self.transfer_snapshot()
        for table, field, identity in (("cpk_operation_actions", "action_id", self.acceptance.action.action_id),
                ("cpk_activity_events", "event_id", self.acceptance.event.event_id)):
            with self.subTest(table=table), self.base.unit_of_work() as uow:
                uow.stores.connection.execute(f"UPDATE {table} SET payload='{{}}'::jsonb WHERE {field}=%s", (identity,))
                with self.assertRaises(_Unavailable):
                    self.prove_transfer(uow)
                # Deliberate corruption rolls back; never becomes a new baseline.
            self.assertEqual((self.base.retained_snapshot(), self.transfer_snapshot()), before)

    def test_exact_current_schema_reentry_preserves_recorded_transfer(self):
        self.record_transfer()
        before = self.base.retained_snapshot(), self.transfer_snapshot()
        install_schema(self.connection)
        self.assertEqual((self.base.retained_snapshot(), self.transfer_snapshot()), before)
