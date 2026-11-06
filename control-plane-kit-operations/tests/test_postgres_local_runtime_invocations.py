"""Registered local dispatch requires admission even without secret preparation."""

import asyncio
import unittest

from control_plane_kit_operations.runtime_authorities import LocalDockerSocketAuthority
from tests.postgres_effect_attempt_reconciliation_fixture import FailIfObserver, UnitOfWorkLedger
from tests.test_postgres_prepared_runtime_invocations import _PreparedRuntimeInvocationFixture
from tests.test_postgres_runtime_invocation_carriage import (
    _ControlledPreparedRuntime, _PreparedRuntimeCarriageFixture,
)


class _RequiredInvocationRuntime(_ControlledPreparedRuntime):
    def _execute(self, request, invocations):
        if invocations is None:
            raise ValueError("runtime invocation admission is required")
        return super()._execute(request, invocations)


class PostgresLocalRuntimeInvocationTests(_PreparedRuntimeInvocationFixture, unittest.TestCase):
    def reset_start_truth(self, **options):
        self.engine = super().reset_start_truth(
            **{**options, "runtime_authority": LocalDockerSocketAuthority()})

    def setUp(self):
        super().setUp()
        self.ledger = UnitOfWorkLedger(self.unit_of_work)
        self.adapter = _RequiredInvocationRuntime(self)
        self.observer = FailIfObserver()

    def composition(self, **options):
        return _PreparedRuntimeCarriageFixture.composition(self, **options)

    def final(self, command):
        return _PreparedRuntimeCarriageFixture.actual_final(self, command)

    def test_local_runtime_with_no_secrets_receives_committed_invocation(self):
        owner, command = self.composition()
        result = asyncio.run(owner.execute_managed(command))
        self.assertEqual((result.status.value, result.effects_attempted), ("failed", 1))
        self.assertEqual(len(self.adapter.calls), 1)
        self.assertEqual(self.adapter.requests[0].secret_resolution_grants, ())
        self.assertEqual([row.kind.value for row in self.adapter.committed], ["prepared-runtime-dispatch"])
        marker, attempt, _ = self.final(command)
        self.assertEqual((marker.disposition.value, marker.outcome.value), ("admitted", "returned"))
        self.assertEqual(attempt.state.status.value, "failed")
        self.assertEqual(self.connection.execute("SELECT count(*) FROM cpk_secret_use_authorizations").fetchone()[0], 0)
        self.assertEqual(self.observer.calls, [])
        self.assertEqual(asyncio.run(owner.execute_managed(command)), result)
        self.assertEqual(len(self.adapter.calls), 1)

    def test_revocation_before_local_dispatch_prevents_provider_call(self):
        owner, command = self.composition()
        self.adapter.before_final = lambda _: self.revoke()
        result = asyncio.run(owner.execute_managed(command))
        self.assertEqual(result.status.value, "authorization-denied")
        self.assertEqual(self.adapter.calls, [])
        marker, attempt, _ = self.final(command)
        self.assertEqual(marker.disposition.value, "denied")
        self.assertIsNone(marker.outcome)
        self.assertEqual(attempt.state.status.value, "started")
        self.assertEqual(self.observer.calls, [])

    def test_unqualified_local_dispatch_stops_before_new_attempt(self):
        owner, command = self.composition(profile=False)
        result = asyncio.run(owner.execute_managed(command))
        self.assertEqual((result.status.value, result.effects_attempted), ("unsupported", 0))
        self.assertEqual(self.adapter.requests, [])
        self.assertEqual(self.connection.execute("SELECT count(*) FROM cpk_effect_attempts").fetchone()[0], 0)
