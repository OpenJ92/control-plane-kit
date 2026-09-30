"""#1904 fresh claim/start/resume serialize before mutable execution truth."""

from datetime import datetime, timezone
import os
import unittest

from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_operations.execution_leases import ExecutionLeaseFence
from control_plane_kit_operations.lifecycle import (
    CancelActivityRun, ClaimAndOpenActivityRun, ExecutionLeaseDuration, ExecutionWorkerAuthority,
    PauseActivityRun, ResumeActivityRun, RunLifecycleCommandService, RunLifecycleError, StartActivityRun,
)
from control_plane_kit_operations.postgres.temporal import decode_postgres_timestamp
from control_plane_kit_operations.workflows import IdempotencyKey
from tests.lifecycle_lock_fixture import (
    LIFECYCLE_LOCK, REQUEST_LOCK, RUN_LOCK, SESSION_LOCK, WORKSPACE_LOCK,
)
from tests.receiver_execution_scope_fixture import ReceiverExecutionScopeFixture
from tests.receiver_fresh_permission_witness import FreshPermissionWitness
from tests.test_execution_admission import Sequence


class ReceiverFreshLifecyclePermissionTests(ReceiverExecutionScopeFixture, FreshPermissionWitness, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.database_url = os.environ["CPK_OPERATIONS_TEST_DATABASE_URL"]
        self.admit()
        self.authority = ExecutionWorkerAuthority("worker-a", (PolicyScope.EXECUTION_OPERATE,))

    def service(self, factory, *ids):
        return RunLifecycleCommandService(factory,
            clock=lambda: decode_postgres_timestamp(datetime.now(timezone.utc)),
            id_factory=Sequence(*ids))

    def assert_later_rows_free(self, *, run=False):
        self.assert_row_lockable(REQUEST_LOCK, ("execution-a",))
        if run:
            self.assert_row_lockable(RUN_LOCK, ("run-a",))
        self.assert_row_lockable(SESSION_LOCK, ("session-a",))
        self.assert_row_lockable(WORKSPACE_LOCK, ("workspace-a",))

    def test_fresh_claim_waits_for_lifecycle_before_request_and_session(self):
        command = ClaimAndOpenActivityRun("execution-a", self.authority,
            ExecutionLeaseDuration(600), IdempotencyKey("claim-lock"))
        with self.blocked_command(LIFECYCLE_LOCK, ("receiver-lifecycle:workspace-a",),
                lambda factory: self.service(factory, "run-a", "open-event", "open-action").execute(command)) as future:
            self.assert_later_rows_free()
            self.assert_advisory_available("operation-action:session-a:claim-lock", available=False)
        self.assertFalse(future.result(timeout=1).replayed)

    def test_fresh_start_waits_for_lifecycle_before_request_and_run(self):
        claimed = self.claim()
        command = StartActivityRun("run-a", self.authority,
            ExecutionLeaseFence("worker-a", claimed.request.claim.generation), IdempotencyKey("start-lock"))
        with self.blocked_command(LIFECYCLE_LOCK, ("receiver-lifecycle:workspace-a",),
                lambda factory: self.service(factory, "start-event", "start-action").execute(command)) as future:
            self.assert_later_rows_free(run=True)
        self.assertFalse(future.result(timeout=1).replayed)

    def test_fresh_resume_waits_for_lifecycle_before_request_and_run(self):
        claimed = self.claim()
        fence = ExecutionLeaseFence("worker-a", claimed.request.claim.generation)
        self.lifecycle("start-event", "start-action").execute(
            StartActivityRun("run-a", self.authority, fence, IdempotencyKey("start")))
        self.lifecycle("pause-event", "pause-action").execute(
            PauseActivityRun("run-a", self.authority, fence, IdempotencyKey("pause")))
        command = ResumeActivityRun("run-a", self.authority, fence, IdempotencyKey("resume-lock"))
        with self.blocked_command(LIFECYCLE_LOCK, ("receiver-lifecycle:workspace-a",),
                lambda factory: self.service(factory, "resume-event", "resume-action").execute(command)) as future:
            self.assert_later_rows_free(run=True)
        self.assertFalse(future.result(timeout=1).replayed)

    def test_original_claim_replay_does_not_take_fresh_lifecycle_permission(self):
        original = self.claim()
        command = ClaimAndOpenActivityRun("execution-a", self.authority,
            ExecutionLeaseDuration(600), IdempotencyKey("claim-a"))
        with self.blocked_command(REQUEST_LOCK, ("execution-a",),
                lambda factory: self.service(factory).execute(command)) as future:
            self.assert_advisory_available("receiver-lifecycle:workspace-a", available=True)
        replay = future.result(timeout=1)
        self.assertTrue(replay.replayed)
        self.assertEqual((replay.run, replay.event, replay.action),
            (original.run, original.event, original.action))

    def test_fresh_claim_rechecks_changed_pins_after_lifecycle_wait(self):
        command = ClaimAndOpenActivityRun("execution-a", self.authority,
            ExecutionLeaseDuration(600), IdempotencyKey("claim-recheck"))
        result = self.assert_rechecks_pins_while_waiting(lambda factory:
            self.service(factory, "run-a", "claim-event", "claim-action").execute(command), RunLifecycleError)
        self.assertFalse(result.replayed)

    def test_fresh_start_rechecks_changed_pins_after_lifecycle_wait(self):
        claimed = self.claim()
        command = StartActivityRun("run-a", self.authority, claimed.request.claim.fence, IdempotencyKey("start-recheck"))
        result = self.assert_rechecks_pins_while_waiting(lambda factory:
            self.service(factory, "start-event", "start-action").execute(command), RunLifecycleError)
        self.assertFalse(result.replayed)

    def test_fresh_resume_rechecks_changed_pins_after_lifecycle_wait(self):
        claimed = self.claim()
        fence = claimed.request.claim.fence
        self.lifecycle("start-event", "start-action").execute(StartActivityRun(
            "run-a", self.authority, fence, IdempotencyKey("start")))
        self.lifecycle("pause-event", "pause-action").execute(PauseActivityRun(
            "run-a", self.authority, fence, IdempotencyKey("pause")))
        command = ResumeActivityRun("run-a", self.authority, fence, IdempotencyKey("resume-recheck"))
        result = self.assert_rechecks_pins_while_waiting(lambda factory:
            self.service(factory, "resume-event", "resume-action").execute(command), RunLifecycleError)
        self.assertFalse(result.replayed)

    def test_start_rechecks_locator_state_after_cancellation_while_waiting(self):
        claimed = self.claim()
        command = StartActivityRun("run-a", self.authority, claimed.request.claim.fence, IdempotencyKey("start-recheck"))
        with self.blocked_command(LIFECYCLE_LOCK, ("receiver-lifecycle:workspace-a",), lambda factory:
                self.service(factory, "forbidden-start", "forbidden-action").execute(command)) as future:
            # Cancel is an evidence-only transition and therefore remains able
            # to settle this CLAIMED run while fresh Start waits for L.
            cancelled = self.lifecycle("cancel-event", "cancel-action").execute(CancelActivityRun(
                "run-a", self.authority, claimed.request.claim.fence, IdempotencyKey("cancel-recheck")))
            before = self.permission_truth()
        with self.assertRaises(RunLifecycleError):
            future.result(timeout=1)
        self.assertEqual(self.permission_truth(), before)
        with self.unit_of_work() as uow:
            self.assertEqual(uow.stores.execution.get_run("run-a"), cancelled.run)
