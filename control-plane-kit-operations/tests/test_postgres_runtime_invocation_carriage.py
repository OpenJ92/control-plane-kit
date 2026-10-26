"""Ordinary runtime carriage targets compose real admission and history owners."""

import asyncio
from dataclasses import replace
import unittest
from unittest import mock
from uuid import uuid4

import control_plane_kit_operations as operations
from control_plane_kit_core.identity import AuthenticatedPrincipal
from control_plane_kit_core.operations import EffectAttemptIdentity, RunId
from control_plane_kit_core.planning import ActivityDependency, ActivityId, ActivityPlan, PlannedActivity, RuntimeTarget, StartRuntime
from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_core.runtime_authority import RuntimeAuthorityReference
from control_plane_kit_core.runtime_effects import RuntimeEffectFailure, RuntimeEffectResult
from control_plane_kit_core.secrets import SecretProviderEndpointReference, SecretProviderId, SecretReference, SecretUseIntent
from control_plane_kit_core.types import RuntimeKind
from control_plane_kit_core.topology import DeploymentGraph, RuntimeRecord
from control_plane_kit_operations.coordinator import (
    ActivityExecutionDispatcher, ExecuteActivityRun, ExecuteManagedActivityRun,
    ExecutionCoordinator, RuntimeInterpreterDispatcher,
)
from control_plane_kit_operations.effect_attempt_fold_interpreter import EffectAttemptFoldService
from control_plane_kit_operations.effect_attempt_reconciliation_interpreter import EffectAttemptReconciliationService
from control_plane_kit_operations.effect_attempt_start_interpreter import EffectAttemptStartService
from control_plane_kit_operations.lifecycle import RunLifecycleCommandService
from control_plane_kit_operations.runtime_adapter_registration import RuntimeAdapterRegistration
from control_plane_kit_operations.secret_providers import (
    RegisterSecretProviderCommand, RegisterSecretReferenceCommand, SecretProviderKind,
    SecretProviderRegistrationService, SecretUseAuthorizationService,
)
from control_plane_kit_operations.workflows import IdempotencyKey
from tests.postgres_effect_attempt_reconciliation_fixture import FailIfObserver, UnitOfWorkLedger
from tests.test_managed_application_chain import now
from tests.test_postgres_prepared_runtime_invocations import _PreparedRuntimeInvocationFixture


class _ControlledPreparedRuntime:
    def __init__(self, fixture):
        self.fixture = fixture
        self.invocations = None
        self.requests = []
        self.authorities = []
        self.committed = []
        self.active_transactions = []
        self.calls = []
        self.local_calls = []
        self.after_preparation = None
        self.before_final = None
        self.after_final_admission = None
        self.final_error = None
        self.classify_final = True
        self.transform_result = None
        self.swallow_control = False
        self.control = None

    def execute(self, request):
        self.local_calls.append(request.effect_id)
        return RuntimeEffectResult.succeeded(request.effect_id)

    def execute_with_authority(self, request, authority, *, invocations=None):
        self.requests.append(request)
        self.authorities.append(authority)
        self.invocations = invocations
        try:
            return self._execute(request, invocations)
        except operations.ExecutionInvocationControlError as error:
            self.control = error
            if self.swallow_control:
                return RuntimeEffectResult.succeeded(request.effect_id)
            raise

    def _execute(self, request, invocations):
        if invocations is not None:
            for grant in request.secret_resolution_grants:
                permit = invocations.admit("secret-resolution", authorization_id=grant.authorization_id)
                self._call(permit, grant.authorization_id)
                invocations.record_outcome(permit, "returned")
                if self.after_preparation is not None:
                    self.after_preparation(invocations)
            if self.before_final is not None:
                self.before_final(invocations)
            permit = invocations.admit("prepared-runtime-dispatch")
            if self.after_final_admission is not None:
                self.after_final_admission(invocations)
            self._call(permit, request.effect_id)
        # A held, correlated result stops the fixture's run without claiming a deployment.
        try:
            if self.final_error is not None:
                raise self.final_error
            result = RuntimeEffectResult.failed(request.effect_id,
                RuntimeEffectFailure("fixture.stopped", "Controlled runtime result."))
            if self.transform_result is not None:
                result = self.transform_result(result)
        except operations.ExecutionInvocationControlError:
            raise
        except Exception:
            if invocations is not None and self.classify_final:
                invocations.record_outcome(permit, "unknown")
            raise
        if invocations is not None and self.classify_final:
            invocations.record_outcome(permit, "returned")
        return result

    def _call(self, permit, correlation):
        self.active_transactions.append(self.fixture.ledger.active)
        self.fixture.assertEqual(self.fixture.ledger.active, 0)
        with self.fixture.unit_of_work() as uow:
            retained = uow.stores.execution.command_invocation(permit.record.command, permit.record.invocation_id)
        self.fixture.assertEqual(retained, permit.record)
        self.fixture.assertEqual((retained.disposition.value, retained.outcome.value), ("admitted", "pending"))
        self.committed.append(retained)
        self.calls.append(correlation)


class _PreparedRuntimeCarriageFixture(_PreparedRuntimeInvocationFixture):
    def setUp(self):
        super().setUp()
        self.permissions().grant(operations.GrantWorkspacePermissions(self.admin_context, "workspace-a",
            self.worker, (PolicyScope.SECRET_PROVIDER_USE,), 1, IdempotencyKey("grant-runtime-secrets")))
        self.original = operations.WorkspaceContextResolver(self.unit_of_work,
            installation_id="installation-a").resolve(AuthenticatedPrincipal(self.worker), "workspace-a")
        self.ledger = UnitOfWorkLedger(self.unit_of_work)
        self.adapter = _ControlledPreparedRuntime(self)
        self.observer = FailIfObserver()
        roles = ((self.runtime.authority.ca_certificate, SecretUseIntent.DOCKER_REMOTE_TLS_CA_CERTIFICATE),
            (self.runtime.authority.client_certificate, SecretUseIntent.DOCKER_REMOTE_TLS_CLIENT_CERTIFICATE),
            (self.runtime.authority.client_key, SecretUseIntent.DOCKER_REMOTE_TLS_CLIENT_KEY))
        registrations = SecretProviderRegistrationService(self.unit_of_work)
        provider = registrations.register_provider(RegisterSecretProviderCommand(
            workspace_id="workspace-a", provider_id=SecretProviderId("runtime"),
            provider_kind=SecretProviderKind.CONTROL_PLANE_KIT_SECRETS, display_name="Runtime carriage fixture",
            endpoint_reference=SecretProviderEndpointReference("runtime-provider"),
            credential_reference=SecretReference("secret://bootstrap/runtime/token"),
            allowed_reference_prefixes=tuple(reference for reference, _ in roles),
            allowed_intents=tuple(intent for _, intent in roles), admitted_by="operator-a", admitted_at=now(),
            actor_scopes=(PolicyScope.SECRET_PROVIDER_REGISTER,)))
        for reference, intent in roles:
            registrations.register_reference(RegisterSecretReferenceCommand(workspace_id="workspace-a",
                reference=reference, provider_registration_id=provider.registration_id,
                allowed_intents=(intent,), admitted_by="operator-a", admitted_at=now(),
                actor_scopes=(PolicyScope.SECRET_PROVIDER_REGISTER,)))

    def composition(self, *, profile=True, service=True, wrapped=True, outer_wrapper=None):
        registration = RuntimeAdapterRegistration(self.adapter,
            invocation_profile="cpk.semantic-invocations.v1" if profile else None)
        dispatcher = RuntimeInterpreterDispatcher({RuntimeKind.DOCKER: registration},
            SecretUseAuthorizationService(self.ledger))
        adapter = ActivityExecutionDispatcher(dispatcher) if wrapped else dispatcher
        if outer_wrapper is not None:
            adapter = outer_wrapper(adapter)
        fold = EffectAttemptFoldService(self.ledger, id_factory=lambda: uuid4().hex)
        invocations = operations.ExecutionInvocationService(self.ledger,
            permission_policy=self.policy(), clock=now) if service else None
        owner = ExecutionCoordinator(self.ledger,
            lifecycle=RunLifecycleCommandService(self.ledger, clock=now, id_factory=lambda: uuid4().hex),
            adapter=adapter, start_service=EffectAttemptStartService(self.ledger,
                permission_policy=self.policy(), id_factory=lambda: uuid4().hex),
            fold_service=fold,
            reconciliation_service=EffectAttemptReconciliationService(self.ledger, self.observer, fold),
            clock=now, id_factory=lambda: uuid4().hex, permission_policy=self.policy(), invocation_service=invocations)
        command = ExecuteManagedActivityRun(ExecuteActivityRun("run-a",
            self.authority(scopes=self.original.granted_scopes), self.runtime_fence,
            IdempotencyKey("prepared-runtime-carriage")), self.original)
        return owner, command

    def revoke_operate(self):
        self.permissions().revoke(operations.RevokeWorkspacePermissions(self.admin_context,
            "workspace-a", self.worker, (PolicyScope.EXECUTION_OPERATE,), 2, IdempotencyKey("revoke-runtime")))

    def change_runtime(self, authority):
        with self.unit_of_work() as uow:
            uow.stores.runtime_authorities.revoke("workspace-a", self.reference)
            value = uow.stores.runtime_authorities.register(workspace_id="workspace-a", authority_ref=self.reference,
                runtime_kind=RuntimeKind.DOCKER, authority=authority, admitted_by="operator-a", admitted_at=now())
            uow.commit()
        return value

    def seed_start(self):
        bound = self.binding(generation=self.runtime_fence.generation, scopes=self.original.granted_scopes)
        start = self.start_command(intent=replace(self.intent(), authority_ref=self.reference),
            fence=self.runtime_fence, authority=self.authority(scopes=self.original.granted_scopes))
        started = self.fresh_start().execute(start, command_context=bound)
        return bound, start, started

    def assert_unfolded(self, command):
        marker, attempt, _ = self.actual_final(command)
        self.assertEqual(attempt.state.status.value, "started")
        self.assertEqual(self.connection.execute("SELECT count(*) FROM cpk_effect_attempt_outcomes").fetchone()[0], 0)
        self.assertEqual(self.observer.calls, [])
        return marker

    def assert_no_preparation(self):
        self.assertEqual(self.adapter.requests, [])
        self.assertEqual(self.connection.execute("SELECT count(*) FROM cpk_secret_use_authorizations").fetchone()[0], 0)
        self.assertEqual(self.connection.execute("SELECT count(*) FROM cpk_effect_attempt_outcomes").fetchone()[0], 0)
        self.assertEqual(self.observer.calls, [])

    def actual_final(self, command):
        with self.unit_of_work() as uow:
            receipt = uow.stores.execution.command_receipt_for_idempotency("run-a", command.execution.idempotency_key.value)
            reference = operations.ExecutionCommandReference("run-a", receipt.idempotency_key, receipt.intent_fingerprint)
            attempt = uow.stores.effect_attempts.get(EffectAttemptIdentity(RunId("run-a"), "start-runtime", 1))
            marker = uow.stores.execution.command_invocation_for_slot(reference, activity_id="start-runtime",
                kind=operations.ExecutionInvocationKind.PREPARED_RUNTIME_DISPATCH)
            session = uow.stores.execution.get_request("request-a").identity.session_id
        self.assertIsNotNone(marker)
        self.assertEqual(marker.original_event_id, attempt.original_start_event.event_id)
        self.assertEqual(marker.request_fingerprint, attempt.state.request_fingerprint)
        self.assertEqual(marker.runtime_registration_id, self.runtime.registration_id)
        return marker, attempt, session


class PostgresRuntimeInvocationCarriageTests(_PreparedRuntimeCarriageFixture, unittest.TestCase):
    def assert_carriage(self, *, wrapped):
        owner, command = self.composition(wrapped=wrapped)
        ports = []
        runtime_dispatch = RuntimeInterpreterDispatcher.execute_runtime
        activity_dispatch = ActivityExecutionDispatcher.execute_runtime

        def runtime_call(dispatcher, context, request, *, invocations=None):
            ports.append(invocations)
            return runtime_dispatch(dispatcher, context, request, invocations=invocations)

        def activity_call(dispatcher, context, request, *, invocations=None):
            ports.append(invocations)
            return activity_dispatch(dispatcher, context, request, invocations=invocations)

        with mock.patch.object(RuntimeInterpreterDispatcher, "execute_runtime", runtime_call), \
                mock.patch.object(ActivityExecutionDispatcher, "execute_runtime", activity_call):
            result = asyncio.run(owner.execute_managed(command))
        marker, attempt, session = self.actual_final(command)
        self.assertIsNotNone(self.adapter.invocations,
            "prepared coordinator did not carry its original bound invocation port")
        self.assertEqual(len(ports), 2 if wrapped else 1)
        for port in ports:
            self.assertIs(port, self.adapter.invocations)
        self.assertEqual(len(self.adapter.requests), 1)
        grants = self.adapter.requests[0].secret_resolution_grants
        self.assertEqual(len(grants), 3)
        self.assertIsNotNone(session)
        self.assertEqual({grant.session_id for grant in grants}, {session})
        self.assertEqual(self.adapter.authorities, [self.runtime])
        self.assertEqual(len(self.adapter.calls), 4)
        self.assertEqual(self.adapter.active_transactions, [0] * 4)
        self.assertEqual([(row.disposition.value, row.outcome.value) for row in self.adapter.committed],
            [("admitted", "pending")] * 4)
        self.assertEqual(marker.outcome.value, "returned")
        self.assertEqual(attempt.state.status.value, "failed")
        self.assertEqual(result.effects_attempted, 1)
        self.assertEqual(self.observer.calls, [])

    def test_real_coordinator_carries_original_port_and_exact_session_to_remote_arm(self):
        self.assert_carriage(wrapped=True)

    def test_direct_runtime_dispatcher_carries_the_same_original_port(self):
        self.assert_carriage(wrapped=False)

    def test_missing_profile_refuses_before_start_or_secret_authorization(self):
        owner, command = self.composition(profile=False)
        before = self.attempt_snapshot()
        result = asyncio.run(owner.execute_managed(command))
        self.assertEqual(result.effects_attempted, 0,
            "dynamic prepared dispatch did not refuse the missing profile before start")
        self.assertEqual(result.status.value, "unsupported")
        self.assertEqual(self.attempt_snapshot(), before)
        self.assertEqual(self.adapter.requests, [])
        self.assertEqual(self.observer.calls, [])
        self.assertEqual(self.connection.execute("SELECT count(*) FROM cpk_execution_command_invocations").fetchone()[0], 0)
        self.assertEqual(self.connection.execute("SELECT count(*) FROM cpk_secret_use_authorizations").fetchone()[0], 0)

    def test_missing_service_refuses_before_start(self):
        owner, command = self.composition(service=False)
        before = self.attempt_snapshot()
        result = asyncio.run(owner.execute_managed(command))
        self.assertEqual((result.status.value, result.effects_attempted), ("unsupported", 0))
        self.assertEqual(self.attempt_snapshot(), before)
        self.assert_no_preparation()

    def test_missing_authority_arm_refuses_before_start(self):
        self.adapter.execute_with_authority = None
        owner, command = self.composition()
        before = self.attempt_snapshot()
        result = asyncio.run(owner.execute_managed(command))
        self.assertEqual((result.status.value, result.effects_attempted), ("unsupported", 0))
        self.assertEqual(self.attempt_snapshot(), before)
        self.assert_no_preparation()

    def test_revocation_between_preparations_is_denial_without_final_or_fold(self):
        owner, command = self.composition()
        self.adapter.after_preparation = lambda port: self.revoke_operate()
        result = asyncio.run(owner.execute_managed(command))
        self.assertEqual((result.status.value, result.effects_attempted), ("authorization-denied", 1))
        self.assertEqual(len(self.adapter.calls), 1)
        self.assertEqual(self.assert_unfolded(command).disposition.value, "planned")

    def test_swallowed_preparation_denial_cannot_fabricate_success(self):
        owner, command = self.composition()
        self.adapter.after_preparation = lambda port: self.revoke_operate()
        self.adapter.swallow_control = True
        with self.assertRaises(operations.ExecutionInvocationPlanned):
            asyncio.run(owner.execute_managed(command))
        self.assertEqual(len(self.adapter.calls), 1)
        self.assertEqual(self.assert_unfolded(command).disposition.value, "planned")

    def test_swallowed_final_denial_is_still_denied_without_fold(self):
        owner, command = self.composition()
        self.adapter.before_final = lambda port: self.revoke_operate()
        self.adapter.swallow_control = True
        result = asyncio.run(owner.execute_managed(command))
        self.assertEqual((result.status.value, result.effects_attempted), ("authorization-denied", 1))
        self.assertEqual(len(self.adapter.calls), 3)
        self.assertEqual(self.assert_unfolded(command).disposition.value, "denied")

    def test_existing_control_escapes_both_dispatch_layers_without_normalization(self):
        owner, command = self.composition()
        self.adapter.after_final_admission = lambda port: port.admit("prepared-runtime-dispatch")
        with self.assertRaises(operations.ExecutionInvocationExisting) as caught:
            asyncio.run(owner.execute_managed(command))
        self.assertIs(caught.exception, self.adapter.control)
        self.assertEqual(len(self.adapter.calls), 3)
        self.assertEqual(self.assert_unfolded(command).outcome.value, "pending")

    def test_unclassified_final_cannot_fold_a_held_result(self):
        owner, command = self.composition()
        self.adapter.classify_final = False
        with self.assertRaises(operations.ExecutionInvocationConflict):
            asyncio.run(owner.execute_managed(command))
        self.assertEqual(len(self.adapter.calls), 4)
        self.assertEqual(self.assert_unfolded(command).outcome.value, "pending")

    def test_held_result_folds_after_revocation_of_permission_and_runtime(self):
        owner, command = self.composition()
        def revoke(port):
            self.revoke_operate()
            with self.unit_of_work() as uow:
                uow.stores.runtime_authorities.revoke("workspace-a", self.reference)
                uow.commit()
        self.adapter.after_final_admission = revoke
        result = asyncio.run(owner.execute_managed(command))
        marker, attempt, _ = self.actual_final(command)
        self.assertEqual(result.status.value, "failed")
        self.assertEqual(attempt.state.status.value, "failed")
        self.assertEqual(marker.outcome.value, "returned")
        self.assertEqual(len(self.adapter.calls), 4)

    def test_wrong_effect_id_result_is_uncertain_despite_classified_final(self):
        owner, command = self.composition()
        self.adapter.transform_result = lambda result: RuntimeEffectResult.succeeded("unrelated-effect")
        result = asyncio.run(owner.execute_managed(command))
        marker, attempt, _ = self.actual_final(command)
        self.assertEqual((result.status.value, attempt.state.status.value), ("uncertain", "uncertain"))
        self.assertEqual(marker.outcome.value, "returned")

    def test_invalid_result_is_uncertain_despite_classified_final(self):
        owner, command = self.composition()
        self.adapter.transform_result = lambda result: object()
        result = asyncio.run(owner.execute_managed(command))
        marker, attempt, _ = self.actual_final(command)
        self.assertEqual((result.status.value, attempt.state.status.value), ("uncertain", "uncertain"))
        self.assertEqual(marker.outcome.value, "returned")

    def test_ordinary_provider_exception_after_admission_retains_uncertainty(self):
        owner, command = self.composition()
        self.adapter.final_error = RuntimeError("bounded fixture fault")
        result = asyncio.run(owner.execute_managed(command))
        marker, attempt, _ = self.actual_final(command)
        self.assertEqual((result.status.value, attempt.state.status.value), ("uncertain", "uncertain"))
        self.assertEqual(marker.outcome.value, "unknown")

    def test_cancellation_before_final_preserves_identity_and_does_not_fold(self):
        owner, command = self.composition()
        cancelled = asyncio.CancelledError("cancel before final")
        def cancel(port):
            raise cancelled
        self.adapter.after_preparation = cancel
        with self.assertRaises(asyncio.CancelledError) as caught:
            asyncio.run(owner.execute_managed(command))
        self.assertIs(caught.exception, cancelled)
        self.assertEqual(len(self.adapter.calls), 1)
        self.assertEqual(self.assert_unfolded(command).disposition.value, "planned")

    def test_cancellation_after_final_admission_folds_uncertainty_then_rethrows(self):
        owner, command = self.composition()
        cancelled = asyncio.CancelledError("cancel after final admission")
        def cancel(port):
            raise cancelled
        self.adapter.after_final_admission = cancel
        with self.assertRaises(asyncio.CancelledError) as caught:
            asyncio.run(owner.execute_managed(command))
        self.assertIs(caught.exception, cancelled)
        marker, attempt, _ = self.actual_final(command)
        self.assertEqual(attempt.state.status.value, "uncertain")
        self.assertEqual(marker.outcome.value, "pending")
        self.assertEqual(len(self.adapter.calls), 3)

    def test_original_cancellation_takes_precedence_over_fold_failure(self):
        owner, command = self.composition()
        cancelled = asyncio.CancelledError("original cancellation")
        def cancel(port):
            raise cancelled
        self.adapter.after_final_admission = cancel
        with mock.patch.object(EffectAttemptFoldService, "execute", side_effect=RuntimeError("fold interrupted")):
            with self.assertRaises(asyncio.CancelledError) as caught:
                asyncio.run(owner.execute_managed(command))
        self.assertIs(caught.exception, cancelled)
        self.assertEqual(self.assert_unfolded(command).outcome.value, "pending")

    def test_arbitrary_outer_wrapper_cannot_advertise_the_prepared_path(self):
        class Wrapper:
            def __init__(self, inner):
                self.inner = inner
            def execute(self, context):
                return self.inner.execute(context)
            def execute_runtime(self, context, request, **options):
                return self.inner.execute_runtime(context, request, **options)
        owner, command = self.composition(outer_wrapper=Wrapper)
        before = self.attempt_snapshot()
        result = asyncio.run(owner.execute_managed(command))
        self.assertEqual((result.status.value, result.effects_attempted), ("unsupported", 0))
        self.assertEqual(self.attempt_snapshot(), before)
        self.assert_no_preparation()

    def test_advertised_adapter_without_keyword_is_not_retried_positionally(self):
        calls = []
        def legacy(request, authority):
            calls.append(request)
            return RuntimeEffectResult.succeeded(request.effect_id)
        self.adapter.execute_with_authority = legacy
        owner, command = self.composition()
        with self.assertRaises(operations.ExecutionInvocationPlanned):
            asyncio.run(owner.execute_managed(command))
        self.assertEqual(calls, [])
        self.assertEqual(self.assert_unfolded(command).disposition.value, "planned")

    def test_unprepared_local_path_omits_the_keyword_and_needs_no_profile_or_service(self):
        from control_plane_kit_operations.runtime_authorities import LocalDockerSocketAuthority
        self.change_runtime(LocalDockerSocketAuthority())
        calls = []
        def legacy(request, authority):
            calls.append(request)
            return RuntimeEffectResult.failed(request.effect_id,
                RuntimeEffectFailure("fixture.local", "Controlled local result."))
        self.adapter.execute_with_authority = legacy
        owner, command = self.composition(profile=False, service=False)
        result = asyncio.run(owner.execute_managed(command))
        self.assertEqual((result.status.value, result.effects_attempted), ("failed", 1))
        self.assertEqual(len(calls), 1)
        self.assertEqual(self.connection.execute("SELECT count(*) FROM cpk_execution_command_invocations").fetchone()[0], 0)
        self.assertEqual(self.connection.execute("SELECT count(*) FROM cpk_secret_use_authorizations").fetchone()[0], 0)

    def assert_selection_change_refuses(self, change, *, after_start=False, service=True, profile=True):
        owner, command = self.composition(service=service, profile=profile)
        execute = EffectAttemptStartService.execute
        def changed_start(owner, start, **options):
            if not after_start:
                change()
            started = execute(owner, start, **options)
            if after_start:
                change()
            return started
        with mock.patch.object(EffectAttemptStartService, "execute", changed_start):
            with self.assertRaises(operations.ExecutionInvocationConflict):
                asyncio.run(owner.execute_managed(command))
        self.assert_no_preparation()
        self.assertEqual(self.connection.execute("SELECT count(*) FROM cpk_effect_attempts").fetchone()[0], 1)

    def test_remote_registration_replacement_between_context_and_start_refuses(self):
        self.assert_selection_change_refuses(lambda: self.change_runtime(
            replace(self.runtime.authority, endpoint="tcp://replacement.example.test:2376")))

    def test_remote_to_local_between_context_and_start_refuses_without_marker_repair(self):
        from control_plane_kit_operations.runtime_authorities import LocalDockerSocketAuthority
        self.assert_selection_change_refuses(lambda: self.change_runtime(LocalDockerSocketAuthority()))
        self.assertEqual(self.connection.execute("SELECT count(*) FROM cpk_execution_command_invocations").fetchone()[0], 0)

    def test_local_to_remote_unexpected_marker_refuses_even_without_invocation_service(self):
        from control_plane_kit_operations.runtime_authorities import LocalDockerSocketAuthority
        self.change_runtime(LocalDockerSocketAuthority())
        self.assert_selection_change_refuses(lambda: self.change_runtime(
            replace(self.runtime.authority, endpoint="tcp://new-remote.example.test:2376")), service=False, profile=False)
        self.assertEqual(self.connection.execute("SELECT count(*) FROM cpk_execution_command_invocations").fetchone()[0], 1)

    def test_missing_expected_marker_after_start_refuses_without_backfill(self):
        self.assert_selection_change_refuses(lambda: self.connection.execute(
            "DELETE FROM cpk_execution_command_invocations"), after_start=True)
        self.assertEqual(self.connection.execute("SELECT count(*) FROM cpk_execution_command_invocations").fetchone()[0], 0)

    def test_corrupt_expected_marker_after_start_refuses_without_repair(self):
        self.assert_selection_change_refuses(lambda: self.connection.execute(
            "UPDATE cpk_execution_command_invocations SET descriptor=descriptor || '{\"unexpected\":true}'::jsonb"),
            after_start=True)
        self.assertTrue(self.connection.execute("SELECT descriptor ? 'unexpected' FROM cpk_execution_command_invocations").fetchone()[0])

    def assert_existing_stop(self, expected, *, key="prepared-existing"):
        owner, command = self.composition(profile=False, service=False)
        command = replace(command, execution=replace(command.execution, idempotency_key=IdempotencyKey(key)))
        with self.assertRaises(expected):
            asyncio.run(owner.execute_managed(command))
        self.assert_no_preparation()
        self.assertIsNone(self.adapter.invocations)
        with self.unit_of_work() as uow:
            receipt = uow.stores.execution.command_receipt_for_idempotency("run-a", key)
        self.assertEqual(receipt.status.value, "incomplete")
        self.assertEqual(self.connection.execute("SELECT count(*) FROM cpk_effect_attempts").fetchone()[0], 1)

    def test_existing_planned_attempt_never_enters_legacy_observation(self):
        self.seed_start()
        self.assert_existing_stop(operations.ExecutionInvocationPlanned)

    def test_existing_denied_attempt_is_history_control_without_new_denial_result(self):
        bound, start, started = self.seed_start()
        service = operations.ExecutionInvocationService(self.ledger, permission_policy=self.policy(), clock=now)
        port = service.bind_runtime(command_context=bound, start=start, started=started)
        self.revoke_operate()
        with self.assertRaises(operations.ExecutionInvocationDenied):
            port.admit("prepared-runtime-dispatch")
        self.assert_existing_stop(operations.ExecutionInvocationDenied)

    def test_unqualified_existing_admitted_pending_and_classified_history_never_observes(self):
        bound, start, started = self.seed_start()
        service = operations.ExecutionInvocationService(self.ledger, permission_policy=self.policy(), clock=now)
        port = service.bind_runtime(command_context=bound, start=start, started=started)
        permit = port.admit("prepared-runtime-dispatch")
        self.assert_existing_stop(operations.ExecutionInvocationConflict, key="existing-pending")
        port.record_outcome(permit, "returned")
        self.assert_existing_stop(operations.ExecutionInvocationConflict, key="existing-classified")

    def test_remote_original_current_local_missing_marker_cannot_use_local_observation(self):
        from control_plane_kit_operations.runtime_authorities import LocalDockerSocketAuthority
        self.seed_start()
        self.change_runtime(LocalDockerSocketAuthority())
        self.connection.execute("DELETE FROM cpk_execution_command_invocations")
        self.assert_existing_stop(operations.ExecutionInvocationConflict)
        self.assertEqual(self.connection.execute("SELECT count(*) FROM cpk_execution_command_invocations").fetchone()[0], 0)

    def test_ambiguous_local_original_refuses_until_fresh_reconciliation_is_qualified(self):
        from control_plane_kit_operations.runtime_authorities import LocalDockerSocketAuthority
        self.change_runtime(LocalDockerSocketAuthority())
        self.seed_start()
        self.assert_existing_stop(operations.ExecutionInvocationConflict)
        self.assertEqual(self.connection.execute("SELECT count(*) FROM cpk_execution_command_invocations").fetchone()[0], 0)

    def test_completed_command_receipt_replay_remains_read_only(self):
        owner, command = self.composition()
        result = asyncio.run(owner.execute_managed(command))
        calls = list(self.adapter.calls)
        self.revoke_operate()
        self.assertEqual(asyncio.run(owner.execute_managed(command)), result)
        self.assertEqual(self.adapter.calls, calls)
        self.assertEqual(self.observer.calls, [])


class PostgresRuntimeInvocationPriorEffectTests(_PreparedRuntimeCarriageFixture, unittest.TestCase):
    def reset_start_truth(self, *, compensation=False):
        prior = ActivityId("start-prior-runtime")
        plan = ActivityPlan((PlannedActivity(prior, StartRuntime(RuntimeTarget("runtime-prior"))),
            PlannedActivity(ActivityId("start-runtime"), StartRuntime(RuntimeTarget("runtime-a")),
                dependencies=(ActivityDependency(prior),))))
        desired = DeploymentGraph("desired", runtimes={
            "runtime-prior": RuntimeRecord("runtime-prior", RuntimeKind.DOCKER),
            "runtime-a": RuntimeRecord("runtime-a", RuntimeKind.DOCKER,
                authority_ref=RuntimeAuthorityReference("prepared-runtime"))})
        super().reset_start_truth(compensation=compensation, plan=plan, desired=desired)

    def test_missing_profile_counts_only_the_preceding_new_effect(self):
        owner, command = self.composition(profile=False)
        command = replace(command, execution=replace(command.execution, max_effects=2))
        result = asyncio.run(owner.execute_managed(command))
        self.assertEqual((result.status.value, result.effects_attempted), ("unsupported", 1))
        self.assertEqual(len(self.adapter.local_calls), 1)
        self.assertEqual(self.adapter.requests, [])
        self.assertEqual(self.connection.execute("SELECT count(*) FROM cpk_effect_attempts").fetchone()[0], 1)
        self.assertEqual(self.connection.execute("SELECT count(*) FROM cpk_execution_command_invocations").fetchone()[0], 0)
        self.assertEqual(self.connection.execute("SELECT count(*) FROM cpk_secret_use_authorizations").fetchone()[0], 0)

    def test_fresh_denial_counts_prior_effect_and_actual_prepared_start(self):
        owner, command = self.composition()
        command = replace(command, execution=replace(command.execution, max_effects=2))
        self.adapter.after_preparation = lambda port: self.revoke_operate()
        result = asyncio.run(owner.execute_managed(command))
        self.assertEqual((result.status.value, result.effects_attempted), ("authorization-denied", 2))
        self.assertEqual(len(self.adapter.local_calls), 1)
        self.assertEqual(len(self.adapter.calls), 1)
        marker, attempt, _ = self.actual_final(command)
        self.assertEqual((marker.disposition.value, attempt.state.status.value), ("planned", "started"))
        self.assertEqual(self.connection.execute("SELECT count(*) FROM cpk_effect_attempts").fetchone()[0], 2)
        self.assertEqual(self.connection.execute("SELECT count(*) FROM cpk_effect_attempt_outcomes").fetchone()[0], 1)
        self.assertEqual(self.observer.calls, [])


class RuntimeInvocationRegistrationTests(unittest.TestCase):
    def test_profile_is_closed_and_exact_without_changing_positional_version_flags(self):
        class Profile(str):
            pass
        for profile in ("unknown", True, Profile("cpk.semantic-invocations.v1")):
            with self.subTest(profile_type=type(profile).__name__), self.assertRaises(ValueError):
                RuntimeAdapterRegistration(object(), invocation_profile=profile)
        registration = RuntimeAdapterRegistration(object(), True, False, True,
            invocation_profile="cpk.semantic-invocations.v1")
        self.assertEqual((registration.exact_version_execute, registration.exact_version_execute_with_authority,
            registration.exact_version_observe), (True, False, True))
