"""Mutation-return evidence shares the caller's configuration read budget."""
import unittest

import psycopg

from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_operations._configuration_preparation import _configuration_accounting
from control_plane_kit_operations.configuration_preparation import ConfigurationEvidenceFootprint
from control_plane_kit_operations.postgres import PostgresUnitOfWork
from control_plane_kit_operations.postgres.configuration_evidence import _Capacity
from control_plane_kit_operations.records import (
    CoordinatorStatus, ExecutionCommandReceiptRecord, ExecutionCommandReceiptStatus,
    ExecutionCommandResultRecord, execution_command_intent_fingerprint,
)
from tests.postgres_effect_attempt_store_fixture import PostgresEffectAttemptStoreFixture
from tests.test_postgres_configuration_evidence import _ObservedConnection


class PostgresConfigurationMutationAccountingTests(PostgresEffectAttemptStoreFixture, unittest.TestCase):
    def measured_uow(self, observations):
        return PostgresUnitOfWork(lambda: _ObservedConnection(psycopg.connect(self.database_url), observations))

    def accounted(self, observations, operation):
        observations.update(rows=0, bytes=0, largest_cell=0, statements=0)
        with _configuration_accounting("run-a") as accounting:
            result = operation()
            # Observe real cursor statements/values, not an expected SQL shape.
            self.assertEqual(accounting.used.statements, observations["statements"])
            self.assertGreaterEqual(accounting.used.records, observations["rows"])
            self.assertGreaterEqual(accounting.used.accounted_bytes, observations["bytes"])
            self.assertGreater(accounting.used.statements, 0)
        return result

    def test_attempt_insert_returns_charge_success_and_zero_without_independent_commit(self):
        record = self.record(event_prefix="accounted-insert", original_ordinal=10)
        observations = dict(rows=0, bytes=0, largest_cell=0, statements=0)
        with self.measured_uow(observations) as uow:
            self.add_record_events(uow.stores, record)
            self.add_record_intent(uow.stores, record)
            with _configuration_accounting("run-a") as accounting:
                accounting.used = ConfigurationEvidenceFootprint(4096, 0, 0, 0)
                with self.assertRaises(_Capacity):
                    uow.stores.effect_attempts._insert_absent(record)
            with self.assertRaises(KeyError):
                uow.stores.effect_attempts.get(record.state.identity)
            insert = lambda: uow.stores.effect_attempts._insert_absent(record)
            self.assertEqual(self.accounted(observations, insert), record)
            self.assertIsNone(self.accounted(observations, insert))
        with self.unit_of_work() as uow:
            with self.assertRaises(KeyError):
                uow.stores.effect_attempts.get(record.state.identity)

    def test_attempt_cas_returns_charge_success_zero_and_prewrite_capacity_refusal(self):
        original = self.record(event_prefix="accounted-cas", original_ordinal=10)
        self.persist(original)
        replacement = self.transition(original, "succeeded", event_id="accounted-finished", ordinal=11)
        observations = dict(rows=0, bytes=0, largest_cell=0, statements=0)
        with self.measured_uow(observations) as uow:
            uow.stores.execution.add_event(replacement.latest_transition_event)
            change = lambda: uow.stores.effect_attempts.compare_and_set(original, replacement)
            with _configuration_accounting("run-a") as accounting:
                accounting.used = ConfigurationEvidenceFootprint(4096, 0, 0, 0)
                with self.assertRaises(_Capacity):
                    change()
            self.assertEqual(uow.stores.effect_attempts.get(original.state.identity), original)
            self.assertEqual(self.accounted(observations, change), replacement)
            self.assertIsNone(self.accounted(observations, change))
        with self.unit_of_work() as uow:
            self.assertEqual(uow.stores.effect_attempts.get(original.state.identity), original)

    def test_receipt_completion_returns_charge_success_zero_and_prewrite_capacity_refusal(self):
        scopes = (PolicyScope.EXECUTION_OPERATE,)
        with self.unit_of_work() as uow:
            run = uow.stores.execution.get_run("run-a")
            original = ExecutionCommandReceiptRecord("run-a", "accounted-completion",
                execution_command_intent_fingerprint(run_id="run-a", worker_id="worker-a",
                    authority_scopes=scopes, claim_generation=7, max_effects=1),
                "worker-a", scopes, 7, 1, "2030-01-01T00:00:00Z", run)
            uow.stores.execution.add_command_receipt(original)
            uow.commit()
        result = ExecutionCommandResultRecord(run, CoordinatorStatus.IN_FLIGHT, 0)
        observations = dict(rows=0, bytes=0, largest_cell=0, statements=0)
        with self.measured_uow(observations) as uow:
            complete = lambda: uow.stores.execution.complete_command_receipt("run-a", original.idempotency_key,
                intent_fingerprint=original.intent_fingerprint, completed_at="2030-01-01T00:00:01Z", result=result)
            with _configuration_accounting("run-a") as accounting:
                accounting.used = ConfigurationEvidenceFootprint(4096, 0, 0, 0)
                with self.assertRaises(_Capacity):
                    complete()
            self.assertEqual(uow.stores.execution.command_receipt_for_idempotency("run-a", original.idempotency_key), original)
            completed = self.accounted(observations, complete)
            self.assertIs(completed.status, ExecutionCommandReceiptStatus.COMPLETED)
            self.assertEqual(completed.result, result)
            self.assertIsNone(self.accounted(observations, complete))
        with self.unit_of_work() as uow:
            self.assertEqual(uow.stores.execution.command_receipt_for_idempotency("run-a", original.idempotency_key), original)
