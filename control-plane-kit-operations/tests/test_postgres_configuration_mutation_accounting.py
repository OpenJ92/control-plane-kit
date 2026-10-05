"""Mutation-return evidence shares the caller's configuration read budget."""
import unittest

import psycopg

from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_core.operations.lifecycle import ActivityRunStatus
from control_plane_kit_operations._configuration_preparation import _configuration_accounting
from control_plane_kit_operations.configuration_preparation import ConfigurationEvidenceFootprint
from control_plane_kit_operations.postgres import PostgresUnitOfWork
from control_plane_kit_operations.postgres.configuration_evidence import _Capacity
from control_plane_kit_operations.records import (
    CoordinatorStatus, ExecutionCommandReceiptRecord, ExecutionCommandReceiptStatus,
    ExecutionCommandResultRecord, execution_command_intent_fingerprint,
    OperationsRecordError,
)
from tests.postgres_effect_attempt_store_fixture import PostgresEffectAttemptStoreFixture
from tests.test_postgres_configuration_evidence import _ObservedConnection


class _ReceiptAcknowledgmentFault:
    """Execute the real insert, then damage only its returned acknowledgment."""
    def __init__(self, connection, acknowledgment, inserted):
        self.connection, self.acknowledgment, self.inserted = connection, acknowledgment, inserted

    def __getattr__(self, name):
        return getattr(self.connection, name)

    def execute(self, query, params=(), **kwargs):
        cursor = self.connection.execute(query, params, **kwargs)
        if " ".join(str(query).lower().split()).startswith("insert into cpk_execution_command_receipts"):
            if cursor.fetchall() != [(1,)]:
                raise AssertionError("fault must follow an actual inserted receipt")
            self.inserted.append(True)
            return self
        return cursor

    def fetchall(self):
        return self.acknowledgment


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

    def receipt(self):
        scopes = (PolicyScope.EXECUTION_OPERATE,)
        with self.unit_of_work() as uow:
            run = uow.stores.execution.get_run("run-a")
        return ExecutionCommandReceiptRecord("run-a", "accounted-insert",
            execution_command_intent_fingerprint(run_id="run-a", worker_id="worker-a",
                authority_scopes=scopes, claim_generation=7, max_effects=1),
            "worker-a", scopes, 7, 1, "2030-01-01T00:00:00Z", run)

    def test_receipt_insert_charges_success_failed_sql_and_refuses_before_write(self):
        receipt = self.receipt()
        observations = dict(rows=0, bytes=0, largest_cell=0, statements=0)
        with self.measured_uow(observations) as uow:
            insert = lambda: uow.stores.execution.add_command_receipt(receipt)
            with _configuration_accounting("run-a") as accounting:
                accounting.used = ConfigurationEvidenceFootprint(4096, 0, 0, 0)
                with self.assertRaises(_Capacity):
                    insert()
                self.assertEqual(observations["statements"], 0)
            self.assertEqual(self.accounted(observations, insert), receipt)
            with _configuration_accounting("run-a") as accounting:
                with self.assertRaises(psycopg.errors.UniqueViolation):
                    insert()
                self.assertEqual(accounting.used, ConfigurationEvidenceFootprint(1, 1, 1, 1))
        with self.unit_of_work() as uow:
            self.assertIsNone(uow.stores.execution.command_receipt_for_idempotency(
                receipt.run_id, receipt.idempotency_key))

    def test_receipt_insert_requires_exact_acknowledgment_and_rolls_back(self):
        receipt = self.receipt()
        for acknowledgment in ([], [(0,)]):
            with self.subTest(acknowledgment=acknowledgment):
                inserted = []
                with self.assertRaises(OperationsRecordError):
                    with _configuration_accounting("run-a"), PostgresUnitOfWork(lambda:
                            _ReceiptAcknowledgmentFault(psycopg.connect(self.database_url), acknowledgment, inserted)) as uow:
                        uow.stores.execution.add_command_receipt(receipt)
                        uow.commit()
                self.assertEqual(inserted, [True])
                with self.unit_of_work() as uow:
                    self.assertIsNone(uow.stores.execution.command_receipt_for_idempotency(
                        receipt.run_id, receipt.idempotency_key))

    def test_run_cas_charges_success_miss_and_refuses_capacity_before_write(self):
        observations = dict(rows=0, bytes=0, largest_cell=0, statements=0)
        with self.measured_uow(observations) as uow:
            original = uow.stores.execution.get_run("run-a")
            change = lambda: uow.stores.execution._compare_and_set_run_status("run-a",
                expected=original.status, replacement=ActivityRunStatus.RUNNING,
                started_at="2030-01-01T00:00:01Z")
            with _configuration_accounting("run-a") as accounting:
                accounting.used = ConfigurationEvidenceFootprint(4096, 0, 0, 0)
                before_statements = observations["statements"]
                with self.assertRaises(_Capacity):
                    change()
                self.assertEqual(observations["statements"], before_statements)
            changed = self.accounted(observations, change)
            self.assertIs(changed.status, ActivityRunStatus.RUNNING)
            self.assertEqual(changed.metadata, original.metadata)
            self.assertEqual(changed.started_at, "2030-01-01T00:00:01Z")
            self.assertIsNone(self.accounted(observations, change))
        with self.unit_of_work() as uow:
            self.assertEqual(uow.stores.execution.get_run("run-a"), original)

    def test_run_cas_matched_oversized_row_refuses_and_rolls_back(self):
        with self.unit_of_work() as uow:
            original = uow.stores.execution.get_run("run-a")
        observations = dict(rows=0, bytes=0, largest_cell=0, statements=0)
        with self.assertRaises(OperationsRecordError):
            with self.measured_uow(observations) as uow:
                uow.stores.connection.execute("UPDATE cpk_activity_runs "
                    "SET metadata=jsonb_build_object('oversized',repeat('x',65537)) WHERE run_id='run-a'")
                observations.update(rows=0, bytes=0, largest_cell=0, statements=0)
                with _configuration_accounting("run-a") as accounting:
                    try:
                        uow.stores.execution._compare_and_set_run_status("run-a",
                            expected=original.status, replacement=ActivityRunStatus.RUNNING,
                            started_at="2030-01-01T00:00:01Z")
                    finally:
                        self.assertEqual(accounting.used.statements, 1)
                        self.assertEqual(observations["statements"], 1)
                        self.assertEqual(observations["largest_cell"], 1)
                        self.assertEqual(accounting.used.value_octets, 1)
                uow.commit()
        with self.unit_of_work() as uow:
            self.assertEqual(uow.stores.execution.get_run("run-a"), original)

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
