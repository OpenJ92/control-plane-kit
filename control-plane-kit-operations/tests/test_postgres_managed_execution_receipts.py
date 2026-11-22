"""The existing receipt store retains managed actor intent without granting it."""

import unittest

from control_plane_kit_core.operations import RecoveryDecisionKind
from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_operations import records
from tests.execution_lease_recovery_fixture import PostgresExecutionLeaseRecoveryFixture
from tests.test_cpk_server_adapters import operator_principal


class PostgresManagedExecutionReceiptTests(PostgresExecutionLeaseRecoveryFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.seed_truth(RecoveryDecisionKind.RENEW_ACTIVE_CLAIM, history="active-empty")

    def receipt(self):
        value_type = getattr(records, "ManagedExecutionCommandIntent", None)
        self.assertIsNotNone(value_type, "managed receipt cannot retain genuine actor intent")
        context = operator_principal(subject_id="authenticated-operator").command_context("workspace-a")
        intent = value_type.from_context(context)
        with self.unit_of_work() as uow:
            run = uow.stores.execution.get_run("run-a")
        fingerprint = records.execution_command_intent_fingerprint(
            run_id="run-a", worker_id="worker-a", authority_scopes=(PolicyScope.EXECUTION_OPERATE,),
            claim_generation=7, max_effects=1, managed_intent=intent,
        )
        return records.ExecutionCommandReceiptRecord(
            run_id="run-a", idempotency_key="managed-execute-a", intent_fingerprint=fingerprint,
            worker_id="worker-a", authority_scopes=(PolicyScope.EXECUTION_OPERATE,), claim_generation=7,
            max_effects=1, admitted_at="2030-01-01T00:00:00Z", initial_run=run, managed_intent=intent,
        )

    def test_managed_intent_roundtrip_retains_actor_separately_from_worker(self):
        original = self.receipt()
        with self.unit_of_work() as uow:
            uow.stores.execution.add_command_receipt(original)
            uow.commit()
        with self.unit_of_work() as uow:
            retained = uow.stores.execution.command_receipt_for_idempotency("run-a", "managed-execute-a")
        self.assertEqual(retained, original)
        self.assertEqual(retained.managed_intent.actor.subject_id, "authenticated-operator")
        self.assertEqual(retained.worker_id, "worker-a")
        self.assertIs(retained.status, records.ExecutionCommandReceiptStatus.INCOMPLETE)

    def test_managed_receipt_is_not_retained_without_caller_commit(self):
        original = self.receipt()
        with self.unit_of_work() as uow:
            uow.stores.execution.add_command_receipt(original)
        with self.unit_of_work() as uow:
            self.assertIsNone(uow.stores.execution.command_receipt_for_idempotency("run-a", "managed-execute-a"))
