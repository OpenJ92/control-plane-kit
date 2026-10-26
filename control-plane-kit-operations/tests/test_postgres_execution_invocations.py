"""Command-owned invocation storage; these fixtures are not admission permits."""

from dataclasses import replace
import unittest

import control_plane_kit_operations as operations
from control_plane_kit_core.operations import RecoveryDecisionKind
from control_plane_kit_operations.effect_permissions import ExecutionCommandReference
from tests.execution_lease_recovery_fixture import PostgresExecutionLeaseRecoveryFixture
from tests import test_postgres_managed_execution_receipts as receipt_fixtures


class PostgresExecutionCommandInvocationTests(PostgresExecutionLeaseRecoveryFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.seed_truth(RecoveryDecisionKind.RENEW_ACTIVE_CLAIM, history="active-empty")
        self.parent = receipt_fixtures.PostgresManagedExecutionReceiptTests.receipt(self)
        with self.unit_of_work() as uow:
            uow.stores.execution.add_command_receipt(self.parent)
            uow.commit()

    def public(self, name):
        value = getattr(operations, name, None)
        self.assertIsNotNone(value, "command-owned invocation history is unavailable: " + name)
        return value

    def admitted_record(self):
        record = self.public("ExecutionInvocationRecord")
        kind = self.public("ExecutionInvocationKind")
        disposition = self.public("ExecutionInvocationDisposition")
        outcome = self.public("ExecutionInvocationOutcome")
        decision = self.public("ExecutionInvocationDecision")
        # An owner-local storage value, not proof that the coordinator admitted
        # an endpoint operation for this fixture's ordinary runtime activity.
        return record(command=ExecutionCommandReference(self.parent.run_id,
            self.parent.idempotency_key, self.parent.intent_fingerprint),
            workspace_id="workspace-a", activity_id="start-runtime",
            kind=kind.ENDPOINT_OBSERVATION, target_fingerprint="1" * 64,
            created_at="2030-01-01T00:00:00Z", disposition=disposition.ADMITTED,
            decision_at="2030-01-01T00:00:00Z", decision=decision(permission_revision=1),
            outcome=outcome.PENDING)

    def test_invocation_retention_requires_caller_commit_and_roundtrips_exactly(self):
        record = self.admitted_record()
        with self.unit_of_work() as uow:
            self.assertEqual(uow.stores.execution.add_command_invocation(record), record)
        with self.unit_of_work() as uow:
            self.assertIsNone(uow.stores.execution.command_invocation(
                record.command, record.invocation_id))
            self.assertEqual(uow.stores.execution.add_command_invocation(record), record)
            uow.commit()
        with self.unit_of_work() as uow:
            self.assertEqual(uow.stores.execution.command_invocation(
                record.command, record.invocation_id), record)
            self.assertEqual(uow.stores.execution.command_receipt_for_idempotency(
                self.parent.run_id, self.parent.idempotency_key), self.parent)

    def test_invocation_rejects_a_different_parent_fingerprint_without_retention(self):
        record = self.admitted_record()
        wrong = replace(record, command=replace(record.command, intent_fingerprint="f" * 64))
        error = self.public("ExecutionInvocationConflict")
        with self.assertRaises(error):
            with self.unit_of_work() as uow:
                uow.stores.execution.add_command_invocation(wrong)
                uow.commit()
        with self.unit_of_work() as uow:
            self.assertIsNone(uow.stores.execution.command_invocation(
                record.command, wrong.invocation_id))
            self.assertEqual(uow.stores.execution.command_receipt_for_idempotency(
                self.parent.run_id, self.parent.idempotency_key), self.parent)
        self.assertEqual(self.connection.execute(
            "SELECT count(*) FROM cpk_execution_command_invocations WHERE run_id=%s AND command_key=%s",
            (self.parent.run_id, self.parent.idempotency_key)).fetchone()[0], 0)
