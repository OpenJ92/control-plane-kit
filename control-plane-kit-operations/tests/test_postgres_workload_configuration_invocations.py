"""Prepared configuration markers share the original bounded start transaction."""

import unittest
from unittest import mock

import control_plane_kit_operations as operations
from control_plane_kit_core.identity import AuthenticatedPrincipal, PrincipalIdentity, PrincipalKind
from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_core.secrets import SecretReference
from control_plane_kit_operations.effect_attempt_start import EffectAttemptStartConflict, NewlyStarted
from control_plane_kit_operations.execution_invocations import ExecutionInvocationKind
from control_plane_kit_operations.runtime_authorities import RemoteDockerTlsAuthority
from control_plane_kit_operations.workflows import IdempotencyKey
from tests.configuration_preparation_fixture import ConfigurationPreparationFixture
from tests.retained_receiver_read_bounds_fixture import conservative_suffix
from tests.test_postgres_current_effect_permissions import CurrentEffectPermissionFixture


class PostgresWorkloadConfigurationInvocationTests(ConfigurationPreparationFixture, unittest.TestCase):
    configuration_runtime_authority = RemoteDockerTlsAuthority("tcp://configuration.example.test:2376",
        SecretReference("secret://configuration/ca"), SecretReference("secret://configuration/cert"),
        SecretReference("secret://configuration/key"))
    permissions = CurrentEffectPermissionFixture.permissions
    policy = CurrentEffectPermissionFixture.policy
    public = CurrentEffectPermissionFixture.public
    binding = CurrentEffectPermissionFixture.binding
    fresh_start = CurrentEffectPermissionFixture.fresh_start
    clock = staticmethod(CurrentEffectPermissionFixture.clock)

    def seed_execution_request(self):
        from tests.receiver_scope_history_fixture import admit_fixture_plan
        admit_fixture_plan(self, requested_at="2026-08-15T03:59:00Z",
            actor_scopes=(PolicyScope.PLAN_EXECUTE, PolicyScope.RUNTIME_AUTHORITY_USE))

    def setUp(self):
        super().setUp()
        self.admin = PrincipalIdentity("issuer-a", "admin", PrincipalKind.OPERATOR)
        self.worker = PrincipalIdentity("issuer-a", "worker-a", PrincipalKind.WORKER)
        self.operate = PolicyScope.EXECUTION_OPERATE
        self.admin_policy = operations.WorkspaceAdministrationPolicy(
            "installation-a", (self.admin,), (self.admin, self.worker))
        self.admin_context = self.admin_policy.authorize(AuthenticatedPrincipal(self.admin))
        self.permissions().initialize(self.admin_context, idempotency_key=IdempotencyKey("initialize"))
        self.permissions().grant(operations.GrantWorkspacePermissions(self.admin_context, "workspace-a",
            self.worker, (self.operate,), 0, IdempotencyKey("grant")))
        self.original = operations.WorkspaceContextResolver(self.unit_of_work,
            installation_id="installation-a").resolve(AuthenticatedPrincipal(self.worker), "workspace-a")

    def workload_snapshot(self):
        return self.complete_start_snapshot(), self.connection.execute(
            "SELECT invocation_id,descriptor FROM cpk_execution_command_invocations ORDER BY invocation_id").fetchall(), self.connection.execute(
            "SELECT run_id,metadata FROM cpk_activity_runs WHERE run_id='run-a'").fetchall()

    def test_remote_configuration_marker_and_claims_commit_within_existing_suffix_bound(self):
        bound = self.binding()
        command = self.configuration_command()
        with conservative_suffix(self):
            started = self.fresh_start().execute(command, command_context=bound)
        self.assertIs(type(started), NewlyStarted)
        refs, claims = self.protection_rows()
        self.assertEqual(len(refs), len(command.intent.configuration_instances.instances))
        self.assertEqual(len(claims), len(refs))
        with self.unit_of_work() as uow:
            original = uow.stores.effect_attempt_intents.get(started.attempt.state.identity)
            marker = uow.stores.execution.command_invocation_for_slot(bound.receipt,
                activity_id="start-api", kind=ExecutionInvocationKind.PREPARED_RUNTIME_DISPATCH)
        self.assertEqual(original.intent, command.intent)
        self.assertEqual(original.original_start_event, started.attempt.original_start_event)
        self.assertEqual(marker.runtime_registration_id, self.configuration_runtime.registration_id)
        self.assertEqual(marker.disposition.value, "planned")
        self.assertEqual(marker.original_event_id, started.attempt.original_start_event.event_id)

    def test_capacity_refusal_in_prepared_suffix_rolls_back_original_claims_and_marker(self):
        from control_plane_kit_operations.postgres.configuration_evidence import _Capacity, _EvidenceRead
        bound = self.binding()
        command = self.configuration_command()
        before = self.workload_snapshot()
        reached = []
        def capacity_at_marker(read, sql, params, **limits):
            if sql.startswith("INSERT INTO cpk_execution_command_invocations"):
                # All original writes/claims precede this exact new suffix.
                reached.append(True)
                raise _Capacity
            return original_query(read, sql, params, **limits)
        with conservative_suffix(self):
            original_query = _EvidenceRead.query
            with mock.patch.object(_EvidenceRead, "query", capacity_at_marker):
                with self.assertRaises(EffectAttemptStartConflict):
                    self.fresh_start().execute(command, command_context=bound)
        self.assertEqual(reached, [True])
        self.assertEqual(self.workload_snapshot(), before)

    def test_captured_current_run_growth_refuses_without_unbounded_fallback(self):
        from psycopg.types.json import Jsonb
        from control_plane_kit_operations.postgres.configuration_evidence import _EvidenceRead
        bound = self.binding()
        command = self.configuration_command()
        before = self.workload_snapshot()
        original_query = _EvidenceRead.query
        changed = []
        def grow_after_capture(read, sql, params, **limits):
            if (not changed and sql.startswith("SELECT 1 FROM cpk_runtime_authorities")
                    and sql.endswith("FOR UPDATE")):
                changed.append(True)
                # Deliberate transport drift in the same transaction after the
                # current run was captured; it must not widen the issued bound.
                read.connection.execute("UPDATE cpk_activity_runs SET metadata=%s WHERE run_id='run-a'",
                    (Jsonb({"transport-growth": "x" * 4096}),))
            return original_query(read, sql, params, **limits)
        with mock.patch.object(_EvidenceRead, "query", grow_after_capture):
            with self.assertRaises(EffectAttemptStartConflict):
                self.fresh_start().execute(command, command_context=bound)
        self.assertEqual(changed, [True])
        self.assertEqual(self.workload_snapshot(), before)
