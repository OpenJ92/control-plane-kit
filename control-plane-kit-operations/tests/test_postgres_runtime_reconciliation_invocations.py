"""Fresh reconciliation targets use original prepared history and actual owners."""

import asyncio
import inspect
import unittest
from uuid import uuid4

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

    def observe(self, request, authority, *, invocations=None):
        self.requests.append((request, authority))
        self.invocations = invocations
        if invocations is not None:
            for grant in request.runtime_request.secret_resolution_grants:
                permit = invocations.admit("secret-resolution", authorization_id=grant.authorization_id)
                self._call(permit)
                invocations.record_outcome(permit, "returned")
            permit = invocations.admit("reconciliation-observation")
            self._call(permit)
        result = RuntimeEffectObservedIndeterminate(request.effect_id, request.request_fingerprint,
            RuntimeEffectObservationEvidence({"fixture": "controlled-observation"}),
            RuntimeEffectObservationFailure("fixture.indeterminate", "Controlled observation."))
        if invocations is not None:
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
        return bound, start, started, marker, service

    def composition_for_reconciliation(self, observer, service):
        fold = EffectAttemptFoldService(self.ledger, id_factory=lambda: uuid4().hex)
        registration = RuntimeAdapterRegistration(observer, invocation_profile="cpk.semantic-invocations.v1")
        # Target-only construction compatibility: accepted production keywords
        # do not exist yet. No owner behavior, result or admission is replaced.
        options = {}
        if "permission_policy" in inspect.signature(EffectAttemptReconciliationService).parameters:
            options = dict(permission_policy=self.policy(), invocation_service=service)
        reconciliation = EffectAttemptReconciliationService(self.ledger, registration, fold, **options)
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
