"""Fresh semantic admission follows a real acknowledged signed-health start."""

import unittest

import control_plane_kit_operations as operations
from control_plane_kit_operations.execution_invocations import (
    ExecutionInvocationDenied, ExecutionInvocationDisposition as Disposition,
    ExecutionInvocationKind as Kind, ExecutionInvocationOutcome as Outcome,
)
from control_plane_kit_operations.postgres import PostgresUnitOfWork
from control_plane_kit_operations.workflows import IdempotencyKey
from tests.health_signing_authority_fixture import timestamp
from tests.test_postgres_managed_effect_permissions import _ManagedPermissionStartFixture


class PostgresHealthInvocationServiceTests(_ManagedPermissionStartFixture, unittest.TestCase):
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

    def bound_invocations(self):
        service = getattr(operations, "ExecutionInvocationService", None)
        self.assertIsNotNone(service, "signed-health semantic invocation admission is unavailable")
        return service(self.invocation_factory, permission_policy=self.policy,
            clock=lambda: timestamp(self.started.preparation.transit_grant.not_before),
            health_receiver_decoders=self.health_receiver_decoders()).bind_health(
                command_context=self.bound, start=self.health_command, started=self.started,
                managed_target=self.selected)

    def endpoint_call(self, invocations):
        permit = invocations.admit("endpoint-observation")
        # A separate connection must see admission before the controlled call;
        # outcome recording cannot be the first durable write.
        with self.unit_of_work() as uow:
            rows = uow.stores.execution.command_invocations(self.bound.receipt).items
            admitted, = (row for row in rows if row.kind is Kind.ENDPOINT_OBSERVATION)
            self.assertEqual(admitted.command, self.bound.receipt)
            self.assertIs(admitted.disposition, Disposition.ADMITTED)
            self.assertIs(admitted.outcome, Outcome.PENDING)
            self.assertEqual(admitted.decision.permission_revision, 1)
            self.assertEqual(admitted.decision.assignment_id, self.selected.assignment_id)
            self.assertEqual(admitted.decision.assignment_revision, self.selected.revision)
        self.assertEqual(self.active_invocation_uows, 0, "capability called inside an admission transaction")
        self.calls.append("endpoint-observation")
        invocations.record_outcome(permit, "returned")

    def test_real_bound_endpoint_admission_commits_before_call_and_retains_classification(self):
        invocations = self.bound_invocations()
        with self.observed_time(timestamp(self.started.preparation.transit_grant.not_before)):
            self.endpoint_call(invocations)
        self.assertEqual(self.calls, ["endpoint-observation"])
        self.assertEqual(self.active_invocation_uows, 0)
        with self.unit_of_work() as uow:
            rows = uow.stores.execution.command_invocations(self.bound.receipt).items
            endpoint, = (row for row in rows if row.kind is Kind.ENDPOINT_OBSERVATION)
            self.assertIs(endpoint.disposition, Disposition.ADMITTED)
            self.assertIs(endpoint.outcome, Outcome.RETURNED)
            self.assertEqual(endpoint.decision.permission_revision, 1)
            self.assertEqual(endpoint.decision.assignment_id, self.selected.assignment_id)
            self.assertEqual(endpoint.decision.assignment_revision, self.selected.revision)
            self.assertEqual(uow.stores.execution.command_invocation(
                self.marker.command, self.marker.invocation_id), self.marker)
            self.assertEqual(uow.stores.effect_attempts.get(self.started.start.attempt.state.identity),
                self.started.start.attempt)

    def test_workspace_revoke_before_endpoint_admission_retains_denial_with_zero_calls(self):
        invocations = self.bound_invocations()
        self.permission_service.revoke(operations.RevokeWorkspacePermissions(self.admin_context,
            "workspace-a", self.worker, (self.operate,), 1, IdempotencyKey("revoke-before-endpoint")))
        with self.observed_time(timestamp(self.started.preparation.transit_grant.not_before)):
            with self.assertRaises(ExecutionInvocationDenied):
                self.endpoint_call(invocations)
        self.assertEqual(self.calls, [])
        self.assertEqual(self.active_invocation_uows, 0)
        with self.unit_of_work() as uow:
            rows = uow.stores.execution.command_invocations(self.bound.receipt).items
            endpoint, = (row for row in rows if row.kind is Kind.ENDPOINT_OBSERVATION)
            self.assertIs(endpoint.disposition, Disposition.DENIED)
            self.assertIsNone(endpoint.outcome)
            self.assertEqual(endpoint.decision.permission_revision, 2)
            self.assertIsNone(endpoint.decision.assignment_id)
            self.assertEqual(endpoint.decision.denial_code, "required-scope-denied")
            self.assertEqual(uow.stores.execution.command_invocation(
                self.marker.command, self.marker.invocation_id), self.marker)
