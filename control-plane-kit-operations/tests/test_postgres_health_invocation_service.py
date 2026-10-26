"""Fresh semantic admission follows a real acknowledged signed-health start."""

from dataclasses import replace
import unittest
from uuid import uuid4

import control_plane_kit_operations as operations
from control_plane_kit_operations.execution_invocations import (
    ExecutionInvocationDenied, ExecutionInvocationConflict, ExecutionInvocationExisting,
    ExecutionInvocationDisposition as Disposition,
    ExecutionInvocationKind as Kind, ExecutionInvocationOutcome as Outcome,
)
from control_plane_kit_operations.postgres import PostgresUnitOfWork
from control_plane_kit_operations.workflows import IdempotencyKey
from control_plane_kit_operations.records import ExecutionCommandResultRecord, CoordinatorStatus
from control_plane_kit_operations.secret_providers import SecretProviderRegistrationService, RevokeSecretProviderCommand
from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_core.planning import ManagementBootstrapStage, ObserveManagementBootstrap, ObserveNodeHealth
from tests.health_signing_authority_fixture import timestamp
from tests.test_postgres_health_effect_start_rollback import _CommitThenRaiseConnection
from tests.test_postgres_managed_effect_permissions import _ManagedPermissionStartFixture


class _HealthInvocationServiceFixture(_ManagedPermissionStartFixture):
    health_stage = None

    def health_context(self, **options):
        return super().health_context(**({"stage": self.health_stage} | options))

    def setUp(self):
        super().setUp()
        self.health_command = self.start_health_command(context=self.original)
        self.started = self.service().execute_health(self.health_command,
            command_context=self.bound, managed_target=self.selected)
        with self.unit_of_work() as uow:
            self.marker, = uow.stores.execution.command_invocations(self.bound.receipt).items
        self.assertIs(self.marker.kind, Kind.SIGNED_HEALTH_TRANSPORT)
        self.assertIs(self.marker.disposition, Disposition.PLANNED)
        self.assertEqual(self.marker.original_event_id, self.started.preparation.original_event_id)
        self.active_invocation_uows = 0
        self.calls = []

    def invocation_factory(self):
        test = self

        class ObservedUnitOfWork(PostgresUnitOfWork):
            def __enter__(self):
                result = super().__enter__()
                test.active_invocation_uows += 1
                return result

            def __exit__(self, *args):
                try:
                    return super().__exit__(*args)
                finally:
                    test.active_invocation_uows -= 1

        return ObservedUnitOfWork(self.connect)

    def bound_invocations(self, *, factory=None, clock=None, context=None, start=None, selection=None):
        service = getattr(operations, "ExecutionInvocationService", None)
        self.assertIsNotNone(service, "signed-health semantic invocation admission is unavailable")
        return service(factory or self.invocation_factory, permission_policy=self.policy,
            clock=clock or (lambda: timestamp(self.started.preparation.transit_grant.not_before)),
            health_receiver_decoders=self.health_receiver_decoders()).bind_health(
                command_context=context or self.bound, start=start or self.health_command, started=self.started,
                managed_target=selection or self.selected)

    def admission_time(self):
        return self.observed_time(timestamp(self.started.preparation.transit_grant.not_before))

    def retained(self, kind, authorization_id=None):
        with self.unit_of_work() as uow:
            return uow.stores.execution.command_invocation_for_slot(self.bound.receipt,
                activity_id=self.marker.activity_id, kind=kind, authorization_id=authorization_id)

    def revoke_workspace(self, factory=None):
        service = self.permission_service if factory is None else operations.WorkspacePermissionCommandService(
            factory, administration_policy=self.admin_policy, clock=self.clock, id_factory=lambda: uuid4().hex)
        return service.revoke(operations.RevokeWorkspacePermissions(self.admin_context,
            "workspace-a", self.worker, (self.operate,), 1, IdempotencyKey("revoke-workspace")))

    def secret_call(self, invocations):
        permit = invocations.admit("secret-resolution", authorization_id=self.started.preparation.transit_authorization_id)
        # A separate connection must see admission before the controlled call;
        # outcome recording cannot be the first durable write.
        with self.unit_of_work() as uow:
            rows = uow.stores.execution.command_invocations(self.bound.receipt).items
            admitted, = (row for row in rows if row.kind is Kind.SECRET_RESOLUTION and row.authorization_id == self.started.preparation.transit_authorization_id)
            self.assertEqual(admitted.command, self.bound.receipt)
            self.assertIs(admitted.disposition, Disposition.ADMITTED)
            self.assertIs(admitted.outcome, Outcome.PENDING)
            self.assertEqual(admitted.decision.permission_revision, 1)
            self.assertEqual(admitted.decision.assignment_id, self.selected.assignment_id)
            self.assertEqual(admitted.decision.assignment_revision, self.selected.revision)
        self.assertEqual(self.active_invocation_uows, 0, "capability called inside an admission transaction")
        self.calls.append("secret-resolution")
        invocations.record_outcome(permit, "returned")

class PostgresHealthInvocationServiceTests(_HealthInvocationServiceFixture, unittest.TestCase):
    def test_real_bound_secret_admission_commits_before_call_and_retains_classification(self):
        self.assertIs(type(self.health_command.start.intent.operation), ObserveNodeHealth)
        invocations = self.bound_invocations()
        with self.observed_time(timestamp(self.started.preparation.transit_grant.not_before)):
            self.secret_call(invocations)
        self.assertEqual(self.calls, ["secret-resolution"])
        self.assertEqual(self.active_invocation_uows, 0)
        with self.unit_of_work() as uow:
            rows = uow.stores.execution.command_invocations(self.bound.receipt).items
            secret, = (row for row in rows if row.kind is Kind.SECRET_RESOLUTION and row.authorization_id == self.started.preparation.transit_authorization_id)
            self.assertIs(secret.disposition, Disposition.ADMITTED)
            self.assertIs(secret.outcome, Outcome.RETURNED)
            self.assertEqual(secret.decision.permission_revision, 1)
            self.assertEqual(secret.decision.assignment_id, self.selected.assignment_id)
            self.assertEqual(secret.decision.assignment_revision, self.selected.revision)
            self.assertEqual(uow.stores.execution.command_invocation(
                self.marker.command, self.marker.invocation_id), self.marker)
            self.assertEqual(uow.stores.effect_attempts.get(self.started.start.attempt.state.identity),
                self.started.start.attempt)

    def test_each_selected_preparation_and_final_call_has_its_own_admission(self):
        invocations = self.bound_invocations()
        preparation = self.started.preparation
        slots = ((Kind.SECRET_RESOLUTION, preparation.transit_authorization_id),
            (Kind.SECRET_RESOLUTION, preparation.workload_authorization_id),
            (Kind.SIGNED_HEALTH_TRANSPORT, None))
        with self.admission_time():
            for kind, authorization in slots:
                permit = invocations.admit(kind.value, authorization_id=authorization)
                self.assertIs(self.retained(kind, authorization).outcome, Outcome.PENDING)
                self.assertEqual(self.active_invocation_uows, 0)
                self.calls.append((kind, authorization))
                invocations.record_outcome(permit, "returned")
        self.assertEqual(self.calls, list(slots))
        for kind, authorization in slots:
            self.assertIs(self.retained(kind, authorization).outcome, Outcome.RETURNED)
            with self.assertRaises(ExecutionInvocationExisting):
                invocations.admit(kind.value, authorization_id=authorization)
        with self.unit_of_work() as uow:
            self.assertEqual(uow.stores.effect_attempts.get(preparation.identity), self.started.start.attempt)
        self.assertEqual(self.connection.execute("SELECT count(*) FROM cpk_effect_attempt_outcomes").fetchone()[0], 0)

    def test_returned_preparation_does_not_authorize_next_secret_or_final_after_revoke(self):
        invocations = self.bound_invocations()
        with self.admission_time():
            self.secret_call(invocations)
            self.revoke_workspace()
            for kind, authorization in ((Kind.SECRET_RESOLUTION, self.started.preparation.workload_authorization_id),
                    (Kind.SIGNED_HEALTH_TRANSPORT, None)):
                with self.assertRaises(ExecutionInvocationDenied):
                    invocations.admit(kind.value, authorization_id=authorization)
                self.assertIs(self.retained(kind, authorization).disposition, Disposition.DENIED)
        self.assertEqual(self.calls, ["secret-resolution"])
        self.assertIs(self.retained(Kind.SECRET_RESOLUTION, self.started.preparation.transit_authorization_id).outcome, Outcome.RETURNED)
        denied = self.retained(Kind.SIGNED_HEALTH_TRANSPORT)
        self.permission_service.grant(operations.GrantWorkspacePermissions(self.admin_context,
            "workspace-a", self.worker, (self.operate,), 2, IdempotencyKey("restore-workspace")))
        with self.assertRaises(ExecutionInvocationDenied):
            invocations.admit("signed-health-transport")
        self.assertEqual(self.retained(Kind.SIGNED_HEALTH_TRANSPORT), denied)

    def test_stale_original_management_selection_cannot_borrow_a_regrant(self):
        invocations = self.bound_invocations()
        self.revoke()
        self.assignments().grant(operations.GrantManagedRuntimeAccess(self.admin_context,
            "workspace-a", self.runtime_id, self.runtime.authority_ref, self.runtime.registration_id,
            "installer-local-docker", 2, IdempotencyKey("restore-management")))
        with self.admission_time(), self.assertRaises(ExecutionInvocationDenied):
            self.secret_call(invocations)
        denied = self.retained(Kind.SECRET_RESOLUTION, self.started.preparation.transit_authorization_id)
        self.assertEqual(denied.decision.denial_code, "management-assignment-denied")
        self.assertIsNone(denied.decision.assignment_id)
        self.assertEqual(self.calls, [])
        self.assertEqual(self.retained(Kind.SIGNED_HEALTH_TRANSPORT), self.marker)

    def test_actual_commit_ack_loss_returns_no_permit_and_replay_cannot_dispatch(self):
        connections = []
        failure = RuntimeError("admission-ack-lost")
        def factory():
            def connect():
                value = _CommitThenRaiseConnection(self.connect(), failure)
                connections.append(value)
                return value
            return PostgresUnitOfWork(connect)
        invocations = self.bound_invocations(factory=factory)
        with self.admission_time(), self.assertRaises(ExecutionInvocationConflict):
            self.secret_call(invocations)
        self.assertEqual(self.calls, [])
        self.assertEqual([value.commits for value in connections], [1])
        admitted = self.retained(Kind.SECRET_RESOLUTION, self.started.preparation.transit_authorization_id)
        self.assertIs(admitted.disposition, Disposition.ADMITTED)
        self.assertIs(admitted.outcome, Outcome.PENDING)
        with self.assertRaises(ExecutionInvocationExisting):
            self.bound_invocations().admit("secret-resolution", authorization_id=self.started.preparation.transit_authorization_id)
        self.assertEqual(self.retained(Kind.SECRET_RESOLUTION, self.started.preparation.transit_authorization_id), admitted)

    def test_outcome_survives_revoke_and_completed_parent_and_replays_without_clock(self):
        reads = []
        def clock():
            self.assertEqual(reads, [], "completed classification read a new clock")
            reads.append(True)
            return timestamp(self.started.preparation.transit_grant.not_before)
        invocations = self.bound_invocations(clock=clock)
        with self.admission_time():
            permit = invocations.admit("secret-resolution", authorization_id=self.started.preparation.transit_authorization_id)
        self.revoke_workspace()
        with self.unit_of_work() as uow:
            reference = self.bound.receipt
            uow.stores.execution.lock_command_idempotency(reference.run_id, reference.idempotency_key)
            run = uow.stores.execution.get_run(reference.run_id)
            uow.stores.execution.complete_command_receipt(reference.run_id, reference.idempotency_key,
                intent_fingerprint=reference.intent_fingerprint,
                completed_at=timestamp(self.started.preparation.transit_grant.not_before),
                result=ExecutionCommandResultRecord(run, CoordinatorStatus.UNCERTAIN, 1, self.marker.activity_id))
            uow.commit()
        invocations.record_outcome(permit, "returned")
        retained = self.retained(Kind.SECRET_RESOLUTION, self.started.preparation.transit_authorization_id)
        invocations.record_outcome(permit, "returned")
        self.assertEqual(reads, [True])
        self.assertEqual(self.retained(Kind.SECRET_RESOLUTION, self.started.preparation.transit_authorization_id), retained)
        with self.assertRaises(ExecutionInvocationConflict):
            invocations.record_outcome(permit, "failed")

    def test_denial_commit_ack_loss_retains_denial_but_never_yields_a_permit(self):
        self.revoke_workspace()
        def factory():
            return PostgresUnitOfWork(lambda: _CommitThenRaiseConnection(
                self.connect(), RuntimeError("denial-ack-lost")))
        with self.admission_time(), self.assertRaises(ExecutionInvocationConflict):
            self.secret_call(self.bound_invocations(factory=factory))
        self.assertEqual(self.calls, [])
        denied = self.retained(Kind.SECRET_RESOLUTION, self.started.preparation.transit_authorization_id)
        self.assertIs(denied.disposition, Disposition.DENIED)
        with self.assertRaises(ExecutionInvocationDenied):
            self.bound_invocations().admit("secret-resolution", authorization_id=self.started.preparation.transit_authorization_id)
        self.assertEqual(self.retained(Kind.SECRET_RESOLUTION, self.started.preparation.transit_authorization_id), denied)

    def test_commit_request_rollback_leaves_only_original_marker_and_no_permit(self):
        commits = []
        def factory():
            uow = self.unit_of_work()
            def refuse():
                commits.append(True)
                raise RuntimeError("admission-commit-request-failed")
            uow.commit = refuse
            return uow
        with self.admission_time(), self.assertRaises(ExecutionInvocationConflict):
            self.secret_call(self.bound_invocations(factory=factory))
        self.assertEqual(commits, [True])
        self.assertEqual(self.calls, [])
        self.assertIsNone(self.retained(Kind.SECRET_RESOLUTION, self.started.preparation.transit_authorization_id))
        self.assertEqual(self.retained(Kind.SIGNED_HEALTH_TRANSPORT), self.marker)

    def test_workspace_denial_does_not_bypass_expired_lease_guard(self):
        self.revoke_workspace()
        with self.observed_time("2100-01-01T00:00:00Z"), self.assertRaises(ExecutionInvocationConflict):
            self.secret_call(self.bound_invocations())
        self.assertEqual(self.calls, [])
        self.assertIsNone(self.retained(Kind.SECRET_RESOLUTION, self.started.preparation.transit_authorization_id))

    def test_wrong_bound_permit_and_backward_clock_preserve_pending_without_redispatch(self):
        invocations = self.bound_invocations(clock=lambda: "2000-01-01T00:00:00Z")
        with self.admission_time():
            permit = invocations.admit("secret-resolution", authorization_id=self.started.preparation.transit_authorization_id)
        with self.assertRaises(ExecutionInvocationConflict):
            self.bound_invocations().record_outcome(permit, "returned")
        with self.assertRaises(ExecutionInvocationConflict):
            invocations.record_outcome(permit, "returned")
        self.assertIs(self.retained(Kind.SECRET_RESOLUTION, self.started.preparation.transit_authorization_id).outcome, Outcome.PENDING)
        with self.assertRaises(ExecutionInvocationExisting):
            invocations.admit("secret-resolution", authorization_id=self.started.preparation.transit_authorization_id)

    def test_missing_or_incongruent_profile_is_not_repaired_or_admitted(self):
        invocations = self.bound_invocations()
        self.connection.execute("DELETE FROM cpk_execution_command_invocations WHERE invocation_id=%s",
            (self.marker.invocation_id,))
        with self.assertRaises(ExecutionInvocationConflict):
            invocations.admit("secret-resolution", authorization_id=self.started.preparation.transit_authorization_id)
        self.assertIsNone(self.retained(Kind.SECRET_RESOLUTION, self.started.preparation.transit_authorization_id))
        wrong = replace(self.marker, target_fingerprint="f" * 64)
        with self.unit_of_work() as uow:
            uow.stores.execution.add_command_invocation(wrong)
            uow.commit()
        with self.assertRaises(ExecutionInvocationConflict):
            invocations.admit("secret-resolution", authorization_id=self.started.preparation.transit_authorization_id)
        self.assertIsNone(self.retained(Kind.SECRET_RESOLUTION, self.started.preparation.transit_authorization_id))
        self.assertEqual(self.retained(Kind.SIGNED_HEALTH_TRANSPORT), wrong)

    def test_other_secret_slot_and_changed_original_identity_cannot_borrow_binding(self):
        with self.assertRaises(ExecutionInvocationConflict):
            self.bound_invocations().admit("secret-resolution", authorization_id="suse_" + "f" * 64)
        original = self.original
        identity = replace(original.principal.identity, issuer="other-issuer")
        changed = replace(original, principal=replace(original.principal, identity=identity))
        changed_binding = replace(self.bound, original=changed)
        invocations = self.bound_invocations(context=changed_binding,
            start=replace(self.health_command, context=changed))
        with self.admission_time(), self.assertRaises(ExecutionInvocationConflict):
            invocations.admit("secret-resolution", authorization_id=self.started.preparation.transit_authorization_id)
        self.assertIsNone(self.retained(Kind.SECRET_RESOLUTION, self.started.preparation.transit_authorization_id))

    def test_provider_unavailability_is_not_fabricated_as_workspace_denial(self):
        invocations = self.bound_invocations()
        SecretProviderRegistrationService(self.unit_of_work).revoke_provider(RevokeSecretProviderCommand(
            "workspace-a", self.provider.provider_id, "operator-a", self.marker.created_at,
            (PolicyScope.SECRET_PROVIDER_REVOKE,)))
        with self.admission_time(), self.assertRaises(ExecutionInvocationConflict):
            self.secret_call(invocations)
        self.assertEqual(self.calls, [])
        self.assertIsNone(self.retained(Kind.SECRET_RESOLUTION, self.started.preparation.transit_authorization_id))
        self.assertEqual(self.retained(Kind.SIGNED_HEALTH_TRANSPORT), self.marker)

    @staticmethod
    def permission_lock(query, parameters):
        return "FROM cpk_workspace_permissions" in query and "FOR UPDATE" in query

    def race_revoke(self, *, management, admission_first):
        revoke = self.revoke if management else self.revoke_workspace
        lock = self.authority_lock if management else self.permission_lock
        def admit(factory):
            bound = self.bound_invocations(factory=factory)
            return bound, bound.admit("secret-resolution", authorization_id=self.started.preparation.transit_authorization_id)
        with self.admission_time():
            if admission_first:
                first, second = self.opposing_commands(admit, revoke, lock)
                invocations, permit = first.result()
                second.result()
                self.assertIs(self.retained(Kind.SECRET_RESOLUTION, self.started.preparation.transit_authorization_id).outcome, Outcome.PENDING)
                self.calls.append("secret-resolution")
                invocations.record_outcome(permit, "returned")
                self.assertIs(self.retained(Kind.SECRET_RESOLUTION, self.started.preparation.transit_authorization_id).outcome, Outcome.RETURNED)
            else:
                first, second = self.opposing_commands(revoke, admit, lock)
                first.result()
                with self.assertRaises(ExecutionInvocationDenied):
                    second.result()
                self.assertIs(self.retained(Kind.SECRET_RESOLUTION, self.started.preparation.transit_authorization_id).disposition, Disposition.DENIED)
        self.assertEqual(self.calls, ["secret-resolution"] if admission_first else [])

    def test_workspace_revoke_orders_before_admission(self):
        self.race_revoke(management=False, admission_first=False)

    def test_workspace_admission_orders_before_revoke_and_retains_result(self):
        self.race_revoke(management=False, admission_first=True)

    def test_management_revoke_orders_before_admission(self):
        self.race_revoke(management=True, admission_first=False)

    def test_management_admission_orders_before_revoke_and_retains_result(self):
        self.race_revoke(management=True, admission_first=True)

    def test_workspace_revoke_before_secret_admission_retains_denial_with_zero_calls(self):
        invocations = self.bound_invocations()
        self.permission_service.revoke(operations.RevokeWorkspacePermissions(self.admin_context,
            "workspace-a", self.worker, (self.operate,), 1, IdempotencyKey("revoke-before-secret")))
        with self.observed_time(timestamp(self.started.preparation.transit_grant.not_before)):
            with self.assertRaises(ExecutionInvocationDenied):
                self.secret_call(invocations)
        self.assertEqual(self.calls, [])
        self.assertEqual(self.active_invocation_uows, 0)
        with self.unit_of_work() as uow:
            rows = uow.stores.execution.command_invocations(self.bound.receipt).items
            secret, = (row for row in rows if row.kind is Kind.SECRET_RESOLUTION and row.authorization_id == self.started.preparation.transit_authorization_id)
            self.assertIs(secret.disposition, Disposition.DENIED)
            self.assertIsNone(secret.outcome)
            self.assertEqual(secret.decision.permission_revision, 2)
            self.assertIsNone(secret.decision.assignment_id)
            self.assertEqual(secret.decision.denial_code, "required-scope-denied")
            self.assertEqual(uow.stores.execution.command_invocation(
                self.marker.command, self.marker.invocation_id), self.marker)


class _NonlocalHealthInvocationCases:
    def test_endpoint_is_inapplicable_but_selected_secret_and_final_paths_remain_valid(self):
        operation = self.health_command.start.intent.operation
        if self.health_stage is None:
            self.assertIs(type(operation), ObserveNodeHealth)
        else:
            self.assertIs(type(operation), ObserveManagementBootstrap)
            self.assertIs(operation.stage, self.health_stage)
        invocations = self.bound_invocations()
        with self.assertRaises(ExecutionInvocationConflict):
            invocations.admit("endpoint-observation")
        self.assertIsNone(self.retained(Kind.ENDPOINT_OBSERVATION))
        self.assertEqual(self.retained(Kind.SIGNED_HEALTH_TRANSPORT), self.marker)
        self.assertEqual(self.active_invocation_uows, 0)
        with self.admission_time():
            for authorization in (self.started.preparation.transit_authorization_id,
                    self.started.preparation.workload_authorization_id):
                permit = invocations.admit("secret-resolution", authorization_id=authorization)
                self.assertIs(self.retained(Kind.SECRET_RESOLUTION, authorization).outcome, Outcome.PENDING)
                self.assertEqual(self.active_invocation_uows, 0)
                invocations.record_outcome(permit, "returned")
            permit = invocations.admit("signed-health-transport")
            self.assertIs(self.retained(Kind.SIGNED_HEALTH_TRANSPORT).outcome, Outcome.PENDING)
            self.assertEqual(self.active_invocation_uows, 0)
            invocations.record_outcome(permit, "returned")
        self.assertIs(self.retained(Kind.SIGNED_HEALTH_TRANSPORT).outcome, Outcome.RETURNED)
        self.assertIsNone(self.retained(Kind.ENDPOINT_OBSERVATION))


class PostgresPublicHealthInvocationEligibilityTests(
        _NonlocalHealthInvocationCases, _HealthInvocationServiceFixture, unittest.TestCase):
    health_stage = None


class PostgresAuthenticatedHealthInvocationEligibilityTests(
        _NonlocalHealthInvocationCases, _HealthInvocationServiceFixture, unittest.TestCase):
    health_stage = ManagementBootstrapStage.AUTHENTICATED_MANAGEMENT_PATH


class PostgresIngressHealthInvocationEligibilityTests(
        _NonlocalHealthInvocationCases, _HealthInvocationServiceFixture, unittest.TestCase):
    health_stage = ManagementBootstrapStage.GATEWAY_INGRESS_READY
