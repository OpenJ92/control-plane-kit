"""#1896 nested retirement publication joins the outer lifecycle prefix."""
import unittest

from control_plane_kit_operations.gateway_key_rotation_retirement import (
    GatewayKeyRotationRetirementProjectionService, PublishGatewayKeyRotationRetirementProjection,
)
from control_plane_kit_operations.workflows import OperationCommandService, StartOperationSession, IdempotencyKey
from tests.gateway_rotation_retirement_fixture import GatewayRotationRetirementFixture
from tests.lifecycle_lock_fixture import LifecycleLockFixture, LIFECYCLE_LOCK, SESSION_LOCK, WORKSPACE_LOCK


class PostgresLifecycleRetirementLockTests(
    LifecycleLockFixture, GatewayRotationRetirementFixture, unittest.TestCase,
):
    def test_outer_retirement_preparation_takes_lifecycle_before_session(self):
        ids = iter(("retirement-lock-session", "retirement-session-action"))
        session = OperationCommandService(self.unit_of_work,
            clock=lambda: "2026-08-02T03:05:00Z", id_factory=lambda: next(ids)).execute(
                StartOperationSession("workspace-a", "operator-a", "Retirement lock law",
                    IdempotencyKey("retirement-session"))).session
        command = PublishGatewayKeyRotationRetirementProjection(
            self.rotation_id, session.session_id, "operator-a", self.retirement_version,
            "graph-a", self.overlap_projection_id, self.overlap_projection_id, 2,
            self.scopes(), IdempotencyKey("retirement-publication"))
        def execute(uow):
            return GatewayKeyRotationRetirementProjectionService(uow,
                clock=lambda: "2026-08-02T03:05:01Z", trusted_epoch_clock=lambda: 1065,
                action_id_factory=lambda: "retirement-published").execute(command)
        with self.blocked_command(LIFECYCLE_LOCK, ("receiver-lifecycle:workspace-a",), execute) as future:
            self.assert_row_lockable(SESSION_LOCK, (session.session_id,))
            self.assert_row_lockable(WORKSPACE_LOCK, ("workspace-a",))
            self.assert_advisory_available(
                "operation-action:retirement-lock-session:retirement-publication", available=False)
        self.assertEqual(future.result(timeout=1).publication.desired_graph_revision, 3)
