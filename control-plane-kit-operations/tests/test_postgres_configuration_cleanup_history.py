"""Cleanup freshness and retained-history laws through actual public owners."""
import unittest
import psycopg

from control_plane_kit_core.operations import EffectAttemptStatus
from control_plane_kit_core.configuration_instances import ConfigurationCleanupReason, ConfigurationCleanupStatus
from control_plane_kit_core.secrets import SecretReference
from control_plane_kit_core.runtime_effect_observation import (
    RuntimeEffectObservedSucceeded, RuntimeEffectObservationEvidence,
    RuntimeEffectObservedIndeterminate, RuntimeEffectObservationFailure, runtime_effect_intent_fingerprint,
)
from control_plane_kit_core.runtime_effects import RuntimeEffectResult
from control_plane_kit_operations.effect_attempt_fold import ExistingFold, NewlyFolded
from control_plane_kit_operations.effect_attempt_fold_interpreter import EffectAttemptFoldService
from control_plane_kit_operations.effect_attempt_reconciliation import (
    ReconcileEffectAttempt, EffectAttemptReconciliationConflict,
)
from control_plane_kit_operations.effect_attempt_reconciliation_interpreter import EffectAttemptReconciliationService
from control_plane_kit_operations.effect_attempt_start import EffectAttemptStartConflict, EffectAttemptStartDenied
from control_plane_kit_operations.effect_attempt_start_interpreter import EffectAttemptStartService
from control_plane_kit_operations.postgres import PostgresUnitOfWork
from control_plane_kit_operations._configuration_preparation import _ACCOUNTING, _configuration_accounting
from control_plane_kit_operations.configuration_preparation import ConfigurationEvidenceFootprint
from control_plane_kit_operations.coordinator import CoordinatorStatus, ExecutionCoordinatorConflict
from control_plane_kit_operations.runtime_authorities import RemoteDockerTlsAuthority
from tests.configuration_cleanup_execution_fixture import ConfigurationCleanupExecutionFixture
from tests.test_execution_admission import Sequence
from tests import test_postgres_configuration_cleanup_transactions as transactions
from tests.postgres_effect_attempt_reconciliation_fixture import RecordingObserver, UnitOfWorkLedger
from tests.postgres_effect_attempt_coordinator_fixture import RecordingRuntimeAdapter


class PostgresConfigurationCleanupHistoryTests(ConfigurationCleanupExecutionFixture, unittest.TestCase):
    removed_fold = transactions.PostgresConfigurationCleanupTransactionTests.removed_fold

    def no_ids(self):
        self.fail("retained cleanup replay allocated an identity")

    def reconciliation(self, original, observer, *, allocate=True, factory=None):
        factory = factory or self.unit_of_work
        fold = EffectAttemptFoldService(factory,
            id_factory=Sequence("cleanup-terminal") if allocate else self.no_ids)
        service = EffectAttemptReconciliationService(factory, observer, fold)
        command = ReconcileEffectAttempt(original.request_id, original.transition.identity,
            original.authority, original.fence)
        return service, command

    def test_generic_observed_success_retains_exclusion_without_member_retirement(self):
        claimed = self.ready_cleanup()
        original, started = self.start_cleanup(claimed)
        ledger = UnitOfWorkLedger(self.unit_of_work)
        observer = RecordingObserver(RuntimeEffectObservedSucceeded(
            started.attempt.original_start_event.event_id,
            runtime_effect_intent_fingerprint(original.intent),
            RuntimeEffectObservationEvidence({"fixture": "generic-observation-not-member-deletion"})), ledger=ledger)
        service, command = self.reconciliation(original, observer, factory=ledger)
        result = service.execute(command)
        self.assertIs(type(result), NewlyFolded)
        self.assertEqual(len(observer.calls), 1)
        identity = started.attempt.state.identity
        with self.unit_of_work() as uow:
            reservation = uow.stores.configuration_cleanup_ownership.get(identity)
            self.assertIs(reservation.status, EffectAttemptStatus.SUCCEEDED)
            self.assertIsNone(reservation.outcomes)
            self.assertEqual((len(reservation.members), len(reservation.completions), len(reservation.claims)), (1, 1, 1))
        self.assertEqual(self.connection.execute(
            "SELECT count(*) FROM cpk_configuration_cleanup_member_outcomes").fetchone(), (0,))
        before = self.ceiling_truth()
        service, command = self.reconciliation(original, observer, allocate=False)
        self.assertIs(type(service.execute(command)), ExistingFold)
        self.assertEqual(len(observer.calls), 1)
        self.assertEqual(self.ceiling_truth(), before)

    def test_reconciliation_replay_requires_complete_original_cleanup_proof(self):
        claimed = self.ready_cleanup()
        original, started = self.start_cleanup(claimed)
        EffectAttemptFoldService(self.unit_of_work, id_factory=Sequence("cleanup-terminal")).execute(
            self.removed_fold(original, started))
        observer = RecordingObserver(None)
        service, command = self.reconciliation(original, observer, allocate=False)
        before = self.ceiling_truth()
        self.assertIs(type(service.execute(command)), ExistingFold)
        self.assertEqual(observer.calls, [])
        self.assertEqual(self.ceiling_truth(), before)
        # A schema-valid corrupt historical digest is an explicit below-owner
        # negative premise; replay must re-prove B instead of accepting generic history.
        self.connection.execute("UPDATE cpk_configuration_cleanup_reservations "
            "SET proposal_fingerprint=%s", ("0" * 64,))
        before = self.ceiling_truth()
        with self.assertRaises(EffectAttemptReconciliationConflict):
            service.execute(command)
        self.assertEqual(observer.calls, [])
        self.assertEqual(self.ceiling_truth(), before)

    def test_dispatched_result_retains_original_after_authority_and_desired_drift(self):
        claimed = self.ready_cleanup()
        original, started = self.start_cleanup(claimed)
        with self.unit_of_work() as uow:
            previous = uow.stores.workspaces.get("workspace-a")
        self.desired_receiver("after-cleanup-dispatch", graph=self.canonical_receiver_graph)
        with self.unit_of_work() as uow:
            changed = uow.stores.workspaces.get("workspace-a")
            self.assertGreater(changed.desired_graph_revision, previous.desired_graph_revision)
        self.connection.execute("UPDATE cpk_runtime_authorities SET status='revoked' WHERE registration_id=%s",
            (self.registration.registration_id,))
        command = self.removed_fold(original, started)
        result = EffectAttemptFoldService(self.unit_of_work,
            id_factory=Sequence("cleanup-terminal")).execute(command)
        self.assertIs(type(result), NewlyFolded)
        with self.unit_of_work() as uow:
            reservation = uow.stores.configuration_cleanup_ownership.get(started.attempt.state.identity)
            self.assertIs(reservation.status, EffectAttemptStatus.SUCCEEDED)
            self.assertEqual(reservation.registration_id, self.registration.registration_id)
            self.assertEqual(len(reservation.outcomes.outcomes), 1)
        before = self.ceiling_truth()
        self.assertIs(type(EffectAttemptFoldService(self.unit_of_work,
            id_factory=self.no_ids).execute(command)), ExistingFold)
        self.assertEqual(self.ceiling_truth(), before)

    def test_fresh_start_refuses_revoked_authority_or_missing_whole_completion_before_ids(self):
        claimed = self.ready_cleanup()
        command = self.native_start_command(claimed, "cleanup-execution")
        cases = (
            ("UPDATE cpk_runtime_authorities SET status='revoked' WHERE registration_id=%s RETURNING 1",
                (self.registration.registration_id,)),
            ("DELETE FROM cpk_configuration_invocation_completions "
                "WHERE (run_id,activity_id,attempt)=(%s,%s,%s) RETURNING 1",
                (self.source_identity.run_id.value, self.source_identity.activity_id, self.source_identity.attempt)),
        )
        for query, params in cases:
            with self.subTest(query=query):
                changed = []
                before = self.ceiling_truth()

                def connect():
                    connection = psycopg.connect(self.database_url)
                    rows = connection.execute(query, params).fetchall()
                    if rows != [(1,)]:
                        connection.close()
                        raise AssertionError("negative premise must change exactly one actual row")
                    changed.append(True)
                    return connection

                with self.assertRaises((EffectAttemptStartConflict, EffectAttemptStartDenied)):
                    EffectAttemptStartService(lambda: PostgresUnitOfWork(connect),
                        id_factory=self.no_ids).execute(command)
                self.assertEqual(changed, [True])
                self.assertEqual(self.ceiling_truth(), before)

    def test_fresh_start_refuses_new_desired_revision_before_ids(self):
        claimed = self.ready_cleanup()
        command = self.native_start_command(claimed, "cleanup-execution")
        self.desired_receiver("before-cleanup-dispatch", graph=self.canonical_receiver_graph)
        before = self.ceiling_truth()
        with self.assertRaises((EffectAttemptStartConflict, EffectAttemptStartDenied)):
            EffectAttemptStartService(self.unit_of_work, id_factory=self.no_ids).execute(command)
        self.assertEqual(self.ceiling_truth(), before)

    def test_new_registration_between_context_and_start_never_dispatches_stale_authority(self):
        claimed = self.ready_cleanup()
        adapter = RecordingRuntimeAdapter()
        coordinator = self.coordinator(self.unit_of_work, adapter, "cleanup-execution")
        actual_start = coordinator._start_service
        owner = self
        selected = []

        class ChangeRegistrationBeforeRealStart:
            def execute(self, command):
                # Test scheduling premise outside the measured command: real
                # registration data changes after context load, before actual start.
                with _configuration_accounting("test-registration-change", active=False), owner.unit_of_work() as uow:
                    uow.stores.connection.execute("UPDATE cpk_runtime_authorities SET status='revoked' "
                        "WHERE registration_id=%s", (owner.registration.registration_id,))
                    replacement = uow.stores.runtime_authorities.register(workspace_id="workspace-a",
                        authority_ref=owner.registration.authority_ref, runtime_kind=owner.registration.runtime_kind,
                        authority=RemoteDockerTlsAuthority("tcp://changed-authority.invalid:2376",
                            SecretReference("secret://test/ca"), SecretReference("secret://test/cert"),
                            SecretReference("secret://test/key")), admitted_by="operator-a", admitted_at=owner.now())
                    uow.commit()
                owner.assertNotEqual(replacement.registration_id, owner.registration.registration_id)
                result = actual_start.execute(command)
                with _configuration_accounting("test-committed-binding"), owner.unit_of_work() as uow:
                    reservation = uow.stores.configuration_cleanup_ownership.get(result.attempt.state.identity)
                    owner.assertEqual(reservation.registration_id, replacement.registration_id)
                    owner.assertIs(reservation.status, EffectAttemptStatus.STARTED)
                selected.append(result.attempt.state.identity)
                return result

        coordinator._start_service = ChangeRegistrationBeforeRealStart()
        command = self.execution_command(claimed, "cleanup-execution")
        result = coordinator.execute(command)
        self.assertIs(result.status, CoordinatorStatus.UNCERTAIN)
        self.assertEqual(adapter.runtime_calls, [], "stale dispatch authority must never be invoked")
        self.assertEqual(len(selected), 1)
        with self.unit_of_work() as uow:
            retained = uow.stores.configuration_cleanup_ownership.get(selected[0])
            self.assertEqual(tuple((value.status, value.reason) for value in retained.outcomes.outcomes),
                ((ConfigurationCleanupStatus.UNKNOWN, ConfigurationCleanupReason.NOT_ATTEMPTED),))
        before = self.ceiling_truth()
        self.assertIs(coordinator.execute(command).status, CoordinatorStatus.UNCERTAIN)
        self.assertEqual(adapter.runtime_calls, [])
        self.assertEqual(len(selected), 1)
        self.assertEqual(self.ceiling_truth(), before)

    def test_capacity_refusal_after_dispatch_retains_started_and_resumes_by_observation_only(self):
        claimed = self.ready_cleanup()
        # Exhaust the actual existing prefix after the committed-start boundary,
        # without replacing its ledger or erasing any prior charges.
        def deplete(_context, request):
            accounting = _ACCOUNTING.get()
            self.assertIsNotNone(accounting)
            before = accounting.used
            accounting.used = before.plus(ConfigurationEvidenceFootprint(4096 - before.records, 0, 0, 0))
            self.assertEqual(accounting.used.value_octets, before.value_octets)
            self.assertEqual(accounting.used.statements, before.statements)
            return RuntimeEffectResult.succeeded(request.effect_id)

        adapter = RecordingRuntimeAdapter(deplete)
        coordinator = self.coordinator(self.unit_of_work, adapter, "cleanup-execution")
        command = self.execution_command(claimed, "cleanup-execution")
        with self.assertRaises(ExecutionCoordinatorConflict):
            coordinator.execute(command)
        self.assertEqual(len(adapter.runtime_calls), 1)
        request = adapter.runtime_calls[0][1]
        from control_plane_kit_core.operations import EffectAttemptIdentity
        identity = EffectAttemptIdentity(request.source.run_id, request.activity_id.value, 1)
        with self.unit_of_work() as uow:
            original = uow.stores.effect_attempt_intents.get(identity)
            retained = uow.stores.configuration_cleanup_ownership.get(identity)
            self.assertIs(retained.status, EffectAttemptStatus.STARTED)
            self.assertIsNone(retained.outcomes)
        observer = RecordingObserver(RuntimeEffectObservedIndeterminate(request.effect_id,
            original.request_fingerprint, RuntimeEffectObservationEvidence({"fixture": "unresolved-observation"}),
            RuntimeEffectObservationFailure("unresolved", "Observation does not establish member deletion")))
        fold = EffectAttemptFoldService(self.unit_of_work, id_factory=Sequence("cleanup-observed"))
        coordinator._reconciliation_service = EffectAttemptReconciliationService(self.unit_of_work, observer, fold)
        self.assertIs(coordinator.execute(command).status, CoordinatorStatus.UNCERTAIN)
        self.assertEqual(len(adapter.runtime_calls), 1)
        self.assertEqual(len(observer.calls), 1)
        with self.unit_of_work() as uow:
            retained = uow.stores.configuration_cleanup_ownership.get(identity)
            self.assertIs(retained.status, EffectAttemptStatus.UNCERTAIN)
            self.assertIsNone(retained.outcomes)
        self.assertEqual(self.connection.execute("SELECT count(*) FROM cpk_configuration_cleanup_member_outcomes").fetchone(), (0,))
        before = self.ceiling_truth()
        self.assertIs(coordinator.execute(command).status, CoordinatorStatus.UNCERTAIN)
        self.assertEqual(len(adapter.runtime_calls), 1)
        self.assertEqual(len(observer.calls), 1)
        self.assertEqual(self.ceiling_truth(), before)
