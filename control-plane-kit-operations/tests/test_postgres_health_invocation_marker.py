"""The real signed start owns its final-dispatch marker atomically."""

import hashlib
import unittest

import rfc8785

from control_plane_kit_operations.effect_attempt_start import ExistingAttempt, NewlyStarted
from control_plane_kit_operations.execution_invocations import (
    ExecutionInvocationDisposition as Disposition, ExecutionInvocationKind as Kind,
)
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
