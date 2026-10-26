"""#2039 current permission at the existing first-attempt transaction."""

from dataclasses import replace
import os
import unittest
from uuid import uuid4

import psycopg

import control_plane_kit_operations as operations
from control_plane_kit_core.identity import (
    AuthenticatedPrincipal, PrincipalIdentity, PrincipalKind, WorkspaceGrant,
)
from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_core.runtime_effects import RuntimeEffectResult
from control_plane_kit_operations import records
from control_plane_kit_operations.effect_attempt_fold import FoldEffectAttempt
from control_plane_kit_operations.effect_attempt_fold_interpreter import EffectAttemptFoldService
from control_plane_kit_operations.effect_attempt_start import EffectAttemptStartDenied, NewlyStarted
from control_plane_kit_operations.effect_attempt_start_interpreter import EffectAttemptStartService
from control_plane_kit_operations.effect_outcome_evidence import (
    ExecutionEffectOutcome, effect_outcome_failure, effect_outcome_transition,
)
from control_plane_kit_operations.postgres import PostgresUnitOfWork, install_schema
from control_plane_kit_operations.workflows import IdempotencyKey
from tests.lifecycle_lock_fixture import LifecycleLockFixture
from tests.postgres_effect_attempt_start_fixture import PostgresEffectAttemptStartFixture


class PostgresCurrentEffectPermissionTests(
    PostgresEffectAttemptStartFixture, LifecycleLockFixture, unittest.TestCase,
):
    def setUp(self):
        self.database_url = os.environ.get("CPK_OPERATIONS_TEST_DATABASE_URL")
        if not self.database_url:
            raise RuntimeError("Run ./control-plane-kit-operations/test.sh for Docker PostgreSQL")
        self.schema = "current_effect_permissions_" + uuid4().hex
        self.connection = psycopg.connect(self.database_url, autocommit=True)
        self.addCleanup(self.connection.close)
        self.connection.execute(f'CREATE SCHEMA "{self.schema}"')
        self.addCleanup(self.connection.execute, f'DROP SCHEMA "{self.schema}" CASCADE')
        self.connection.execute(f'SET search_path TO "{self.schema}"')
        install_schema(self.connection)
        # Reuse lifecycle-coherent existing owner truth before any new C API.
        self.reset_start_truth()
        self.admin = PrincipalIdentity("issuer-a", "admin", PrincipalKind.OPERATOR)
        self.worker = PrincipalIdentity("issuer-a", "worker-a", PrincipalKind.WORKER)
        self.operate = PolicyScope.EXECUTION_OPERATE
        self.admin_policy = operations.WorkspaceAdministrationPolicy(
            "installation-a", (self.admin,), (self.admin, self.worker))
        self.admin_context = self.admin_policy.authorize(AuthenticatedPrincipal(self.admin))
        self.permissions().initialize(self.admin_context, idempotency_key=IdempotencyKey("initialize"))
        self.permissions().grant(operations.GrantWorkspacePermissions(
            self.admin_context, "workspace-a", self.worker, (self.operate,), 0, IdempotencyKey("grant")))
        self.original = operations.WorkspaceContextResolver(
            self.unit_of_work, installation_id="installation-a",
        ).resolve(AuthenticatedPrincipal(self.worker), "workspace-a")

    def tearDown(self):
        # The isolated schema's registered cleanups own these rows and connection.
        pass

    def connect(self):
        return psycopg.connect(self.database_url, options=f"-c search_path={self.schema}")

    def unit_of_work(self):
        return PostgresUnitOfWork(self.connect)

    @staticmethod
    def clock():
        return "2026-10-25T20:00:00Z"

    def public(self, name):
        value = getattr(operations, name, None)
        self.assertIsNotNone(value, "current effect permission interface is unavailable: " + name)
        return value

    def permissions(self, factory=None):
        return operations.WorkspacePermissionCommandService(
            factory or self.unit_of_work, administration_policy=self.admin_policy,
            clock=self.clock, id_factory=lambda: uuid4().hex)

    def revoke(self, factory=None):
        return self.permissions(factory).revoke(operations.RevokeWorkspacePermissions(
            self.admin_context, "workspace-a", self.worker, (self.operate,), 1, IdempotencyKey("revoke")))

    def policy(self):
        return self.public("CurrentWorkspaceEffectPolicy")(
            installation_id="installation-a",
            capability_policy=operations.ManagedRuntimeCapabilityPolicy("installation-a", ()))

    def binding(self, *, original=None, key="execute-a"):
        original = original or self.original
        intent = records.ManagedExecutionCommandIntent.from_context(original)
        with self.unit_of_work() as uow:
            run = uow.stores.execution.get_run("run-a")
            fingerprint = records.execution_command_intent_fingerprint(
                run_id="run-a", worker_id="worker-a", authority_scopes=(self.operate,),
                claim_generation=7, max_effects=1, managed_intent=intent)
            receipt = records.ExecutionCommandReceiptRecord(
                run_id="run-a", idempotency_key=key, intent_fingerprint=fingerprint,
                worker_id="worker-a", authority_scopes=(self.operate,), claim_generation=7,
                max_effects=1, admitted_at=self.clock(), initial_run=run, managed_intent=intent)
            uow.stores.execution.add_command_receipt(receipt)
            uow.commit()
        reference = self.public("ExecutionCommandReference")("run-a", key, fingerprint)
        return self.public("EffectCommandContext")(original, reference)

    def fresh_start(self, factory=None):
        return EffectAttemptStartService(factory or self.unit_of_work,
            id_factory=lambda: uuid4().hex, permission_policy=self.policy())

    @staticmethod
    def permission_lock(query, parameters):
        return "FROM cpk_workspace_permissions" in query and "FOR UPDATE" in query

    def test_shared_decision_uses_original_context_without_execution_receipt(self):
        with self.unit_of_work() as uow:
            decision = self.policy().evaluate_in_unit_of_work(
                uow, self.original, required_scopes=(self.operate,))
            self.assertTrue(decision.allowed)
            self.assertEqual(decision.permission_revision, 1)
            self.assertEqual(decision.effective_scopes, (self.operate,))
            uow.commit()
        self.assertEqual(self.connection.execute(
            "SELECT count(*) FROM cpk_execution_command_receipts").fetchone()[0], 0)

    def test_later_grant_cannot_expand_the_original_scope_ceiling(self):
        later = PolicyScope.SECRET_PROVIDER_USE
        self.permissions().grant(operations.GrantWorkspacePermissions(
            self.admin_context, "workspace-a", self.worker, (later,), 1, IdempotencyKey("later-grant")))
        with self.unit_of_work() as uow:
            decision = self.policy().evaluate_in_unit_of_work(
                uow, self.original, required_scopes=(self.operate, later))
            self.assertFalse(decision.allowed)
            self.assertEqual(decision.permission_revision, 2)
            self.assertNotIn(later, decision.effective_scopes)
            uow.commit()

    def test_same_subject_wrong_issuer_or_kind_has_no_current_permission(self):
        for identity in (replace(self.worker, issuer="issuer-b"),
                         replace(self.worker, kind=PrincipalKind.OPERATOR)):
            with self.subTest(identity=identity):
                original = AuthenticatedPrincipal(identity, (
                    WorkspaceGrant("workspace-a", (self.operate,)),
                )).command_context("workspace-a")
                with self.unit_of_work() as uow:
                    decision = self.policy().evaluate_in_unit_of_work(
                        uow, original, required_scopes=(self.operate,))
                    self.assertFalse(decision.allowed)
                    self.assertIsNone(decision.permission_revision)
                    uow.commit()

    def test_dynamic_new_start_requires_the_exact_trusted_receipt_binding(self):
        binding = self.binding()
        command = self.start_command()
        other = replace(self.worker, issuer="issuer-b")
        self.admin_policy = replace(self.admin_policy,
            known_identities=(*self.admin_policy.known_identities, other))
        self.permissions().grant(operations.GrantWorkspacePermissions(
            self.admin_context, "workspace-a", other, (self.operate,), 0, IdempotencyKey("grant-other")))
        substituted = operations.WorkspaceContextResolver(
            self.unit_of_work, installation_id="installation-a",
        ).resolve(AuthenticatedPrincipal(other), "workspace-a")
        with self.unit_of_work() as uow:
            admitted = self.policy().evaluate_in_unit_of_work(
                uow, substituted, required_scopes=(self.operate,))
            self.assertTrue(admitted.allowed)
            uow.commit()
        before = self.attempt_snapshot()
        with self.assertRaises(EffectAttemptStartDenied):
            self.fresh_start().execute(command)
        with self.assertRaises(EffectAttemptStartDenied):
            self.fresh_start().execute(command, command_context=replace(binding, original=substituted))
        missing = replace(binding, receipt=replace(binding.receipt, idempotency_key="missing-receipt"))
        with self.assertRaises(EffectAttemptStartDenied):
            self.fresh_start().execute(command, command_context=missing)
        self.assertEqual(self.attempt_snapshot(), before)
        started = self.fresh_start().execute(command, command_context=binding)
        self.assertIsInstance(started, NewlyStarted)
        self.assertEqual(started.attempt.state.identity, command.transition.identity)

    def test_revocation_wins_real_admission_lock_and_prevents_dispatch(self):
        binding = self.binding()
        command = self.start_command()
        calls = []

        def admit_then_call(factory):
            started = self.fresh_start(factory).execute(command, command_context=binding)
            calls.append(started.attempt.state.identity)
            return started

        first, second = self.opposing_commands(
            lambda factory: self.revoke(factory), admit_then_call, self.permission_lock)
        self.assertEqual(first.result().receipt.revision, 2)
        with self.assertRaises(EffectAttemptStartDenied):
            second.result()
        self.assertEqual(calls, [])
        self.assertEqual(self.connection.execute("SELECT count(*) FROM cpk_effect_attempts").fetchone()[0], 0)

    def test_admission_wins_then_owned_result_can_fold_after_revocation(self):
        binding = self.binding()
        command = self.start_command()
        first, second = self.opposing_commands(
            lambda factory: self.fresh_start(factory).execute(command, command_context=binding),
            lambda factory: self.revoke(factory), self.permission_lock)
        started = first.result()
        self.assertIsInstance(started, NewlyStarted)
        self.assertEqual(second.result().receipt.revision, 2)
        # The original in-flight caller still holds a real typed provider result.
        # This is completion, not a second effect admission or historical replay.
        outcome = ExecutionEffectOutcome(started.attempt.state.identity,
            started.attempt.state.request_fingerprint,
            RuntimeEffectResult.succeeded(started.attempt.original_start_event.event_id))
        folded = EffectAttemptFoldService(self.unit_of_work, id_factory=lambda: uuid4().hex).execute(
            FoldEffectAttempt(command.request_id, effect_outcome_transition(outcome),
                command.authority, command.fence, effect_outcome_failure(outcome), outcome))
        self.assertEqual(folded.attempt.state.status.value, "succeeded")
        with self.unit_of_work() as uow:
            retained = uow.stores.effect_outcomes.get(started.attempt.state.identity,
                folded.attempt.latest_transition_event.event_id)
            self.assertEqual(retained.outcome, outcome)
            denied = self.policy().evaluate_in_unit_of_work(
                uow, self.original, required_scopes=(self.operate,))
            self.assertFalse(denied.allowed)
            uow.commit()
