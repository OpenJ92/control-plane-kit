"""Ordinary runtime carriage targets compose real admission and history owners."""

import asyncio
import inspect
import unittest
from uuid import uuid4

import control_plane_kit_operations as operations
from control_plane_kit_core.identity import AuthenticatedPrincipal
from control_plane_kit_core.operations import EffectAttemptIdentity, RunId
from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_core.runtime_effects import RuntimeEffectFailure, RuntimeEffectResult
from control_plane_kit_core.secrets import SecretProviderEndpointReference, SecretProviderId, SecretReference, SecretUseIntent
from control_plane_kit_core.types import RuntimeKind
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

    def execute(self, request):
        raise AssertionError("prepared remote fixture reached the local arm")

    def execute_with_authority(self, request, authority, *, invocations=None):
        self.requests.append(request)
        self.authorities.append(authority)
        self.invocations = invocations
        if invocations is not None:
            for grant in request.secret_resolution_grants:
                permit = invocations.admit("secret-resolution", authorization_id=grant.authorization_id)
                self._call(invocations, permit, grant.authorization_id)
            permit = invocations.admit("prepared-runtime-dispatch")
            self._call(invocations, permit, request.effect_id)
        # A held, correlated result stops the fixture's run without claiming a deployment.
        return RuntimeEffectResult.failed(request.effect_id,
            RuntimeEffectFailure("fixture.stopped", "Controlled runtime result."))

    def _call(self, invocations, permit, correlation):
        self.active_transactions.append(self.fixture.ledger.active)
        self.fixture.assertEqual(self.fixture.ledger.active, 0)
        with self.fixture.unit_of_work() as uow:
            retained = uow.stores.execution.command_invocation(permit.record.command, permit.record.invocation_id)
        self.fixture.assertEqual(retained, permit.record)
        self.fixture.assertEqual((retained.disposition.value, retained.outcome.value), ("admitted", "pending"))
        self.committed.append(retained)
        self.calls.append(correlation)
        invocations.record_outcome(permit, "returned")


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

    def composition(self, *, profile=True, service=True, wrapped=True):
        # Until the public profile keyword exists, exercise the actual legacy
        # registration and observe the missing carriage after a real start.
        options = {}
        if "invocation_profile" in inspect.signature(RuntimeAdapterRegistration).parameters:
            options["invocation_profile"] = "cpk.semantic-invocations.v1" if profile else None
        registration = RuntimeAdapterRegistration(self.adapter, **options)
        dispatcher = RuntimeInterpreterDispatcher({RuntimeKind.DOCKER: registration},
            SecretUseAuthorizationService(self.ledger))
        adapter = ActivityExecutionDispatcher(dispatcher) if wrapped else dispatcher
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
    def test_real_coordinator_carries_original_port_and_exact_session_to_remote_arm(self):
        owner, command = self.composition()
        result = asyncio.run(owner.execute_managed(command))
        marker, attempt, session = self.actual_final(command)
        self.assertIsNotNone(self.adapter.invocations,
            "prepared coordinator did not carry its original bound invocation port")
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
