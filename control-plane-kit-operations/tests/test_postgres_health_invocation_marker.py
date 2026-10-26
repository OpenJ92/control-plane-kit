"""The real signed start owns its final-dispatch marker atomically."""

import hashlib
import unittest
from unittest import mock
from uuid import uuid4

import rfc8785

import control_plane_kit_operations as operations
from control_plane_kit_operations.effect_attempt_start import ExistingAttempt, NewlyStarted
from control_plane_kit_operations.effect_attempt_start_interpreter import EffectAttemptStartService
from control_plane_kit_operations.execution_invocations import (
    ExecutionInvocationDisposition as Disposition, ExecutionInvocationKind as Kind,
    signed_health_invocation_target_fingerprint,
)
from control_plane_kit_operations.postgres import PostgresExecutionStore, PostgresUnitOfWork
from control_plane_kit_operations.workflows import IdempotencyKey
from tests.test_postgres_health_effect_start_rollback import _CommitThenRaiseConnection
from tests.test_postgres_managed_effect_permissions import _ManagedPermissionStartFixture


class PostgresHealthInvocationMarkerTests(_ManagedPermissionStartFixture, unittest.TestCase):
    def markers(self):
        with self.unit_of_work() as uow:
            return uow.stores.execution.command_invocations(self.bound.receipt).items

    def test_real_signed_start_retains_one_exact_planned_final_marker_and_replays_it(self):
        started = self.admit()
        self.assertIsInstance(started, NewlyStarted)
        markers = self.markers()
        self.assertEqual(len(markers), 1, "signed-health start lacks its atomic final-dispatch marker")
        marker, = markers
        self.assertIs(marker.kind, Kind.SIGNED_HEALTH_TRANSPORT)
        self.assertIs(marker.disposition, Disposition.PLANNED)
        self.assertEqual(marker.command, self.bound.receipt)
        self.assertEqual(marker.workspace_id, self.original.workspace_id)
        self.assertEqual(marker.activity_id, started.attempt.state.identity.activity_id)
        self.assertEqual(marker.attempt, started.attempt.state.identity.attempt)
        self.assertEqual(marker.original_event_id, started.attempt.original_start_event.event_id)
        self.assertEqual(marker.request_fingerprint, started.attempt.state.request_fingerprint)
        self.assertEqual(marker.created_at, started.attempt.original_start_event.occurred_at)
        with self.unit_of_work() as uow:
            preparation = uow.stores.health_effect_preparations.get(started.attempt.state.identity)
        expected_target = hashlib.sha256(rfc8785.dumps(dict(
            domain="cpk.signed-health-final-target.v1", kind="signed-health-transport",
            request_fingerprint=preparation.request_fingerprint,
            health_request_digest=preparation.request_digest.value,
            transit_key_registration_id=preparation.transit_key_registration_id,
            workload_key_registration_id=preparation.workload_key_registration_id,
            transit_authorization_id=preparation.transit_authorization_id,
            workload_authorization_id=preparation.workload_authorization_id,
        ))).hexdigest()
        self.assertEqual(marker.target_fingerprint, expected_target)
        self.assertIsNone(marker.decision)
        self.assertIsNone(marker.decision_at)
        self.assertIsNone(marker.outcome)
        before = self.health_snapshot()
        self.assertEqual(self.admit(), ExistingAttempt(started.attempt))
        self.assertEqual(self.markers(), markers)
        self.assertEqual(self.health_snapshot(), before)

    def test_marker_write_and_commit_request_failure_roll_back_all_start_facts(self):
        before = self.health_snapshot()
        failure = RuntimeError("marker-transaction-failure")
        original = PostgresExecutionStore.add_command_invocation

        def write_then_fail(store, record):
            original(store, record)
            raise failure

        with mock.patch.object(PostgresExecutionStore, "add_command_invocation", write_then_fail):
            with self.assertRaises(RuntimeError) as caught:
                self.admit()
        self.assertIs(caught.exception, failure)
        self.assertEqual(self.health_snapshot(), before)
        self.assertEqual(self.markers(), ())

        def refuse_commit():
            uow = self.unit_of_work()
            uow.commit = mock.Mock(side_effect=failure)
            return uow

        with self.assertRaises(RuntimeError) as caught:
            self.admit(factory=refuse_commit)
        self.assertIs(caught.exception, failure)
        self.assertEqual(self.health_snapshot(), before)
        self.assertEqual(self.markers(), ())

    def test_lost_commit_ack_retains_marker_and_attempt_but_returns_no_new_start(self):
        failure = RuntimeError("marker-commit-acknowledgement-lost")
        connections = []

        def factory():
            def connect():
                connection = _CommitThenRaiseConnection(self.connect(), failure)
                connections.append(connection)
                return connection
            return PostgresUnitOfWork(connect)

        with self.assertRaises(RuntimeError) as caught:
            self.admit(factory=factory)
        self.assertIs(caught.exception, failure)
        self.assertEqual([connection.commits for connection in connections], [1])
        marker, = self.markers()
        with self.unit_of_work() as uow:
            attempt = uow.stores.effect_attempts.get(self.start_value.transition.identity)
            preparation = uow.stores.health_effect_preparations.get(attempt.state.identity)
        self.assertEqual(marker.original_event_id, attempt.original_start_event.event_id)
        self.assertEqual(marker.target_fingerprint, signed_health_invocation_target_fingerprint(preparation))
        self.assertIs(marker.disposition, Disposition.PLANNED)
        before = self.health_snapshot()
        self.assertEqual(self.admit(), ExistingAttempt(attempt))
        self.assertEqual(self.markers(), (marker,))
        self.assertEqual(self.health_snapshot(), before)

    def test_historical_marker_identity_survives_revocation_and_newer_assignment(self):
        started = self.admit()
        marker, = self.markers()
        self.revoke()
        with self.unit_of_work() as uow:
            preparation = uow.stores.health_effect_preparations.get(started.attempt.state.identity)
        self.assertEqual(signed_health_invocation_target_fingerprint(preparation), marker.target_fingerprint)
        self.assertEqual(self.admit(), ExistingAttempt(started.attempt))
        self.assignments().grant(operations.GrantManagedRuntimeAccess(self.admin_context,
            "workspace-a", self.runtime_id, self.runtime.authority_ref, self.runtime.registration_id,
            "installer-local-docker", 2, IdempotencyKey("regrant-management")))
        self.assertEqual(self.markers(), (marker,))
        self.assertEqual(signed_health_invocation_target_fingerprint(preparation), marker.target_fingerprint)
        self.assertEqual(self.admit(), ExistingAttempt(started.attempt))

    def test_replay_never_upgrades_a_legacy_start_to_the_new_marker_profile(self):
        legacy = EffectAttemptStartService(self.unit_of_work, id_factory=lambda: uuid4().hex,
            health_receiver_decoders=self.health_receiver_decoders())
        started = legacy.execute_health(self.start_health_command(context=self.original)).start
        self.assertIsInstance(started, NewlyStarted)
        self.assertEqual(self.markers(), ())
        self.assertEqual(self.admit(), ExistingAttempt(started.attempt))
        self.assertEqual(self.markers(), ())

    def test_replay_does_not_backfill_a_deleted_new_profile_marker(self):
        started = self.admit()
        marker, = self.markers()
        self.connection.execute("DELETE FROM cpk_execution_command_invocations WHERE invocation_id=%s",
            (marker.invocation_id,))
        before = self.health_snapshot()
        self.assertEqual(self.admit(), ExistingAttempt(started.attempt))
        self.assertEqual(self.markers(), ())
        self.assertEqual(self.health_snapshot(), before)
