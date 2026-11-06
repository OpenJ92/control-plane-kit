"""#1904 public affecting writers cannot turn a lifecycle guard into permission."""

from dataclasses import replace
from datetime import datetime, timezone
import unittest

from control_plane_kit_core.operations import ActivityRunStatus
from control_plane_kit_core.planning import NodeTarget, WaitForHealthy
from control_plane_kit_operations.execution_leases import ExecutionLeaseFence
from control_plane_kit_operations.postgres.temporal import decode_postgres_timestamp
from control_plane_kit_operations.receiver_execution_scopes import ReceiverScopeUnavailable
from control_plane_kit_operations.records import ActivityRunRecord, AdmittedRun, RetryIdentity
from tests.receiver_execution_scope_fixture import ReceiverExecutionScopeFixture


class ReceiverDirectExecutionPermissionTests(ReceiverExecutionScopeFixture, unittest.TestCase):
    def test_bare_affecting_claim_refuses_even_with_lifecycle_guard(self):
        admitted = self.admit()
        with self.unit_of_work() as uow:
            uow.stores.graphs.lock_receiver_lifecycle("workspace-a")
            with self.assertRaises(ReceiverScopeUnavailable):
                uow.stores.execution.claim_request("execution-a", "worker-a", 600)
            self.assertEqual(uow.stores.execution.get_request("execution-a"), admitted.request)
            self.assertEqual(uow.stores.execution.runs_for_request("execution-a"), ())

    def test_bare_affecting_lease_rotation_refuses_even_with_guard_and_exact_fence(self):
        self.admit()
        claimed = self.claim()
        with self.unit_of_work() as uow:
            uow.stores.graphs.lock_receiver_lifecycle("workspace-a")
            with self.assertRaises(ReceiverScopeUnavailable):
                uow.stores.execution.rotate_request_claim("execution-a",
                    expected_fence=claimed.request.claim.fence,
                    replacement_fence=ExecutionLeaseFence("worker-a", claimed.request.claim.generation + 1),
                    observed_at=decode_postgres_timestamp(datetime.now(timezone.utc)),
                    lease_duration_seconds=600)
            self.assertEqual(uow.stores.execution.get_request("execution-a"), claimed.request)

    def test_bare_affecting_run_refuses_with_real_request_and_guard(self):
        admitted = self.admit()
        run = ActivityRunRecord("bare-run", admitted.request.identity.plan_id,
            AdmittedRun("execution-a"), RetryIdentity(1), ActivityRunStatus.CLAIMED,
            decode_postgres_timestamp(datetime.now(timezone.utc)))
        with self.unit_of_work() as uow:
            uow.stores.graphs.lock_receiver_lifecycle("workspace-a")
            with self.assertRaises(ReceiverScopeUnavailable):
                uow.stores.execution.add_run(run)
            self.assertEqual(uow.stores.execution.runs_for_request("execution-a"), ())

    def test_bare_enabling_run_cas_refuses_without_changing_original_run(self):
        self.admit()
        claimed = self.claim()
        with self.unit_of_work() as uow:
            uow.stores.graphs.lock_receiver_lifecycle("workspace-a")
            with self.assertRaises(ReceiverScopeUnavailable):
                uow.stores.execution.compare_and_set_run_status("run-a",
                    expected=ActivityRunStatus.CLAIMED, replacement=ActivityRunStatus.RUNNING,
                    started_at=decode_postgres_timestamp(datetime.now(timezone.utc)))
            self.assertEqual(uow.stores.execution.get_run("run-a"), claimed.run)

    def test_nonaffecting_direct_claim_run_and_enabling_cas_remain_supported(self):
        admitted = self.admit_operations("observation", WaitForHealthy(NodeTarget("app")))
        self.assertEqual(self.scope_header("execution-observation")[0], 0)
        with self.unit_of_work() as uow:
            request = uow.stores.execution.claim_request("execution-observation", "worker-a", 600)
            self.assertIsNotNone(request.claim)
            run = ActivityRunRecord("observation-run", admitted.request.identity.plan_id,
                AdmittedRun(request.identity.request_id), RetryIdentity(1), ActivityRunStatus.CLAIMED,
                request.claim.claimed_at)
            self.assertEqual(uow.stores.execution.add_run(run), run)
            self.assertEqual(uow.stores.execution.compare_and_set_run_status(run.run_id,
                expected=ActivityRunStatus.CLAIMED, replacement=ActivityRunStatus.RUNNING,
                started_at=request.claim.claimed_at),
                replace(run, status=ActivityRunStatus.RUNNING, started_at=request.claim.claimed_at))
            rotated = uow.stores.execution.rotate_request_claim(request.identity.request_id,
                expected_fence=request.claim.fence,
                replacement_fence=ExecutionLeaseFence("worker-a", request.claim.generation + 1),
                observed_at=request.claim.claimed_at, lease_duration_seconds=600)
            self.assertEqual(rotated.claim.generation, request.claim.generation + 1)
            self.assertEqual(rotated.identity, request.identity)
            uow.commit()
