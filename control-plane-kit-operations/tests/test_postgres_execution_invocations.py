"""Command-owned invocation storage; these fixtures are not admission permits."""

from dataclasses import replace
import unittest

import control_plane_kit_operations as operations
from control_plane_kit_core.operations import RecoveryDecisionKind
from control_plane_kit_operations.effect_permissions import ExecutionCommandReference
from tests.execution_lease_recovery_fixture import PostgresExecutionLeaseRecoveryFixture
from tests import test_postgres_managed_execution_receipts as receipt_fixtures
from tests.test_postgres_current_effect_permissions import CurrentEffectPermissionFixture
from control_plane_kit_operations.execution_invocations import (
    ExecutionInvocationRecord, ExecutionInvocationKind as Kind,
    ExecutionInvocationDecision as Decision, ExecutionInvocationDisposition as Disposition,
    ExecutionInvocationOutcome as Outcome, ExecutionInvocationConflict, ExecutionInvocationCursor,
)
from control_plane_kit_operations.records import ExecutionCommandResultRecord, CoordinatorStatus
from control_plane_kit_operations.postgres import install_schema, SchemaInstallationError
from psycopg.types.json import Jsonb
from tests.test_postgres_managed_effect_permissions import _ManagedPermissionStartFixture
from control_plane_kit_operations.execution_invocations import secret_invocation_target_fingerprint
from control_plane_kit_operations.secret_providers import (
    AuthorizeSecretUse, authorize_secret_use_in_unit_of_work, secret_use_correlation_for,
    SecretProviderRegistrationService, RevokeSecretProviderCommand,
)
from control_plane_kit_core.policies import PolicyScope


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


class PostgresExecutionInvocationStateTests(CurrentEffectPermissionFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.bound = self.binding()
        self.started = self.fresh_start().execute(self.start_command(), command_context=self.bound).attempt
        self.at = self.started.original_start_event.occurred_at

    def planned(self):
        with self.unit_of_work() as uow:
            intent = uow.stores.effect_attempt_intents.get(self.started.state.identity)
        return ExecutionInvocationRecord(self.bound.receipt, "workspace-a", "start-runtime",
            Kind.PREPARED_RUNTIME_DISPATCH, "2" * 64, self.at,
            attempt=self.started.state.identity.attempt,
            original_event_id=self.started.original_start_event.event_id,
            request_fingerprint=intent.request_fingerprint)

    def insert(self, record):
        with self.unit_of_work() as uow:
            uow.stores.execution.add_command_invocation(record)
            uow.commit()

    def retained(self, record):
        with self.unit_of_work() as uow:
            return uow.stores.execution.command_invocation(record.command, record.invocation_id)

    def complete_parent(self):
        with self.unit_of_work() as uow:
            run = uow.stores.execution.get_run(self.bound.receipt.run_id)
            completed = uow.stores.execution.complete_command_receipt(run.run_id,
                self.bound.receipt.idempotency_key, intent_fingerprint=self.bound.receipt.intent_fingerprint,
                completed_at=self.at, result=ExecutionCommandResultRecord(run, CoordinatorStatus.UNCERTAIN, 1, "start-runtime"))
            self.assertIsNotNone(completed)
            uow.commit()

    def test_planned_decision_is_transactional_and_first_committed_denial_is_immutable(self):
        planned = self.planned()
        self.insert(planned)
        self.revoke()
        denied = planned.decided(Decision(2, denial_code="required-scope-denied"), self.at)
        with self.unit_of_work() as uow:
            self.assertTrue(uow.stores.execution.replace_command_invocation(planned, denied))
        self.assertEqual(self.retained(planned), planned)
        with self.unit_of_work() as uow:
            self.assertTrue(uow.stores.execution.replace_command_invocation(planned, denied))
            uow.commit()
        with self.unit_of_work() as uow:
            self.assertFalse(uow.stores.execution.replace_command_invocation(planned, denied))
            uow.commit()
        with self.assertRaises(ExecutionInvocationConflict):
            with self.unit_of_work() as uow:
                uow.stores.execution.replace_command_invocation(planned, planned.decided(Decision(3), self.at))
                uow.commit()
        self.assertEqual(self.retained(planned), denied)

    def test_owned_classification_survives_revoke_and_parent_completion_without_new_permission(self):
        planned = self.planned()
        self.insert(planned)
        admitted = planned.decided(Decision(1), self.at)
        with self.unit_of_work() as uow:
            self.assertTrue(uow.stores.execution.replace_command_invocation(planned, admitted))
            uow.commit()
        self.revoke()
        self.complete_parent()
        returned = admitted.completed(Outcome.RETURNED, self.at)
        with self.unit_of_work() as uow:
            self.assertTrue(uow.stores.execution.replace_command_invocation(admitted, returned))
            uow.commit()
        with self.unit_of_work() as uow:
            self.assertFalse(uow.stores.execution.replace_command_invocation(admitted, returned))
            uow.commit()
        with self.assertRaises(ExecutionInvocationConflict):
            with self.unit_of_work() as uow:
                uow.stores.execution.replace_command_invocation(admitted, admitted.completed(Outcome.UNKNOWN, self.at))
        self.assertEqual(self.retained(planned), returned)
        with self.unit_of_work() as uow:
            self.assertEqual(uow.stores.effect_attempts.get(self.started.state.identity), self.started)
        self.assertEqual(self.connection.execute("SELECT count(*) FROM cpk_effect_attempt_outcomes WHERE run_id=%s AND activity_id=%s AND attempt=%s",
            (self.started.state.identity.run_id.value, self.started.state.identity.activity_id,
                self.started.state.identity.attempt)).fetchone()[0], 0)

    def test_completed_parent_cannot_gain_a_new_planned_decision(self):
        planned = self.planned()
        self.insert(planned)
        self.complete_parent()
        with self.assertRaises(ExecutionInvocationConflict):
            with self.unit_of_work() as uow:
                uow.stores.execution.replace_command_invocation(planned, planned.decided(Decision(1), self.at))
        self.assertEqual(self.retained(planned), planned)

    def test_exact_attempt_and_workspace_activity_joins_refuse_false_correlations(self):
        planned = self.planned()
        for changes in ({"workspace_id": "workspace-b"}, {"activity_id": "different-activity"},
                {"original_event_id": "seed-run-started"}, {"request_fingerprint": "f" * 64}, {"attempt": 2}):
            with self.subTest(changes=changes), self.assertRaises(ExecutionInvocationConflict):
                self.insert(replace(planned, **changes))
        self.assertEqual(self.connection.execute("SELECT count(*) FROM cpk_execution_command_invocations").fetchone()[0], 0)
        with self.unit_of_work() as uow:
            self.assertEqual(uow.stores.effect_attempts.get(self.started.state.identity), self.started)

    def test_pages_are_bounded_and_cursor_cannot_cross_parent_scope(self):
        planned = self.planned()
        records = (planned, replace(planned, kind=Kind.ENDPOINT_OBSERVATION,
            disposition=Disposition.ADMITTED, decision_at=self.at, decision=Decision(1), outcome=Outcome.PENDING),
            replace(planned, kind=Kind.RECONCILIATION_OBSERVATION,
                disposition=Disposition.ADMITTED, decision_at=self.at, decision=Decision(1), outcome=Outcome.PENDING))
        for record in records:
            self.insert(record)
        with self.unit_of_work() as uow:
            first = uow.stores.execution.command_invocations(planned.command, limit=1)
            second = uow.stores.execution.command_invocations(planned.command, limit=100, cursor=first.next_cursor)
            self.assertEqual(tuple(item.invocation_id for item in (*first.items, *second.items)),
                tuple(sorted(record.invocation_id for record in records)))
            self.assertIsNone(second.next_cursor)
            foreign = replace(first.next_cursor, command=replace(planned.command, idempotency_key="other-command"))
            with self.assertRaises(ExecutionInvocationConflict):
                uow.stores.execution.command_invocations(planned.command, cursor=foreign)
            for limit in (0, 101, True):
                with self.subTest(limit=limit), self.assertRaises(ExecutionInvocationConflict):
                    uow.stores.execution.command_invocations(planned.command, limit=limit)

    def test_corrupt_descriptor_refuses_reads_and_schema_verification_without_repair(self):
        planned = self.planned()
        self.insert(planned)
        corrupt = dict(planned.descriptor(), unexpected="corrupt")
        self.connection.execute("UPDATE cpk_execution_command_invocations SET descriptor=%s WHERE invocation_id=%s",
            (Jsonb(corrupt), planned.invocation_id))
        with self.assertRaises(ExecutionInvocationConflict):
            self.retained(planned)
        with self.assertRaises(SchemaInstallationError):
            install_schema(self.connection)
        self.assertEqual(self.connection.execute("SELECT descriptor FROM cpk_execution_command_invocations WHERE invocation_id=%s",
            (planned.invocation_id,)).fetchone()[0], corrupt)


class PostgresExecutionInvocationHealthJoinTests(_ManagedPermissionStartFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.started = self.admit().attempt
        self.at = self.started.original_start_event.occurred_at
        with self.unit_of_work() as uow:
            self.preparation = uow.stores.health_effect_preparations.get(self.started.state.identity)
            self.uses = tuple(uow.stores.secret_use_authorizations.get("workspace-a", authorization)
                for authorization in (self.preparation.transit_authorization_id,
                    self.preparation.workload_authorization_id))

    def record(self, authorized):
        return ExecutionInvocationRecord(self.bound.receipt, "workspace-a", self.started.state.identity.activity_id,
            Kind.SECRET_RESOLUTION, secret_invocation_target_fingerprint(authorized), self.at,
            attempt=self.started.state.identity.attempt, original_event_id=self.preparation.original_event_id,
            request_fingerprint=self.preparation.request_fingerprint, authorization_id=authorized.authorization_id,
            disposition=Disposition.ADMITTED, decision_at=self.at,
            decision=Decision(1, self.selected.assignment_id, self.selected.revision), outcome=Outcome.PENDING)

    def insert(self, record):
        with self.unit_of_work() as uow:
            uow.stores.execution.add_command_invocation(record)
            uow.commit()

    def test_both_health_slots_reuse_existing_operation_join_and_survive_provider_revocation(self):
        records = tuple(self.record(use) for use in self.uses)
        for use, record in zip(self.uses, records):
            self.assertIsNone(use.effect_id)
            self.insert(record)
        SecretProviderRegistrationService(self.unit_of_work).revoke_provider(RevokeSecretProviderCommand(
            "workspace-a", self.provider.provider_id, "operator-a", self.at, (PolicyScope.SECRET_PROVIDER_REVOKE,)))
        with self.unit_of_work() as uow:
            for record in records:
                self.assertEqual(uow.stores.execution.command_invocation(record.command, record.invocation_id), record)
        install_schema(self.connection)

    def test_swapped_health_reference_purpose_and_unrelated_operation_are_not_the_selected_use(self):
        with self.unit_of_work() as uow:
            original_markers = uow.stores.execution.command_invocations(self.bound.receipt).items
        transit, workload = self.uses
        record = self.record(transit)
        with self.assertRaises(ExecutionInvocationConflict):
            self.insert(replace(record, authorization_id=workload.authorization_id))
        with self.assertRaises(ExecutionInvocationConflict):
            self.insert(replace(record, attempt=None, original_event_id=None, request_fingerprint=None))
        fields = dict(workspace_id="workspace-a", reference=transit.reference, intent=transit.intent,
            actor_subject=transit.actor_subject, operation_id="different-operation", session_id="session-a",
            run_id=transit.run_id, activity_id=transit.activity_id)
        with self.unit_of_work() as uow:
            unrelated, _ = authorize_secret_use_in_unit_of_work(uow, AuthorizeSecretUse(**fields,
                correlation_id=secret_use_correlation_for(**fields), requested_at=self.at,
                actor_scopes=self.original.granted_scopes))
            uow.commit()
        with self.assertRaises(ExecutionInvocationConflict):
            self.insert(self.record(unrelated))
        with self.assertRaises(ExecutionInvocationConflict):
            self.insert(replace(self.record(unrelated), attempt=None,
                original_event_id=None, request_fingerprint=None))
        self.assertEqual(self.connection.execute(
            "SELECT count(*) FROM cpk_execution_command_invocations WHERE kind='secret-resolution'").fetchone()[0], 0)
        with self.unit_of_work() as uow:
            self.assertEqual(uow.stores.execution.command_invocations(self.bound.receipt).items, original_markers)

    def test_missing_health_preparation_is_not_a_generic_effect_id_fallback(self):
        record = self.record(self.uses[0])
        self.insert(record)
        before = self.connection.execute(
            "SELECT invocation_id, descriptor FROM cpk_execution_command_invocations ORDER BY invocation_id").fetchall()
        self.connection.execute("DELETE FROM cpk_health_effect_preparations WHERE run_id=%s AND activity_id=%s AND attempt=%s",
            (record.command.run_id, record.activity_id, record.attempt))
        with self.assertRaises(ExecutionInvocationConflict):
            with self.unit_of_work() as uow:
                uow.stores.execution.command_invocation(record.command, record.invocation_id)
        self.assertEqual(self.connection.execute(
            "SELECT invocation_id, descriptor FROM cpk_execution_command_invocations ORDER BY invocation_id").fetchall(), before)
