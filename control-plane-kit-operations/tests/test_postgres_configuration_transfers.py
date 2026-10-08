"""Typed transfer schema and defensive point proofs over genuine C output."""
import unittest

import psycopg

from control_plane_kit_core.operations import EffectAttemptStatus
from control_plane_kit_operations.postgres import install_schema
from control_plane_kit_operations.postgres.configuration_evidence import _Unavailable, _joined_read
from control_plane_kit_operations.postgres.configuration_preparation_store import _paired_disposition
from control_plane_kit_operations.postgres.configuration_source import read_source
from control_plane_kit_operations.postgres.effect_outcome_store import EffectAttemptOutcomeStore
from tests.configuration_transfer_fixture import ConfigurationTransferFixture, TRANSFER_TABLE


class PostgresConfigurationTransferTests(ConfigurationTransferFixture, unittest.TestCase):
    def test_structural_pair_keeps_original_correlation_without_issuing_permission(self):
        self.assert_produced_transfers()
        with self.base.unit_of_work() as uow, _joined_read(uow.stores.connection) as read:
            ref = self.refs[0]
            paired = _paired_disposition(read, self.key(ref), ref)
            self.assertEqual((paired.kind, paired.acceptance_revision, paired.cleanup_identity),
                ("accepted-current", self.revision, None))
            self.assertEqual(uow.stores.configuration_completions.get(self.original.identity), self.completion)
            with self.assertRaises(_Unavailable):
                _paired_disposition(read, self.key(ref), ref, protective=True)

    def test_genuine_completion_and_acceptance_produce_exactly_once_on_original_replay(self):
        self.require_transfer_schema()
        self.assert_produced_transfers()
        self.assertEqual(self.connection.execute(f"SELECT count(*) FROM {TRANSFER_TABLE}").fetchone(), (len(self.refs),))
        self.assertEqual(self.membership.protective_claims(), [])
        before = self.proof_snapshot()
        replay = self.membership.advance()
        self.assertTrue(replay.replayed)
        self.assertEqual(self.proof_snapshot(), before)
        self.assert_produced_transfers()

    def test_produced_transfer_point_proves_own_completion_and_original_receipt(self):
        self.assert_produced_transfers()
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
        self.assert_produced_transfers()
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
        self.assert_produced_transfers()
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
        self.assert_produced_transfers()
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

    def test_exact_current_schema_reentry_preserves_produced_transfer(self):
        self.assert_produced_transfers()
        before = self.base.retained_snapshot(), self.transfer_snapshot()
        install_schema(self.connection)
        self.assertEqual((self.base.retained_snapshot(), self.transfer_snapshot()), before)

    def test_absent_own_admission_refuses_without_repair(self):
        self.assert_produced_transfers()
        with self.base.unit_of_work() as uow:
            self.prove_transfer(uow)
        before = self.proof_snapshot()
        with self.base.unit_of_work() as uow:
            # Deliberate retained corruption, never committed. Otherwise the
            # immediate FK would reject before the defensive reader runs.
            uow.stores.connection.execute(f"ALTER TABLE {TRANSFER_TABLE} DROP CONSTRAINT cpk_claim_transfers_completion_fk")
            uow.stores.connection.execute("DELETE FROM cpk_configuration_invocation_completions "
                "WHERE (run_id,activity_id,attempt)=(%s,%s,%s)", self.key(self.refs[0])[:3])
            self.assertIsNone(uow.stores.configuration_completions.get(self.original.identity))
            with self.assertRaises(_Unavailable):
                self.prove_transfer(uow)
        self.assertEqual(self.proof_snapshot(), before)
        with self.base.unit_of_work() as uow:
            self.prove_transfer(uow)

    def test_admitted_failed_own_source_is_not_successful_transfer_proof(self):
        self.assert_recorded_terminal_refuses(failed=True)

    def test_unprofiled_own_success_without_admission_is_not_transfer_proof(self):
        self.assert_recorded_terminal_refuses(failed=False)

    def assert_recorded_terminal_refuses(self, *, failed):
        self.assert_produced_transfers()
        with self.base.unit_of_work() as uow:
            self.prove_transfer(uow)
        before = self.proof_snapshot()
        with self.base.unit_of_work() as uow:
            recorded = self.corrupt_same_terminal(uow, failed=failed)
            with _joined_read(uow.stores.connection) as read:
                completion = uow.stores.configuration_completions.get(self.original.identity)
                if failed:
                    self.assertIsNotNone(completion, "the distinct failed-completion law needs admitted D1")
                    self.assertEqual((completion.identity, completion.request_fingerprint,
                        completion.selection_fingerprint, completion.outcome_fingerprint),
                        (self.completion.identity, self.completion.request_fingerprint,
                         self.completion.selection_fingerprint, recorded.outcome.outcome_fingerprint))
                else:
                    self.assertIsNone(completion)
                source = read_source(uow.stores.connection, self.original.identity, self.refs[0], read=read)
                self.assertEqual(source.state, "complete")
                outcome, attempt = EffectAttemptOutcomeStore(uow.stores.connection)._configuration_terminal(source.source, read)
                self.assertEqual((outcome, attempt), (recorded.outcome, recorded.attempt))
                self.assertIs(attempt.state.status, EffectAttemptStatus.FAILED if failed else EffectAttemptStatus.SUCCEEDED)
            with self.assertRaises(_Unavailable):
                self.prove_transfer(uow)
            # No commit: impossible post-acceptance history and FK drops roll back.
        self.assertEqual(self.proof_snapshot(), before)
        with self.base.unit_of_work() as uow:
            self.prove_transfer(uow)


class PostgresConfigurationTransferNeighborTests(ConfigurationTransferFixture, unittest.TestCase):
    transfer_node_ids = ("api", "worker")

    def test_real_same_run_neighbor_completion_cannot_replace_own_commitments(self):
        self.assert_produced_transfers()
        with self.base.unit_of_work() as uow:
            self.prove_transfer(uow)
            neighbor = uow.stores.configuration_completions.get(self.membership.originals["worker"].identity)
            self.assertIsNotNone(neighbor)
            self.assertEqual(neighbor.identity.run_id, self.completion.identity.run_id)
            self.assertNotEqual(neighbor.identity, self.completion.identity)
            self.assertNotEqual(neighbor.selection_fingerprint, self.completion.selection_fingerprint)
        before = self.proof_snapshot()
        with self.base.unit_of_work() as uow:
            uow.stores.connection.execute(f"ALTER TABLE {TRANSFER_TABLE} DROP CONSTRAINT cpk_claim_transfers_completion_fk")
            uow.stores.connection.execute(f"UPDATE {TRANSFER_TABLE} SET request_fingerprint=%s,"
                "selection_fingerprint=%s,outcome_fingerprint=%s WHERE (run_id,activity_id,attempt)=(%s,%s,%s)",
                (neighbor.request_fingerprint, neighbor.selection_fingerprint, neighbor.outcome_fingerprint,
                 *self.key(self.refs[0])[:3]))
            with self.assertRaises(_Unavailable):
                self.prove_transfer(uow)
        self.assertEqual(self.proof_snapshot(), before)
        with self.base.unit_of_work() as uow:
            self.prove_transfer(uow)
