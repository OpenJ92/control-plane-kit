"""Fixed standalone operations use real current permission at owner admission."""

from contextlib import contextmanager
from dataclasses import replace
import inspect
import os
import threading
from types import SimpleNamespace
import unittest
from unittest import mock
from uuid import uuid4

import psycopg
from psycopg.conninfo import make_conninfo

import control_plane_kit_operations as operations
from control_plane_kit_core.identity import AuthenticatedPrincipal, PrincipalIdentity, PrincipalKind
from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_core.secrets import SecretValue
from control_plane_kit_operations.delegation_key_generation_attempts import (
    GenerationAttemptAuthorizationDenied, GenerationAttemptClaim, GenerationAttemptAdmissionService,
)
from control_plane_kit_operations.delegation_key_generation_commands import DelegationKeyGenerationCommandService
from control_plane_kit_operations.secret_material_provision_attempts import (
    ProvisionAttemptAuthorizationDenied, ProvisionAttemptClaim, ProvisionAttemptAdmissionService,
)
from control_plane_kit_operations.secret_material_provision_commands import SecretMaterialProvisionCommandService
from control_plane_kit_operations.workflows import IdempotencyKey
from tests.lifecycle_lock_fixture import LifecycleLockFixture
from tests import test_delegation_key_generation_commands as generation_cases
from tests import test_secret_material_provision_commands as provision_cases


class _StandalonePermissions(LifecycleLockFixture):
    def setUp(self):
        database_url = os.environ["CPK_OPERATIONS_TEST_DATABASE_URL"]
        owner = psycopg.connect(database_url, autocommit=True)
        self.addCleanup(owner.close)
        schema = "standalone_permissions_" + uuid4().hex
        owner.execute(f'CREATE SCHEMA "{schema}"')
        self.addCleanup(owner.execute, f'DROP SCHEMA "{schema}" CASCADE')
        scoped = make_conninfo(database_url, options=f"-c search_path={schema}")
        self.case = self.case_type()
        self.addCleanup(self.case.doCleanups)
        with mock.patch.dict(os.environ, CPK_OPERATIONS_TEST_DATABASE_URL=scoped):
            self.case.setUp()
        fixture = self.case.fixture if self.generation else self.case.fixture.fixture
        self.connection, self.database_url = fixture.connection, fixture.database_url
        self.context = self.case.context
        self.admin = PrincipalIdentity("standalone-test", "admin", PrincipalKind.OPERATOR)
        self.administration = operations.WorkspaceAdministrationPolicy("standalone-installation",
            (self.admin,), (self.admin, self.context.principal.identity))
        self.admin_context = self.administration.authorize(AuthenticatedPrincipal(self.admin))
        self.permissions().initialize(self.admin_context, idempotency_key=IdempotencyKey("standalone-initialize"))
        granted = self.permissions().grant(operations.GrantWorkspacePermissions(self.admin_context,
            "workspace-a", self.context.principal.identity, self.scopes, 0, IdempotencyKey("standalone-grant")))
        self.revision = granted.receipt.revision
        self.case.context = operations.WorkspaceContextResolver(self.case.unit_of_work,
            installation_id="standalone-installation").resolve(self.context.principal, "workspace-a")
        self.context = self.case.context
        self.policy = operations.CurrentWorkspaceEffectPolicy("standalone-installation",
            operations.ManagedRuntimeCapabilityPolicy("standalone-installation", ()))

    def permissions(self, factory=None):
        return operations.WorkspacePermissionCommandService(factory or self.case.unit_of_work,
            administration_policy=self.administration, clock=lambda: "2026-10-26T04:00:00Z",
            id_factory=lambda: uuid4().hex)

    def revoke(self, scope=None, factory=None):
        result = self.permissions(factory).revoke(operations.RevokeWorkspacePermissions(
            self.admin_context, "workspace-a", self.context.principal.identity,
            (scope or self.scopes[0],), self.revision, IdempotencyKey("revoke-current")))
        self.revision = result.receipt.revision
        return result

    def grant(self, scopes, key):
        result = self.permissions().grant(operations.GrantWorkspacePermissions(self.admin_context,
            "workspace-a", self.context.principal.identity, scopes, self.revision, IdempotencyKey(key)))
        self.revision = result.receipt.revision
        return result

    def require_interface(self):
        self.assertIn("permission_policy", inspect.signature(self.command_service).parameters,
            "standalone current permission interface is unavailable")

    def service(self, factory=None):
        self.require_interface()
        selected = self.case.unit_of_work
        if factory is not None:
            @contextmanager
            def selected():
                self.case.local.active = getattr(self.case.local, "active", 0) + 1
                try:
                    with factory() as uow:
                        yield uow
                finally:
                    self.case.local.active -= 1
        if self.generation:
            return self.command_service(selected, SimpleNamespace(generate=self.case.generate_provider),
                authorizer=self.case.authorizer, clock=lambda: generation_cases.NOW, permission_policy=self.policy)
        return self.command_service(selected, SimpleNamespace(store=self.case.store),
            authorizer=SimpleNamespace(authorize=self.case.authorize),
            clock=lambda: provision_cases.NOW, permission_policy=self.policy)

    def execute(self, factory=None):
        service = self.service(factory)
        return (service.generate(self.case.command()) if self.generation
            else service.provision(self.case.command(), SecretValue(provision_cases.VALUE)))

    def permission_lock(self, query, parameters):
        return ("FROM cpk_workspace_permissions" in query and "FOR UPDATE" in query
            and "workspace-a" in parameters and self.context.principal.identity.issuer in parameters
            and self.context.actor_id in parameters)

    def assert_scope_denied(self, scope):
        self.require_interface()
        self.revoke(scope)
        with self.assertRaises(self.denied):
            self.execute()
        self.assertEqual(self.case.calls, [])
        self.assertEqual(self.case.read().state.value, "refused")

    def test_primary_scope_is_current_at_initial_claim(self):
        self.assert_scope_denied(self.scopes[0])

    def test_secondary_scope_is_current_at_initial_claim(self):
        self.assert_scope_denied(self.scopes[1])

    def test_revoke_wins_real_claim_lock_with_no_provider_call(self):
        self.require_interface()
        first, second = self.opposing_commands(lambda factory: self.revoke(factory=factory),
            self.execute, self.permission_lock, connection_query=self.permission_lock)
        self.assertEqual(first.result().receipt.revision, 2)
        with self.assertRaises(self.denied):
            second.result()
        self.assertEqual(self.case.calls, [])
        self.assertEqual(self.case.read().state.value, "refused")

    def test_admission_wins_and_result_survives_revoke_but_registration_denies(self):
        self.require_interface()
        revoked = threading.Event()
        provider = self.case.behavior
        def finish_after_revoke(*args):
            self.assertTrue(revoked.wait(10), "permission revocation did not complete")
            return provider(*args)
        self.case.behavior = finish_after_revoke
        def revoke_and_signal(factory):
            try:
                return self.revoke(factory=factory)
            finally:
                revoked.set()
        first, second = self.opposing_commands(self.execute, revoke_and_signal,
            self.permission_lock, connection_query=self.permission_lock)
        self.assertEqual(second.result().receipt.revision, 2)
        with self.assertRaises(self.denied):
            first.result()
        self.assertEqual(len(self.case.calls), 1)
        attempt = self.case.read()
        self.assertEqual(attempt.state.value, "receipt-recorded")
        self.assertIsNotNone(attempt.receipt)
        if self.generation:
            with self.assertRaises(self.denied):
                self.service().reconcile(self.case.api.ReconcileDelegationKeyGenerationCommand(
                    self.context, "request-a"))
            self.assertEqual(self.case.read(), attempt)
            self.assertEqual(len(self.case.calls), 1)
        with self.case.unit_of_work() as uow:
            self.assertFalse(uow.stores.secret_references.list_history(
                "workspace-a", self.case.command().secret_reference))

    def test_registration_requires_real_context_bound_to_retained_actor(self):
        self.require_interface()
        provider = self.case.behavior
        def complete_after_revoke(*args):
            self.revoke()
            return provider(*args)
        self.case.behavior = complete_after_revoke
        with self.assertRaises(self.denied):
            self.execute()
        attempt = self.case.read()
        self.assertIsNotNone(attempt.receipt)
        self.grant(self.scopes, "restore")
        other = replace(self.context.principal.identity, issuer="other-issuer")
        self.administration = replace(self.administration,
            known_identities=(*self.administration.known_identities, other))
        self.permissions().grant(operations.GrantWorkspacePermissions(self.admin_context,
            "workspace-a", other, self.scopes, 0, IdempotencyKey("other-actor")))
        other_context = operations.WorkspaceContextResolver(self.case.unit_of_work,
            installation_id="standalone-installation").resolve(AuthenticatedPrincipal(other), "workspace-a")
        claim = (GenerationAttemptClaim("workspace-a", "request-a", attempt.fence, attempt.claim_kind)
            if self.generation else ProvisionAttemptClaim("workspace-a", "request-a"))
        service = self.admission_service(self.case.unit_of_work, permission_policy=self.policy)
        arguments = dict(actor=self.context.principal.identity, actor_scopes=self.scopes, admitted_at=self.now)
        for context in (None, other_context):
            with self.subTest(context="missing" if context is None else "other-issuer"):
                with self.assertRaises(self.denied):
                    service.admit(claim, **arguments, command_context=context)
                self.assertEqual(self.case.read(), attempt)
        admitted = service.admit(claim, **arguments, command_context=self.context)
        self.assertEqual(admitted.state.value, "admitted")
        self.assertEqual(len(self.case.calls), 1)


class PostgresGenerationPermissionTests(_StandalonePermissions, unittest.TestCase):
    generation = True
    case_type = generation_cases.DelegationKeyGenerationCommandTests
    command_service = DelegationKeyGenerationCommandService
    admission_service = GenerationAttemptAdmissionService
    scopes = generation_cases.SCOPES
    denied = GenerationAttemptAuthorizationDenied
    now = generation_cases.NOW

    def test_denied_explicit_reconciliation_preserves_uncertainty_and_fence(self):
        self.require_interface()
        def unavailable(grant):
            raise RuntimeError("controlled provider uncertainty")
        self.case.behavior = unavailable
        self.assertEqual(self.execute().state.value, "uncertain")
        before = self.case.read()
        self.revoke()
        with self.assertRaises(self.denied):
            self.service().reconcile(self.case.api.ReconcileDelegationKeyGenerationCommand(
                self.context, "request-a"))
        self.assertEqual(self.case.read(), before)
        self.assertEqual(len(self.case.calls), 1)

    def test_added_unrelated_permission_cannot_change_explicit_reconciliation_target(self):
        self.require_interface()
        def unavailable(grant):
            raise RuntimeError("controlled provider uncertainty")
        self.case.behavior = unavailable
        self.assertEqual(self.execute().state.value, "uncertain")
        original = self.case.read().intent
        self.grant((PolicyScope.DELEGATION_KEY_ACTIVATE,), "unrelated-grant")
        self.case.behavior = self.case.provider_result
        result = self.service().reconcile(self.case.api.ReconcileDelegationKeyGenerationCommand(
            self.context, "request-a"))
        self.assertEqual(result.state.value, "admitted")
        self.assertEqual(self.case.read().intent, original)
        self.assertEqual(self.case.calls, [original.grant, original.grant])


class PostgresProvisionPermissionTests(_StandalonePermissions, unittest.TestCase):
    generation = False
    case_type = provision_cases.SecretMaterialProvisionCommandTests
    command_service = SecretMaterialProvisionCommandService
    admission_service = ProvisionAttemptAdmissionService
    scopes = provision_cases.SCOPES
    denied = ProvisionAttemptAuthorizationDenied
    now = provision_cases.NOW
