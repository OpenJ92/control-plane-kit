"""Current management grants bound to real pinned management start plans."""

from dataclasses import replace
import inspect
import os
import unittest
from uuid import uuid4

import psycopg

import control_plane_kit_operations as operations
from control_plane_kit_core.identity import AuthenticatedPrincipal, PrincipalIdentity, PrincipalKind
from control_plane_kit_core.planning import (
    ManagementBootstrapStage, ObserveManagementBootstrap, compile_graph_activity_plan,
)
from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_core.runtime_authority import RuntimeAuthorityReference
from control_plane_kit_core.runtime_effect_observation import runtime_effect_intent_fingerprint
from control_plane_kit_core.topology import validate_graph
from control_plane_kit_operations.effect_attempt_start import EffectAttemptStartDenied, ExistingAttempt, NewlyStarted
from control_plane_kit_operations.effect_attempt_start_interpreter import EffectAttemptStartService
from control_plane_kit_operations.postgres import PostgresUnitOfWork, install_schema
from control_plane_kit_operations.runtime_authorities import (
    LocalDockerSocketAuthority, RegisterRuntimeAuthorityCommand, RuntimeAuthorityRegistrationService,
)
from control_plane_kit_operations.runtime_management_targets import project_management_health_target
from control_plane_kit_operations.workflows import IdempotencyKey
from tests.health_effect_start_fixture import HEALTH_SCOPES, HealthEffectStartValues
from tests.lifecycle_lock_fixture import LifecycleLockFixture
from tests.postgres_health_effect_start_fixture import PostgresHealthEffectStartFixture
from tests import test_postgres_current_effect_permissions as permission_fixtures


class _ManagedPermissionStartFixture(PostgresHealthEffectStartFixture, LifecycleLockFixture):
    native = False
    binding = permission_fixtures.PostgresCurrentEffectPermissionTests.binding
    public = permission_fixtures.PostgresCurrentEffectPermissionTests.public

    def health_context(self, **options):
        _, original, current, desired, _ = super().health_context(**options)
        runtime_id = original.operation.target.runtime_id
        graph = desired.graph
        desired = validate_graph(replace(graph, runtimes={**graph.runtimes,
            runtime_id: replace(graph.runtimes[runtime_id],
                authority_ref=RuntimeAuthorityReference("managed-docker"))}))
        desired.require_valid()
        plan = compile_graph_activity_plan(current, desired)
        selected, = (item for item in plan.activities
            if type(item.operation) is type(original.operation)
            and item.operation.target.runtime_id == runtime_id
            and (item.operation.stage is original.operation.stage
                if type(original.operation) is ObserveManagementBootstrap
                else item.operation.node_id == original.operation.node_id))
        target = project_management_health_target(plan, selected.activity_id,
            selected.operation, current, desired)
        return plan, selected, current, desired, target

    def setUp(self):
        self.database_url = os.environ.get("CPK_OPERATIONS_TEST_DATABASE_URL")
        if not self.database_url:
            raise RuntimeError("Run ./control-plane-kit-operations/test.sh for Docker PostgreSQL")
        self.schema = "managed_effect_permissions_" + uuid4().hex
        self.connection = psycopg.connect(self.database_url, autocommit=True)
        self.addCleanup(self.connection.close)
        self.connection.execute(f'CREATE SCHEMA "{self.schema}"')
        self.addCleanup(self.connection.execute, f'DROP SCHEMA "{self.schema}" CASCADE')
        self.connection.execute(f'SET search_path TO "{self.schema}"')
        install_schema(self.connection)
        self._health_context_options = {}
        self.reset_start_truth()
        self.seed_ready_health()
        self.admin = PrincipalIdentity("issuer-a", "admin", PrincipalKind.OPERATOR)
        self.worker = PrincipalIdentity("issuer-a", "worker-a", PrincipalKind.WORKER)
        self.operate = PolicyScope.EXECUTION_OPERATE
        self.admin_policy = operations.WorkspaceAdministrationPolicy(
            "installation-a", (self.admin,), (self.admin, self.worker))
        self.admin_context = self.admin_policy.authorize(AuthenticatedPrincipal(self.admin))
        permissions = operations.WorkspacePermissionCommandService(self.unit_of_work,
            administration_policy=self.admin_policy, clock=self.clock, id_factory=lambda: uuid4().hex)
        permissions.initialize(self.admin_context, idempotency_key=IdempotencyKey("initialize"))
        permissions.grant(operations.GrantWorkspacePermissions(self.admin_context,
            "workspace-a", self.worker, (self.operate, *HEALTH_SCOPES), 0, IdempotencyKey("grant")))
        self.original = operations.WorkspaceContextResolver(self.unit_of_work,
            installation_id="installation-a").resolve(AuthenticatedPrincipal(self.worker), "workspace-a")
        self.bound = self.binding()
        self.capabilities = operations.ManagedRuntimeCapabilityPolicy("installation-a",
            (operations.ManagedRuntimeCapability("installer-local-docker"),))
        self.policy = operations.CurrentWorkspaceEffectPolicy("installation-a", self.capabilities)
        self.runtime_id = self.start_value.intent.operation.target.runtime_id
        self.runtime = self.register(self.start_value.intent.authority_ref)
        self.selected = self.grant(self.runtime, self.runtime_id, "management")

    def tearDown(self):
        pass  # Registered isolated-schema cleanups own all fixture state.

    def connect(self):
        return psycopg.connect(self.database_url, options=f"-c search_path={self.schema}")

    def unit_of_work(self):
        return PostgresUnitOfWork(self.connect)

    @staticmethod
    def clock():
        return "2026-10-25T20:00:00Z"

    def register(self, reference):
        return RuntimeAuthorityRegistrationService(self.unit_of_work).register(RegisterRuntimeAuthorityCommand(
            workspace_id="workspace-a", authority_ref=reference,
            runtime_kind=self.start_value.intent.runtime_kind, authority=LocalDockerSocketAuthority(),
            admitted_by=self.admin.subject_id, admitted_at=self.clock(),
            actor_scopes=(PolicyScope.RUNTIME_AUTHORITY_REGISTER,)))

    def assignments(self, factory=None):
        return operations.ManagedRuntimeAssignmentCommandService(factory or self.unit_of_work,
            administration_policy=self.admin_policy, capability_policy=self.capabilities,
            clock=self.clock, id_factory=lambda: uuid4().hex)

    def grant(self, authority, runtime_id, key):
        self.assignments().grant(operations.GrantManagedRuntimeAccess(self.admin_context,
            "workspace-a", runtime_id, authority.authority_ref, authority.registration_id,
            "installer-local-docker", 0, IdempotencyKey(key)))
        selected = operations.ManagedRuntimeSelectionService(self.unit_of_work,
            capability_policy=self.capabilities).select(
                workspace_id="workspace-a", runtime_id=runtime_id, authority=authority)
        self.assertIsNotNone(selected)
        return selected

    def revoke(self, factory=None):
        return self.assignments(factory).revoke(operations.RevokeManagedRuntimeAccess(
            self.admin_context, "workspace-a", self.runtime_id, self.runtime.authority_ref,
            1, IdempotencyKey("revoke-management")))

    def service(self, factory=None):
        return EffectAttemptStartService(factory or self.unit_of_work, id_factory=lambda: uuid4().hex,
            health_receiver_decoders=self.health_receiver_decoders(), permission_policy=self.policy)

    def admit(self, *, selection=None, start=None, factory=None):
        service = self.service(factory)
        method = service.execute if self.native else service.execute_health
        self.require_management_interface(method)
        start = self.start_value if start is None else start
        command = start if self.native else self.start_health_command(context=self.original, start=start)
        result = method(command, command_context=self.bound,
            managed_target=self.selected if selection is None else selection)
        return result if self.native else result.start

    def require_management_interface(self, method):
        self.assertIn("managed_target", inspect.signature(method).parameters,
            "current management assignment carrier is unavailable")

    def assert_missing_target_denied(self):
        before = self.health_snapshot()
        service = self.service()
        with self.assertRaises(EffectAttemptStartDenied):
            if self.native:
                service.execute(self.start_value, command_context=self.bound)
            else:
                service.execute_health(self.start_health_command(context=self.original), command_context=self.bound)
        self.assertEqual(self.health_snapshot(), before)

    @staticmethod
    def authority_lock(query, parameters):
        return "FROM cpk_runtime_authorities" in query and "FOR UPDATE" in query

    def assert_alternate_authority_denied(self):
        other_authority = self.register(RuntimeAuthorityReference("other-authority"))
        other = self.grant(other_authority, self.runtime_id, "other-authority")
        with self.unit_of_work() as uow:
            self.assertTrue(self.policy.evaluate_in_unit_of_work(uow, self.original,
                required_scopes=(self.operate,), managed_target=other).allowed)
            uow.commit()
        intent = replace(self.start_value.intent, authority_ref=other_authority.authority_ref)
        start = replace(self.start_value, intent=intent, transition=replace(self.start_value.transition,
            request_fingerprint=runtime_effect_intent_fingerprint(intent)))
        before = self.health_snapshot()
        with self.assertRaises(EffectAttemptStartDenied):
            self.admit(selection=other, start=start)
        self.assertEqual(self.health_snapshot(), before)
        self.assertIsInstance(self.admit(), NewlyStarted)


class PostgresSignedManagementPermissionTests(_ManagedPermissionStartFixture, unittest.TestCase):
    def test_signed_start_requires_current_management_selection(self):
        self.assert_missing_target_denied()
        self.assertIsInstance(self.admit(), NewlyStarted)

    def test_other_runtime_sharing_the_authority_cannot_authorize_this_plan(self):
        other = self.grant(self.runtime, "other-runtime", "other-runtime")
        with self.unit_of_work() as uow:
            self.assertTrue(self.policy.evaluate_in_unit_of_work(uow, self.original,
                required_scopes=(self.operate,), managed_target=other).allowed)
            uow.commit()
        before = self.health_snapshot()
        with self.assertRaises(EffectAttemptStartDenied):
            self.admit(selection=other)
        self.assertEqual(self.health_snapshot(), before)
        self.assertIsInstance(self.admit(), NewlyStarted)

    def test_consistent_alternate_intent_and_selection_must_still_match_pinned_graph(self):
        self.assert_alternate_authority_denied()


class PostgresNativeManagementPermissionTests(_ManagedPermissionStartFixture, unittest.TestCase):
    native = True

    def health_context(self, **options):
        _, _, current, desired, _ = super().health_context(
            stage=ManagementBootstrapStage.AUTHENTICATED_MANAGEMENT_PATH)
        plan = compile_graph_activity_plan(current, desired)
        selected, = (item for item in plan.activities if type(item.operation) is ObserveManagementBootstrap
            and item.operation.stage is ManagementBootstrapStage.CONNECTOR_CONNECTED)
        return plan, selected, current, desired, None

    def health_start_value(self, **options):
        start = HealthEffectStartValues.health_start_value(self, **options)
        runtime = self.health_graph.runtimes[start.intent.operation.target.runtime_id]
        intent = replace(start.intent, authority_ref=runtime.authority_ref, runtime_kind=runtime.kind)
        return replace(start, intent=intent, transition=replace(start.transition,
            request_fingerprint=runtime_effect_intent_fingerprint(intent)))

    def test_native_start_requires_current_management_selection(self):
        self.assert_missing_target_denied()
        self.assertIsInstance(self.admit(), NewlyStarted)

    def test_native_alternate_authority_pair_must_match_pinned_graph(self):
        self.assert_alternate_authority_denied()

    def test_management_revoke_wins_before_native_start_without_dispatch(self):
        self.require_management_interface(self.service().execute)
        calls = []
        before = self.health_snapshot()
        def admit_then_call(factory):
            result = self.admit(factory=factory)
            calls.append(result.attempt.state.identity)
            return result
        first, second = self.opposing_commands(
            lambda factory: self.revoke(factory), admit_then_call, self.authority_lock)
        self.assertEqual(first.result().receipt.resulting_assignment.revision, 2)
        with self.assertRaises(EffectAttemptStartDenied):
            second.result()
        self.assertEqual(calls, [])
        self.assertEqual(self.health_snapshot(), before)

    def test_native_admission_wins_and_exact_replay_survives_management_revoke(self):
        self.require_management_interface(self.service().execute)
        first, second = self.opposing_commands(
            lambda factory: self.admit(factory=factory), lambda factory: self.revoke(factory), self.authority_lock)
        started = first.result()
        self.assertIsInstance(started, NewlyStarted)
        self.assertEqual(second.result().receipt.resulting_assignment.revision, 2)
        replay = self.admit()
        self.assertIsInstance(replay, ExistingAttempt)
        self.assertEqual(replay.attempt, started.attempt)
