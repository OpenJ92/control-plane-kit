"""Operations owns ordered admission, original grants and bounded completion."""

import asyncio
from dataclasses import replace
import unittest
from unittest import mock

import control_plane_kit_operations as operations
from control_plane_kit_core.identity import AuthenticatedPrincipal
from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_core.runtime_effect_observation import runtime_effect_request_for_intent
from control_plane_kit_core.runtime_effects import RuntimeEffectFailure, RuntimeEffectResult
from control_plane_kit_core.secrets import SecretProviderEndpointReference, SecretReference, SecretUseIntent
from control_plane_kit_operations.secret_providers import (
    RegisterSecretProviderCommand, RegisterSecretReferenceCommand, SecretProviderKind,
    SecretProviderRegistrationService, SecretUseAuthorizationService,
)
from control_plane_kit_operations.workflows import IdempotencyKey
from tests.postgres_effect_attempt_reconciliation_fixture import UnitOfWorkLedger, FailIfObserver
from tests.test_postgres_probe_invocations import _ProbeInvocationFixture
from tests import test_postgres_workload_invocations as workload_fixture
from tests.test_postgres_runtime_invocation_carriage import _PreparedRuntimeCarriageFixture
from tests.test_managed_application_chain import now


class _ProbeAdapter:
    def __init__(self, fixture):
        self.fixture, self.calls, self.requests = fixture, [], []
        self.after_first = None
        self.skip_checks = False
        self.result_kind = "failed"

    def execute(self, request, *, invocations=None):
        raise AssertionError("ordinary verification must not use unprepared fallback")

    def execute_with_authority(self, request, authority, *, invocations):
        self.requests.append(request)
        permit = invocations.admit("ordinary-verification-acquisition")
        self.fixture.assertEqual(self.fixture.ledger.active, 0)
        self.calls.append("acquisition")
        invocations.record_outcome(permit, "returned")
        for index, check in enumerate(() if self.skip_checks else
                request.products[0].product.runtime_contract.verification.checks):
            child = invocations.for_check(check.check_id)
            if child.credential_grant is not None:
                credential = child.admit("secret-resolution", authorization_id=child.credential_grant.authorization_id)
                self.fixture.assertEqual(self.fixture.ledger.active, 0)
                self.calls.append(child.credential_grant.authorization_id)
                child.record_outcome(credential, "returned")
            permit = child.admit("ordinary-verification-check")
            self.fixture.assertEqual(self.fixture.ledger.active, 0)
            self.calls.append(check.check_id)
            child.record_outcome(permit, "returned")
            if index == 0 and self.after_first is not None:
                self.after_first()
        if self.result_kind == "succeeded":
            return RuntimeEffectResult.succeeded(request.effect_id)
        return RuntimeEffectResult.failed(request.effect_id,
            RuntimeEffectFailure("fixture.stopped", "Controlled verification result."))


class _ProbeAdmissionFixture(_ProbeInvocationFixture):
    def setUp(self):
        super().setUp()
        self.permissions().grant(operations.GrantWorkspacePermissions(self.admin_context, "workspace-a",
            self.worker, (PolicyScope.SECRET_PROVIDER_USE,), 1, IdempotencyKey("grant-probe-secrets")))
        self.original = operations.WorkspaceContextResolver(self.unit_of_work,
            installation_id="installation-a").resolve(AuthenticatedPrincipal(self.worker), "workspace-a")
        self.ledger = UnitOfWorkLedger(self.unit_of_work)
        self.adapter, self.observer = _ProbeAdapter(self), FailIfObserver()
        from tests.effect_attempt_intent_fixture import product_material
        check, = product_material().product.runtime_contract.verification.checks
        reference, purpose = check.authentication.password_reference, SecretUseIntent.POSTGRES_PASSWORD
        roles = [(reference, purpose)]
        from control_plane_kit_operations.runtime_authorities import RemoteDockerTlsAuthority
        if type(self.runtime.authority) is RemoteDockerTlsAuthority:
            roles.extend(((self.runtime.authority.ca_certificate, SecretUseIntent.DOCKER_REMOTE_TLS_CA_CERTIFICATE),
                (self.runtime.authority.client_certificate, SecretUseIntent.DOCKER_REMOTE_TLS_CLIENT_CERTIFICATE),
                (self.runtime.authority.client_key, SecretUseIntent.DOCKER_REMOTE_TLS_CLIENT_KEY)))
        registrations = SecretProviderRegistrationService(self.unit_of_work)
        provider = registrations.register_provider(RegisterSecretProviderCommand(
            workspace_id="workspace-a", provider_id=reference.provider_id,
            provider_kind=SecretProviderKind.CONTROL_PLANE_KIT_SECRETS, display_name="Probe fixture",
            endpoint_reference=SecretProviderEndpointReference("probe-provider"),
            credential_reference=SecretReference("secret://bootstrap/probe/token"),
            allowed_reference_prefixes=tuple(ref for ref, _ in roles), allowed_intents=tuple(intent for _, intent in roles),
            admitted_by="operator-a", admitted_at=self.clock(), actor_scopes=(PolicyScope.SECRET_PROVIDER_REGISTER,)))
        for reference, purpose in roles:
            registrations.register_reference(RegisterSecretReferenceCommand(workspace_id="workspace-a",
                reference=reference, provider_registration_id=provider.registration_id, allowed_intents=(purpose,),
                admitted_by="operator-a", admitted_at=self.clock(), actor_scopes=(PolicyScope.SECRET_PROVIDER_REGISTER,)))

    def composition(self, **options):
        return _PreparedRuntimeCarriageFixture.composition(self, **{"ordinary": True, **options})

    def original_start(self):
        return workload_fixture.PostgresWorkloadVersionInvocationTests.original_start(self)

    def prepared_port(self, *, factory=None):
        context, start, started = self.original_start()
        service = operations.ExecutionInvocationService(factory or self.ledger,
            permission_policy=self.policy(), clock=now)
        port = service.bind_runtime(command_context=context, start=start, started=started)
        request = runtime_effect_request_for_intent(start.intent,
            effect_id=started.attempt.original_start_event.event_id, secret_resolution_grants=())
        request, port = service.authorize_ordinary_verification(port, request, SecretUseAuthorizationService(self.ledger))
        return service, port, request

    def returned_acquisition(self, port):
        permit = port.admit("ordinary-verification-acquisition")
        self.assertEqual(self.ledger.active, 0)
        port.record_outcome(permit, "returned")
        return permit

    def returned_secret(self, child):
        permit = child.admit("secret-resolution", authorization_id=child.credential_grant.authorization_id)
        self.assertEqual(self.ledger.active, 0)
        child.record_outcome(permit, "returned")
        return permit

    def revoke_operate(self):
        return _PreparedRuntimeCarriageFixture.revoke_operate(self)


class PostgresProbeInvocationAdmissionTests(_ProbeAdmissionFixture, unittest.TestCase):
    def test_descriptor_equivalent_postgres_subclass_preserves_credential_role(self):
        from tests.effect_attempt_intent_fixture import subclass_copy
        from control_plane_kit_operations.ordinary_verification_invocations import ordinary_verification_invocation_profile
        _, start, started = self.original_start()
        material, = start.intent.products
        contract = material.product.runtime_contract
        equivalent = replace(start.intent, products=(replace(material, product=replace(material.product,
            runtime_contract=replace(contract, verification=replace(contract.verification,
                checks=tuple(subclass_copy(check) for check in contract.verification.checks))))),))
        options = dict(original_event_id=started.attempt.original_start_event.event_id, runtime_authority=self.runtime)
        self.assertEqual(ordinary_verification_invocation_profile(start.intent, **options),
            ordinary_verification_invocation_profile(equivalent, **options))

    def test_repeated_password_roles_have_check_scoped_grants_and_committed_ordered_admission(self):
        service, port, request = self.prepared_port()
        self.assertEqual(request.secret_resolution_grants, ())
        first, second = (port.for_check(check.check_id) for check in port.profile.checks)
        self.assertEqual(first.credential_grant.reference, second.credential_grant.reference)
        self.assertEqual(first.credential_grant.intent, second.credential_grant.intent)
        self.assertNotEqual(first.credential_grant.authorization_id, second.credential_grant.authorization_id)
        self.assertNotEqual(first.credential_grant.probe_id, second.credential_grant.probe_id)
        self.assertEqual(port.for_check(first.check.check_id).credential_grant, first.credential_grant)
        self.returned_acquisition(port)
        for child in (first, second):
            self.returned_secret(child)
            permit = child.admit("ordinary-verification-check")
            with self.unit_of_work() as uow:
                retained = uow.stores.execution.command_invocation(permit.record.command, permit.record.invocation_id)
            self.assertEqual(retained, permit.record)
            self.assertEqual(self.ledger.active, 0)
            child.record_outcome(permit, "returned")
            with self.assertRaises(operations.ExecutionInvocationExisting):
                child.admit("ordinary-verification-check")
        service.inspect_ordinary_verification(port, RuntimeEffectResult.succeeded(request.effect_id))

    def test_per_check_revocation_preserves_prior_completion_and_denies_later_secret(self):
        service, port, request = self.prepared_port()
        self.returned_acquisition(port)
        first, second = (port.for_check(check.check_id) for check in port.profile.checks)
        self.returned_secret(first)
        permit = first.admit("ordinary-verification-check")
        self.revoke_operate()
        first.record_outcome(permit, "returned")
        with self.assertRaises(operations.ExecutionInvocationDenied):
            second.admit("secret-resolution", authorization_id=second.credential_grant.authorization_id)
        with self.unit_of_work() as uow:
            rows = uow.stores.execution.ordinary_verification_invocations(port.marker, port.profile)
        self.assertEqual(sum(row.kind.value == "ordinary-verification-check" for row in rows), 1)
        self.assertEqual(sum(row.disposition.value == "denied" for row in rows), 1)
        with self.assertRaises(operations.ExecutionInvocationDenied):
            service.inspect_ordinary_verification(port, RuntimeEffectResult.succeeded(request.effect_id))

    def test_stage_and_child_scope_cannot_skip_acquisition_credentials_or_prior_check(self):
        _, port, _ = self.prepared_port()
        first, second = (port.for_check(check.check_id) for check in port.profile.checks)
        with self.assertRaises(operations.ExecutionInvocationConflict):
            first.admit("secret-resolution", authorization_id=first.credential_grant.authorization_id)
        self.returned_acquisition(port)
        for scope, kind, options in (
            (port, "secret-resolution", {"authorization_id": first.credential_grant.authorization_id}),
            (first, "ordinary-verification-acquisition", {}),
            (first, "ordinary-verification-check", {}),
            (first, "secret-resolution", {"authorization_id": second.credential_grant.authorization_id}),
            (second, "secret-resolution", {"authorization_id": second.credential_grant.authorization_id}),
        ):
            with self.subTest(kind=kind), self.assertRaises(operations.ExecutionInvocationConflict):
                scope.admit(kind, **options)
        self.returned_secret(first)
        permit = first.admit("ordinary-verification-check")
        with self.assertRaises(operations.ExecutionInvocationConflict):
            port.record_outcome(permit, "returned")
        first.record_outcome(permit, "returned")
        with self.assertRaises(operations.ExecutionInvocationExisting):
            first.admit("secret-resolution", authorization_id=first.credential_grant.authorization_id)

    def test_success_requires_all_checks_but_failed_result_allows_frontier_credential(self):
        service, port, request = self.prepared_port()
        self.returned_acquisition(port)
        first = port.for_check(port.profile.checks[0].check_id)
        self.returned_secret(first)
        with self.assertRaises(operations.ExecutionInvocationConflict):
            service.inspect_ordinary_verification(port, RuntimeEffectResult.succeeded(request.effect_id))
        service.inspect_ordinary_verification(port, RuntimeEffectResult.failed(request.effect_id,
            RuntimeEffectFailure("fixture.stopped", "Controlled endpoint refusal.")))

    def test_pending_and_unknown_are_not_hidden_by_returned_acquisition(self):
        service, port, request = self.prepared_port()
        self.returned_acquisition(port)
        first = port.for_check(port.profile.checks[0].check_id)
        permit = first.admit("secret-resolution", authorization_id=first.credential_grant.authorization_id)
        failed = RuntimeEffectResult.failed(request.effect_id, RuntimeEffectFailure("fixture.stopped", "Controlled failure."))
        with self.assertRaises(operations.ExecutionInvocationConflict):
            service.inspect_ordinary_verification(port, failed)
        first.record_outcome(permit, "unknown")
        with self.assertRaises(operations.ExecutionInvocationConflict):
            service.inspect_ordinary_verification(port, failed)
        uncertain = RuntimeEffectResult.uncertain(request.effect_id,
            RuntimeEffectFailure("fixture.unknown", "Controlled uncertainty."))
        service.inspect_ordinary_verification(port, uncertain)
        with self.assertRaises(operations.ExecutionInvocationConflict):
            service.inspect_ordinary_verification(port)

    def test_lost_check_commit_ack_never_returns_a_new_permit(self):
        from control_plane_kit_operations.postgres import PostgresUnitOfWork
        from tests.test_postgres_health_effect_start_rollback import _CommitThenRaiseConnection
        service, port, _ = self.prepared_port()
        self.returned_acquisition(port)
        first = port.for_check(port.profile.checks[0].check_id)
        self.returned_secret(first)
        failure = RuntimeError("probe admission acknowledgement lost")
        def factory():
            return PostgresUnitOfWork(lambda: _CommitThenRaiseConnection(self.connect(), failure))
        with mock.patch.object(service, "_unit_of_work_factory", factory):
            with self.assertRaises(operations.ExecutionInvocationConflict):
                first.admit("ordinary-verification-check")
        with self.assertRaises(operations.ExecutionInvocationExisting):
            first.admit("ordinary-verification-check")

    def test_coordinator_uses_qualified_family_and_terminal_replay_has_no_io(self):
        owner, command = self.composition()
        result = asyncio.run(owner.execute_managed(command))
        self.assertEqual(result.status.value, "failed")
        self.assertEqual(len(self.adapter.calls), 5)
        self.assertEqual(self.adapter.requests[0].secret_resolution_grants, ())
        before = tuple(self.adapter.calls)
        self.assertEqual(asyncio.run(owner.execute_managed(command)), result)
        self.assertEqual(tuple(self.adapter.calls), before)
        self.assertEqual(self.observer.calls, [])

    def test_old_invocation_profile_is_not_new_family_qualification(self):
        owner, command = self.composition(ordinary=False)
        result = asyncio.run(owner.execute_managed(command))
        self.assertEqual(result.status.value, "unsupported")
        self.assertEqual(self.adapter.calls, [])
        self.assertEqual(self.connection.execute("SELECT count(*) FROM cpk_effect_attempts").fetchone()[0], 0)
        self.assertEqual(self.connection.execute("SELECT count(*) FROM cpk_secret_use_authorizations").fetchone()[0], 0)

    def test_retained_attempt_without_service_returns_closed_control_without_io(self):
        self.original_start()
        owner, command = self.composition(service=False)
        with self.assertRaises(operations.ExecutionInvocationConflict):
            asyncio.run(owner.execute_managed(command))
        self.assertEqual(self.adapter.calls, [])
        self.assertEqual(self.observer.calls, [])

    def test_interrupted_returned_acquisition_never_resumes_checks(self):
        _, port, _ = self.prepared_port()
        self.returned_acquisition(port)
        owner, command = self.composition()
        result = asyncio.run(owner.execute_managed(command))
        self.assertEqual(result.status.value, "unsupported")
        self.assertEqual(self.adapter.calls, [])
        self.assertEqual(self.observer.calls, [])

    def test_coordinator_rejects_false_success_with_missing_check_coverage(self):
        self.adapter.skip_checks, self.adapter.result_kind = True, "succeeded"
        owner, command = self.composition()
        with self.assertRaises(operations.ExecutionInvocationConflict):
            asyncio.run(owner.execute_managed(command))
        self.assertEqual(self.connection.execute("SELECT count(*) FROM cpk_effect_attempt_outcomes").fetchone()[0], 0)


class PostgresEmptyProbeInvocationTests(_ProbeAdmissionFixture, unittest.TestCase):
    empty_checks = True

    def test_empty_contract_requires_only_acquisition_and_no_secret_grants(self):
        service, port, request = self.prepared_port()
        self.assertEqual(port.profile.checks, ())
        self.assertEqual(request.secret_resolution_grants, ())
        self.assertEqual(self.connection.execute("SELECT count(*) FROM cpk_secret_use_authorizations").fetchone()[0], 0)
        self.returned_acquisition(port)
        service.inspect_ordinary_verification(port, RuntimeEffectResult.succeeded(request.effect_id))
        with self.unit_of_work() as uow:
            self.assertEqual(len(uow.stores.execution.ordinary_verification_invocations(port.marker, port.profile)), 1)


class PostgresAuthoritylessProbeInvocationTests(_ProbeAdmissionFixture, unittest.TestCase):
    authorityless = True

    def test_dynamic_authorityless_verification_refuses_before_start_or_authorization(self):
        owner, command = self.composition()
        result = asyncio.run(owner.execute_managed(command))
        self.assertEqual(result.status.value, "unsupported")
        self.assertEqual(self.adapter.calls, [])
        self.assertEqual(self.connection.execute("SELECT count(*) FROM cpk_effect_attempts").fetchone()[0], 0)
        self.assertEqual(self.connection.execute("SELECT count(*) FROM cpk_secret_use_authorizations").fetchone()[0], 0)


class PostgresNonsecretProbeInvocationTests(_ProbeAdmissionFixture, unittest.TestCase):
    no_credentials = True

    def test_revocation_after_admitted_check_denies_later_nonsecret_check(self):
        _, port, request = self.prepared_port()
        self.assertEqual(request.secret_resolution_grants, ())
        self.returned_acquisition(port)
        first, second = (port.for_check(check.check_id) for check in port.profile.checks)
        self.assertIsNone(first.credential_grant)
        permit = first.admit("ordinary-verification-check")
        self.revoke_operate()
        first.record_outcome(permit, "returned")
        with self.assertRaises(operations.ExecutionInvocationDenied):
            second.admit("ordinary-verification-check")
        with self.unit_of_work() as uow:
            rows = uow.stores.execution.ordinary_verification_invocations(port.marker, port.profile)
        checks = tuple(row for row in rows if row.kind.value == "ordinary-verification-check")
        self.assertEqual({row.disposition.value for row in checks}, {"admitted", "denied"})
        self.assertEqual(sum(row.outcome is not None and row.outcome.value == "returned" for row in checks), 1)


class PostgresRemoteProbeInvocationTests(_ProbeAdmissionFixture, unittest.TestCase):
    from control_plane_kit_operations.runtime_authorities import RemoteDockerTlsAuthority
    runtime_authority = RemoteDockerTlsAuthority("tcp://runtime.example.test:2376",
        SecretReference("secret://local/probe/ca"), SecretReference("secret://local/probe/cert"),
        SecretReference("secret://local/probe/key"))

    def test_coordinator_does_not_hide_pending_tls_behind_later_denial(self):
        def interrupted(request, authority, *, invocations):
            first, second, _ = request.secret_resolution_grants
            invocations.admit("secret-resolution", authorization_id=first.authorization_id)
            self.revoke_operate()
            invocations.admit("secret-resolution", authorization_id=second.authorization_id)
            self.fail("denied admission returned a permit")
        self.adapter.execute_with_authority = interrupted
        owner, command = self.composition()
        with self.assertRaises(operations.ExecutionInvocationConflict):
            asyncio.run(owner.execute_managed(command))
        self.assertEqual(self.connection.execute("SELECT count(*) FROM cpk_effect_attempt_outcomes").fetchone()[0], 0)
        self.assertEqual(self.observer.calls, [])

    def test_connection_roles_precede_acquisition_and_pending_tls_is_not_hidden(self):
        service, port, request = self.prepared_port()
        self.assertEqual(len(request.secret_resolution_grants), 3)
        first = port.for_check(port.profile.checks[0].check_id)
        self.assertNotIn(first.credential_grant, request.secret_resolution_grants)
        with self.assertRaises(operations.ExecutionInvocationConflict):
            port.admit("ordinary-verification-acquisition")
        grant = request.secret_resolution_grants[0]
        permit = port.admit("secret-resolution", authorization_id=grant.authorization_id)
        with self.assertRaises(operations.ExecutionInvocationConflict):
            service.inspect_ordinary_verification(port)
        port.record_outcome(permit, "returned")
        with self.assertRaises(operations.ExecutionInvocationPlanned):
            service.inspect_ordinary_verification(port)
        for grant in request.secret_resolution_grants[1:]:
            permit = port.admit("secret-resolution", authorization_id=grant.authorization_id)
            port.record_outcome(permit, "returned")
        self.returned_acquisition(port)
        self.returned_secret(first)
        with self.assertRaises(operations.ExecutionInvocationConflict):
            first.admit("secret-resolution", authorization_id=request.secret_resolution_grants[0].authorization_id)
