"""Fresh reconciliation targets use original prepared history and actual owners."""

import asyncio
from dataclasses import replace
import hashlib
import unittest
from uuid import uuid4

import rfc8785

import control_plane_kit_operations as operations
from control_plane_kit_core.runtime_effect_observation import (
    RuntimeEffectObservationEvidence, RuntimeEffectObservationFailure, RuntimeEffectObservedIndeterminate,
)
from control_plane_kit_core.types import RuntimeKind
from control_plane_kit_operations.coordinator import (
    ActivityExecutionDispatcher, ExecuteActivityRun, ExecuteManagedActivityRun,
    ExecutionCoordinator, RuntimeInterpreterDispatcher,
)
from control_plane_kit_operations.effect_attempt_fold_interpreter import EffectAttemptFoldService
from control_plane_kit_operations.effect_attempt_reconciliation import ReconcileEffectAttempt
from control_plane_kit_operations.effect_attempt_reconciliation_interpreter import EffectAttemptReconciliationService
from control_plane_kit_operations.effect_attempt_start_interpreter import EffectAttemptStartService
from control_plane_kit_operations.lifecycle import RunLifecycleCommandService
from control_plane_kit_operations.runtime_adapter_registration import RuntimeAdapterRegistration
from control_plane_kit_operations.secret_providers import SecretUseAuthorizationService
from control_plane_kit_operations.workflows import IdempotencyKey
from tests.test_managed_application_chain import now
from tests.test_postgres_runtime_invocation_carriage import _PreparedRuntimeCarriageFixture


class _ControlledReconciliationObserver:
    def __init__(self, fixture):
        self.fixture = fixture
        self.requests = []
        self.invocations = None
        self.committed = []
        self.after_preparation = None
        self.before_observation = None
        self.after_admission = None
        self.error = None
        self.classify = True
        self.swallow_control = False
        self.skip_observation = False
        self.transform_result = None
        self.control = None

    def observe(self, request, authority, *, invocations=None):
        self.requests.append((request, authority))
        self.invocations = invocations
        try:
            return self._observe(request, invocations)
        except operations.ExecutionInvocationControlError as error:
            self.control = error
            if not self.swallow_control:
                raise
            return self.result(request)

    @staticmethod
    def result(request):
        return RuntimeEffectObservedIndeterminate(request.effect_id, request.request_fingerprint,
            RuntimeEffectObservationEvidence({"fixture": "controlled-observation"}),
            RuntimeEffectObservationFailure("fixture.indeterminate", "Controlled observation."))

    def _observe(self, request, invocations):
        if invocations is not None:
            for grant in request.runtime_request.secret_resolution_grants:
                permit = invocations.admit("secret-resolution", authorization_id=grant.authorization_id)
                self._call(permit)
                invocations.record_outcome(permit, "returned")
                if self.after_preparation is not None:
                    self.after_preparation(invocations)
            if self.before_observation is not None:
                self.before_observation(invocations)
            if self.skip_observation:
                return self.result(request)
            permit = invocations.admit("reconciliation-observation")
            self._call(permit)
            if self.after_admission is not None:
                self.after_admission(invocations)
        if self.error is not None:
            raise self.error
        result = self.result(request)
        if self.transform_result is not None:
            result = self.transform_result(result)
        if invocations is not None and self.classify:
            invocations.record_outcome(permit, "returned")
        return result

    def _call(self, permit):
        self.fixture.assertEqual(self.fixture.ledger.active, 0)
        with self.fixture.unit_of_work() as uow:
            retained = uow.stores.execution.command_invocation(permit.record.command, permit.record.invocation_id)
        self.fixture.assertEqual(retained, permit.record)
        self.fixture.assertEqual((retained.disposition.value, retained.outcome.value), ("admitted", "pending"))
        self.committed.append(retained)


class PostgresRuntimeReconciliationInvocationTargets(_PreparedRuntimeCarriageFixture, unittest.TestCase):
    def unresolved_original(self):
        bound, start, started = self.seed_start()
        service = operations.ExecutionInvocationService(self.ledger, permission_policy=self.policy(), clock=now)
        port = service.bind_runtime(command_context=bound, start=start, started=started)
        permit = port.admit("prepared-runtime-dispatch")
        with self.unit_of_work() as uow:
            marker = uow.stores.execution.original_final_invocation(started.attempt.state.identity,
                original_event_id=started.attempt.original_start_event.event_id,
                request_fingerprint=started.attempt.state.request_fingerprint,
                kind=operations.ExecutionInvocationKind.PREPARED_RUNTIME_DISPATCH)
        self.assertEqual(marker, permit.record)
        self.assertEqual((marker.disposition.value, marker.outcome.value), ("admitted", "pending"))
        self.assertEqual(self.connection.execute("SELECT count(*) FROM cpk_effect_attempts").fetchone()[0], 1)
        self.seeded_attempt = started.attempt
        return bound, start, started, marker, service

    def composition_for_reconciliation(self, observer, service, *, reconciliation_invocations=None):
        fold = EffectAttemptFoldService(self.ledger, id_factory=lambda: uuid4().hex)
        registration = RuntimeAdapterRegistration(observer, invocation_profile="cpk.semantic-invocations.v1")
        reconciliation = EffectAttemptReconciliationService(self.ledger, registration, fold,
            permission_policy=self.policy(), invocation_service=reconciliation_invocations or service)
        dispatcher = RuntimeInterpreterDispatcher({RuntimeKind.DOCKER: RuntimeAdapterRegistration(self.adapter,
            invocation_profile="cpk.semantic-invocations.v1")}, SecretUseAuthorizationService(self.ledger))
        coordinator = ExecutionCoordinator(self.ledger,
            lifecycle=RunLifecycleCommandService(self.ledger, clock=now, id_factory=lambda: uuid4().hex),
            adapter=ActivityExecutionDispatcher(dispatcher),
            start_service=EffectAttemptStartService(self.ledger, permission_policy=self.policy(), id_factory=lambda: uuid4().hex),
            fold_service=fold, reconciliation_service=reconciliation, clock=now, id_factory=lambda: uuid4().hex,
            permission_policy=self.policy(), invocation_service=service)
        command = ExecuteManagedActivityRun(ExecuteActivityRun("run-a",
            self.authority(scopes=self.original.granted_scopes), self.runtime_fence,
            IdempotencyKey("fresh-prepared-reconciliation")), self.original)
        return coordinator, command

    def direct(self, observer, service, *, context=True, policy=True, invocation=True, profile=True):
        bound = self.binding(key="direct-reconciliation", generation=self.runtime_fence.generation,
            scopes=self.original.granted_scopes) if context else None
        command = ReconcileEffectAttempt("request-a", self.seeded_attempt.state.identity,
            self.authority(scopes=self.original.granted_scopes), self.runtime_fence)
        owner = EffectAttemptReconciliationService(self.ledger,
            RuntimeAdapterRegistration(observer, invocation_profile="cpk.semantic-invocations.v1" if profile else None),
            EffectAttemptFoldService(self.ledger, id_factory=lambda: uuid4().hex),
            permission_policy=self.policy() if policy else None, invocation_service=service if invocation else None)
        return owner, command, bound

    def fresh_port(self, service=None, *, generation=None, original_command=False):
        original, start, started, marker, original_service = self.unresolved_original()
        service = service or original_service
        generation = self.runtime_fence.generation if generation is None else generation
        context = original if original_command else self.binding(key="direct-reconciliation",
            generation=generation, scopes=self.original.granted_scopes)
        command = ReconcileEffectAttempt(start.request_id, started.attempt.state.identity, start.authority,
            replace(start.fence, generation=generation))
        with self.unit_of_work() as uow:
            intent = uow.stores.effect_attempt_intents.get(started.attempt.state.identity)
        port = service.bind_reconciliation(command_context=context, command=command, attempt=started.attempt,
            intent_record=intent, runtime_authority=self.runtime)
        return service, port, context, command, marker

    def assert_no_observed_fold(self):
        self.assertEqual(self.connection.execute("SELECT count(*) FROM cpk_effect_attempts").fetchone()[0], 1)
        self.assertEqual(self.connection.execute("SELECT count(*) FROM cpk_effect_attempt_outcomes").fetchone()[0], 0)
        self.assertEqual(self.adapter.requests, [])

    def observation_row(self, service, observer):
        return service.inspect_reconciliation_observation(observer.invocations)

    def test_new_command_carries_fresh_port_and_session_without_new_original_attempt(self):
        original, _, started, original_marker, service = self.unresolved_original()
        observer = _ControlledReconciliationObserver(self)
        coordinator, command = self.composition_for_reconciliation(observer, service)
        try:
            result = asyncio.run(coordinator.execute_managed(command))
        except operations.ExecutionInvocationExisting as error:
            self.fail("qualified fresh reconciliation remains stopped at original history: " + error.code)
        self.assertIsNotNone(observer.invocations, "fresh observer did not receive its bound invocation port")
        self.assertEqual(len(observer.requests), 1)
        request, authority = observer.requests[0]
        self.assertEqual(authority, self.runtime)
        with self.unit_of_work() as uow:
            receipt = uow.stores.execution.command_receipt_for_idempotency("run-a", command.execution.idempotency_key.value)
            reference = operations.ExecutionCommandReference("run-a", receipt.idempotency_key, receipt.intent_fingerprint)
            session = uow.stores.execution.get_request("request-a").identity.session_id
            current = uow.stores.execution.command_invocation_for_slot(reference, activity_id="start-runtime",
                kind=operations.ExecutionInvocationKind.RECONCILIATION_OBSERVATION)
            retained_original = uow.stores.execution.command_invocation(original.receipt, original_marker.invocation_id)
        self.assertNotEqual(reference, original.receipt)
        self.assertIsNotNone(session)
        self.assertEqual(len(request.runtime_request.secret_resolution_grants), 3)
        self.assertEqual({grant.session_id for grant in request.runtime_request.secret_resolution_grants}, {session})
        self.assertEqual(len(observer.committed), 4)
        self.assertTrue(all(row.command == reference for row in observer.committed))
        self.assertEqual(current.original_event_id, started.attempt.original_start_event.event_id)
        self.assertEqual((current.disposition.value, current.outcome.value), ("admitted", "returned"))
        self.assertEqual(retained_original, original_marker)
        self.assertEqual((result.status.value, result.effects_attempted), ("uncertain", 1))
        self.assertEqual(self.connection.execute("SELECT count(*) FROM cpk_effect_attempts").fetchone()[0], 1)
        self.assertEqual(self.adapter.requests, [])

    def test_known_prepared_history_cannot_use_contextless_legacy_observer(self):
        _, start, started, _, _ = self.unresolved_original()
        observer = _ControlledReconciliationObserver(self)
        fold = EffectAttemptFoldService(self.ledger, id_factory=lambda: uuid4().hex)
        legacy = EffectAttemptReconciliationService(self.ledger, observer, fold)
        command = ReconcileEffectAttempt(start.request_id, started.attempt.state.identity,
            start.authority, start.fence)
        control = None
        try:
            legacy.execute(command)
        except operations.ExecutionInvocationControlError as error:
            control = error
        self.assertIs(type(control), operations.ExecutionInvocationConflict,
            "known prepared history entered the contextless legacy observer")
        self.assertEqual(observer.requests, [])
        self.assertEqual(self.connection.execute("SELECT count(*) FROM cpk_secret_use_authorizations").fetchone()[0], 0)
        self.assertEqual(self.connection.execute("SELECT count(*) FROM cpk_effect_attempt_outcomes").fetchone()[0], 0)

    def test_missing_observer_profile_refuses_before_authorizations(self):
        *_, service = self.unresolved_original()
        observer = _ControlledReconciliationObserver(self)
        owner, command, context = self.direct(observer, service, profile=False)
        with self.assertRaises(operations.ExecutionInvocationConflict):
            owner.execute(command, command_context=context)
        self.assertEqual(observer.requests, [])
        self.assertEqual(self.connection.execute("SELECT count(*) FROM cpk_secret_use_authorizations").fetchone()[0], 0)
        self.assert_no_observed_fold()

    def test_dynamic_owner_without_current_context_cannot_fall_back(self):
        *_, service = self.unresolved_original()
        observer = _ControlledReconciliationObserver(self)
        owner, command, _ = self.direct(observer, service, context=False)
        with self.assertRaises(operations.ExecutionInvocationConflict):
            owner.execute(command)
        self.assertEqual(observer.requests, [])
        self.assert_no_observed_fold()

    def test_current_context_without_dynamic_owners_cannot_fall_back(self):
        *_, service = self.unresolved_original()
        observer = _ControlledReconciliationObserver(self)
        owner, command, context = self.direct(observer, service, policy=False, invocation=False)
        with self.assertRaises(operations.ExecutionInvocationConflict):
            owner.execute(command, command_context=context)
        self.assertEqual(observer.requests, [])
        self.assert_no_observed_fold()

    def test_partial_dynamic_composition_and_missing_marker_cannot_fall_back(self):
        *_, service = self.unresolved_original()
        observer = _ControlledReconciliationObserver(self)
        owner, command, context = self.direct(observer, service, invocation=False)
        self.connection.execute("DELETE FROM cpk_execution_command_invocations")
        with self.assertRaises(operations.ExecutionInvocationConflict):
            owner.execute(command, command_context=context)
        self.assertEqual(observer.requests, [])
        self.assertEqual(self.connection.execute("SELECT count(*) FROM cpk_secret_use_authorizations").fetchone()[0], 0)
        self.assert_no_observed_fold()

    def test_original_command_reference_cannot_bind_new_observation(self):
        with self.assertRaises(operations.ExecutionInvocationConflict):
            self.fresh_port(original_command=True)
        self.assertEqual(self.connection.execute("SELECT count(*) FROM cpk_execution_command_invocations").fetchone()[0], 1)
        self.assert_no_observed_fold()

    def test_newer_current_claim_can_admit_observation_of_the_original_attempt(self):
        generation = self.runtime_fence.generation + 1
        service, port, _, _, original = self.fresh_port(generation=generation)
        self.replace_claim(worker_id=self.runtime_fence.worker_id, generation=generation)
        permit = port.admit("reconciliation-observation")
        self.assertEqual(permit.record.original_event_id, original.original_event_id)
        self.assertEqual(permit.record.attempt, original.attempt)
        port.record_outcome(permit, "returned")
        self.assertEqual(service.inspect_reconciliation_observation(port).outcome.value, "returned")
        self.assert_no_observed_fold()

    def test_observation_target_has_exact_public_original_ownership(self):
        service, port, context, _, original = self.fresh_port()
        self.assertIsNone(service.inspect_reconciliation_observation(port))
        permit = port.admit("reconciliation-observation")
        expected = dict(domain="cpk.prepared-runtime-reconciliation-target.v1", kind="reconciliation-observation",
            original_invocation_id=original.invocation_id, original_target_fingerprint=original.target_fingerprint,
            original_request_fingerprint=original.request_fingerprint, runtime_registration_id=original.runtime_registration_id)
        self.assertEqual(permit.record.target_fingerprint, hashlib.sha256(rfc8785.dumps(expected)).hexdigest())
        self.assertEqual(permit.record.command, context.receipt)
        self.assertNotEqual(permit.record.command, original.command)

    def test_revocation_between_preparations_denies_without_observation_or_fold(self):
        *_, service = self.unresolved_original()
        observer = _ControlledReconciliationObserver(self)
        observer.after_preparation = lambda port: self.revoke_operate()
        coordinator, command = self.composition_for_reconciliation(observer, service)
        result = asyncio.run(coordinator.execute_managed(command))
        self.assertEqual((result.status.value, result.effects_attempted), ("authorization-denied", 1))
        self.assertEqual(len(observer.committed), 1)
        self.assertIsNone(self.observation_row(service, observer))
        self.assert_no_observed_fold()

    def test_swallowed_final_denial_cannot_fold_an_observation(self):
        *_, service = self.unresolved_original()
        observer = _ControlledReconciliationObserver(self)
        observer.before_observation = lambda port: self.revoke_operate()
        observer.swallow_control = True
        coordinator, command = self.composition_for_reconciliation(observer, service)
        result = asyncio.run(coordinator.execute_managed(command))
        self.assertEqual(result.status.value, "authorization-denied")
        self.assertEqual(len(observer.committed), 3)
        self.assertEqual(self.observation_row(service, observer).disposition.value, "denied")
        self.assert_no_observed_fold()

    def test_missing_final_cannot_fold_a_held_observation(self):
        *_, service = self.unresolved_original()
        observer = _ControlledReconciliationObserver(self)
        observer.skip_observation = True
        coordinator, command = self.composition_for_reconciliation(observer, service)
        with self.assertRaises(operations.ExecutionInvocationConflict):
            asyncio.run(coordinator.execute_managed(command))
        self.assertEqual(len(observer.committed), 3)
        self.assertIsNone(self.observation_row(service, observer))
        self.assert_no_observed_fold()

    def test_pending_final_cannot_fold_a_held_observation(self):
        *_, service = self.unresolved_original()
        observer = _ControlledReconciliationObserver(self)
        observer.classify = False
        coordinator, command = self.composition_for_reconciliation(observer, service)
        with self.assertRaises(operations.ExecutionInvocationConflict):
            asyncio.run(coordinator.execute_managed(command))
        self.assertEqual(self.observation_row(service, observer).outcome.value, "pending")
        self.assert_no_observed_fold()

    def test_membership_revocation_after_observation_admission_allows_classification_and_fold(self):
        *_, service = self.unresolved_original()
        observer = _ControlledReconciliationObserver(self)
        observer.after_admission = lambda port: self.revoke_operate()
        coordinator, command = self.composition_for_reconciliation(observer, service)
        result = asyncio.run(coordinator.execute_managed(command))
        self.assertEqual(result.status.value, "uncertain")
        self.assertEqual(self.observation_row(service, observer).outcome.value, "returned")
        self.assertEqual(self.connection.execute("SELECT count(*) FROM cpk_effect_attempt_outcomes").fetchone()[0], 1)
        self.assertEqual(self.connection.execute("SELECT count(*) FROM cpk_effect_attempts").fetchone()[0], 1)

    def test_runtime_revocation_after_admission_retains_classification_but_blocks_guarded_fold(self):
        *_, service = self.unresolved_original()
        observer = _ControlledReconciliationObserver(self)
        def revoke(port):
            with self.unit_of_work() as uow:
                uow.stores.runtime_authorities.revoke("workspace-a", self.reference)
                uow.commit()
        observer.after_admission = revoke
        owner, command, context = self.direct(observer, service)
        with self.assertRaises(operations.EffectAttemptReconciliationDenied):
            owner.execute(command, command_context=context)
        self.assertEqual(self.observation_row(service, observer).outcome.value, "returned")
        self.assert_no_observed_fold()

    def test_lease_expiry_after_admission_retains_classification_but_blocks_guarded_fold(self):
        *_, service = self.unresolved_original()
        observer = _ControlledReconciliationObserver(self)
        observer.after_admission = lambda port: self.expire_claim()
        owner, command, context = self.direct(observer, service)
        with self.assertRaises(operations.EffectAttemptReconciliationDenied):
            owner.execute(command, command_context=context)
        self.assertEqual(self.observation_row(service, observer).outcome.value, "returned")
        self.assert_no_observed_fold()

    def test_invalid_observation_remains_unfolded_despite_classified_invocation(self):
        *_, service = self.unresolved_original()
        observer = _ControlledReconciliationObserver(self)
        observer.transform_result = lambda result: replace(result, effect_id="foreign-effect")
        owner, command, context = self.direct(observer, service)
        with self.assertRaises(operations.EffectAttemptReconciliationConflict):
            owner.execute(command, command_context=context)
        self.assertEqual(self.observation_row(service, observer).outcome.value, "returned")
        self.assert_no_observed_fold()

    def test_ordinary_observer_exception_preserves_identity_and_no_fabricated_observation(self):
        *_, service = self.unresolved_original()
        observer = _ControlledReconciliationObserver(self)
        error = RuntimeError("controlled observer fault")
        observer.error = error
        owner, command, context = self.direct(observer, service)
        with self.assertRaises(RuntimeError) as caught:
            owner.execute(command, command_context=context)
        self.assertIs(caught.exception, error)
        self.assertEqual(self.observation_row(service, observer).outcome.value, "pending")
        self.assert_no_observed_fold()

    def test_observer_cancellation_preserves_identity_and_no_fabricated_observation(self):
        *_, service = self.unresolved_original()
        observer = _ControlledReconciliationObserver(self)
        error = asyncio.CancelledError("controlled observer cancellation")
        observer.error = error
        owner, command, context = self.direct(observer, service)
        with self.assertRaises(asyncio.CancelledError) as caught:
            owner.execute(command, command_context=context)
        self.assertIs(caught.exception, error)
        self.assertEqual(self.observation_row(service, observer).outcome.value, "pending")
        self.assert_no_observed_fold()

    def test_duplicate_observation_admission_control_is_not_normalized_or_folded(self):
        *_, service = self.unresolved_original()
        observer = _ControlledReconciliationObserver(self)
        observer.after_admission = lambda port: port.admit("reconciliation-observation")
        coordinator, command = self.composition_for_reconciliation(observer, service)
        with self.assertRaises(operations.ExecutionInvocationExisting) as caught:
            asyncio.run(coordinator.execute_managed(command))
        self.assertIs(caught.exception, observer.control)
        self.assertEqual(self.observation_row(service, observer).outcome.value, "pending")
        self.assert_no_observed_fold()

    def test_lost_observation_commit_ack_gives_no_permit(self):
        from control_plane_kit_operations.postgres import PostgresUnitOfWork
        from tests.test_postgres_health_effect_start_rollback import _CommitThenRaiseConnection
        armed, connections = [], []
        def connect():
            connection = self.connect()
            if armed:
                connection = _CommitThenRaiseConnection(connection, RuntimeError("observation commit ack lost"))
                connections.append(connection)
            return connection
        service = operations.ExecutionInvocationService(lambda: PostgresUnitOfWork(connect),
            permission_policy=self.policy(), clock=now)
        _, port, context, _, _ = self.fresh_port(service)
        armed.append(True)
        with self.assertRaises(operations.ExecutionInvocationConflict):
            port.admit("reconciliation-observation")
        with self.unit_of_work() as uow:
            current = uow.stores.execution.command_invocation_for_slot(context.receipt,
                activity_id="start-runtime", kind=operations.ExecutionInvocationKind.RECONCILIATION_OBSERVATION)
        self.assertEqual((current.disposition.value, current.outcome.value), ("admitted", "pending"))
        self.assertEqual([connection.commits for connection in connections], [1])
        self.assert_no_observed_fold()

    def permission_race(self, *, revoke_first):
        service, port, context, command, _ = self.fresh_port()
        with self.unit_of_work() as uow:
            intent = uow.stores.effect_attempt_intents.get(command.identity)
        def admit(factory):
            owner = operations.ExecutionInvocationService(factory, permission_policy=self.policy(), clock=now)
            bound = owner.bind_reconciliation(command_context=context, command=command,
                attempt=self.seeded_attempt, intent_record=intent, runtime_authority=self.runtime)
            return bound, bound.admit("reconciliation-observation")
        def revoke(factory):
            return self.permissions(factory).revoke(operations.RevokeWorkspacePermissions(self.admin_context,
                "workspace-a", self.worker, (self.operate,), 2, IdempotencyKey("race-revoke")))
        first, second = self.opposing_commands(revoke if revoke_first else admit,
            admit if revoke_first else revoke, self.permission_lock, connection_query=self.permission_lock)
        if revoke_first:
            first.result()
            with self.assertRaises(operations.ExecutionInvocationDenied):
                second.result()
            self.assertEqual(service.inspect_reconciliation_observation(port).disposition.value, "denied")
        else:
            bound, permit = first.result()
            second.result()
            bound.record_outcome(permit, "returned")
            self.assertEqual(service.inspect_reconciliation_observation(port).outcome.value, "returned")
        self.assert_no_observed_fold()

    def test_permission_revoke_wins_real_observation_admission_lock(self):
        self.permission_race(revoke_first=True)

    def test_observation_admission_wins_revoke_and_retains_classification(self):
        self.permission_race(revoke_first=False)

    def assert_persisted_observation_refused(self, context):
        from control_plane_kit_operations.postgres.current_data_validation import CurrentRowDrift
        from control_plane_kit_operations.postgres.execution import validate_execution_invocation_rows
        with self.assertRaises(operations.ExecutionInvocationConflict), self.unit_of_work() as uow:
            uow.stores.execution.command_invocation_for_slot(context.receipt, activity_id="start-runtime",
                kind=operations.ExecutionInvocationKind.RECONCILIATION_OBSERVATION)
        with self.assertRaises(CurrentRowDrift):
            validate_execution_invocation_rows(self.connection)
        self.assert_no_observed_fold()

    def test_persisted_target_drift_is_rejected_by_storage_and_current_data_validation(self):
        from psycopg.types.json import Jsonb
        _, port, context, _, _ = self.fresh_port()
        permit = port.admit("reconciliation-observation")
        forged = replace(permit.record, target_fingerprint="a" * 64)
        self.connection.execute("UPDATE cpk_execution_command_invocations SET invocation_id=%s, "
            "target_fingerprint=%s, descriptor=%s WHERE invocation_id=%s",
            (forged.invocation_id, forged.target_fingerprint, Jsonb(forged.descriptor()), permit.record.invocation_id))
        self.assert_persisted_observation_refused(context)

    def test_missing_original_is_rejected_by_storage_and_current_data_validation(self):
        _, port, context, _, original = self.fresh_port()
        port.admit("reconciliation-observation")
        self.connection.execute("DELETE FROM cpk_execution_command_invocations WHERE invocation_id=%s", (original.invocation_id,))
        self.assert_persisted_observation_refused(context)

    def test_duplicate_original_is_rejected_by_storage_and_current_data_validation(self):
        _, port, context, _, original = self.fresh_port()
        port.admit("reconciliation-observation")
        with self.unit_of_work() as uow:
            uow.stores.execution.add_command_invocation(replace(original, command=context.receipt))
            uow.commit()
        self.assert_persisted_observation_refused(context)

    def test_coordinator_requires_the_same_invocation_owner_instance(self):
        *_, service = self.unresolved_original()
        observer = _ControlledReconciliationObserver(self)
        other = operations.ExecutionInvocationService(self.ledger, permission_policy=self.policy(), clock=now)
        coordinator, command = self.composition_for_reconciliation(observer, service, reconciliation_invocations=other)
        with self.assertRaises(operations.ExecutionInvocationConflict):
            asyncio.run(coordinator.execute_managed(command))
        self.assertEqual(observer.requests, [])
        self.assertEqual(self.connection.execute("SELECT count(*) FROM cpk_secret_use_authorizations").fetchone()[0], 0)
        self.assert_no_observed_fold()

    def test_sessionless_and_foreign_session_grants_cannot_be_adopted(self):
        from control_plane_kit_core.runtime_effect_observation import runtime_effect_request_for_intent
        from control_plane_kit_operations.runtime_secret_uses import required_secret_uses_for_runtime_effect, secret_authorization_command
        from control_plane_kit_operations.secret_providers import authorize_runtime_secret_command
        service, port, _, command, original = self.fresh_port()
        with self.unit_of_work() as uow:
            intent = uow.stores.effect_attempt_intents.get(command.identity).intent
        request = runtime_effect_request_for_intent(intent, effect_id=original.original_event_id)
        use = required_secret_uses_for_runtime_effect(request, self.runtime)[0]
        for session in (None, "foreign-session"):
            authorization = secret_authorization_command(request, use,
                actor_subject=self.original.actor_id, requested_at=now(),
                actor_scopes=self.original.granted_scopes, session_id=session)
            grant = authorize_runtime_secret_command(SecretUseAuthorizationService(self.ledger), authorization)
            with self.subTest(session=session), self.assertRaises(operations.ExecutionInvocationConflict):
                port.admit("secret-resolution", authorization_id=grant.authorization_id)
        self.assertIsNone(service.inspect_reconciliation_observation(port))
        self.assert_no_observed_fold()

    def test_retained_terminal_fold_does_not_require_fresh_profile_or_membership(self):
        *_, service = self.unresolved_original()
        observer = _ControlledReconciliationObserver(self)
        coordinator, command = self.composition_for_reconciliation(observer, service)
        asyncio.run(coordinator.execute_managed(command))
        self.revoke_operate()
        owner = EffectAttemptReconciliationService(self.ledger, object(), object(), permission_policy=self.policy())
        result = owner.execute(ReconcileEffectAttempt("request-a", self.seeded_attempt.state.identity,
            command.execution.authority, command.execution.fence))
        self.assertIs(type(result), operations.ExistingFold)
        self.assertEqual(result.attempt.state.status.value, "uncertain")
        self.assertEqual(len(observer.requests), 1)

    def test_replacement_runtime_cannot_substitute_for_the_original_observation_target(self):
        *_, service = self.unresolved_original()
        replacement = self.change_runtime(replace(self.runtime.authority,
            endpoint="tcp://replacement.example.test:2376"))
        observer = _ControlledReconciliationObserver(self)
        owner, command, context = self.direct(observer, service)
        with self.assertRaises(operations.ExecutionInvocationConflict):
            owner.execute(command, command_context=context)
        self.assertEqual(observer.committed, [])
        self.assertNotEqual(replacement.registration_id, self.runtime.registration_id)
        self.assertEqual(observer.requests[0][1].registration_id, self.runtime.registration_id)
        self.assert_no_observed_fold()
