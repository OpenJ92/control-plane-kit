from __future__ import annotations

import unittest

from gateway_rotation_overlap_fixture import (
    CrashAfterCommitUnitOfWork,
    CrashControl,
    PUBLIC_KEY_OTHER,
    SimulatedProcessLoss,
)
from gateway_rotation_retirement_fixture import GatewayRotationRetirementFixture
from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_operations.execution_leases import ExecutionLeaseFence
from control_plane_kit_core.topology import DEFAULT_GRAPH_CODEC
from control_plane_kit_operations.gateway_key_rotation_retirement_program import (
    GatewayKeyRotationRetirementPreparationAuthorizationDenied,
    GatewayKeyRotationRetirementPreparationConflict,
    GatewayKeyRotationRetirementPreparationOutcome,
)
from control_plane_kit_operations.gateway_key_rotations import (
    GatewayKeyRotationStatus,
)


class GatewayKeyRotationRetirementPreparationTests(
    GatewayRotationRetirementFixture,
    unittest.TestCase,
):
    def test_prepares_exact_b_only_child_without_changing_authored_truth(self) -> None:
        result = self.program().prepare(self.command())

        self.assertEqual(
            result.outcome,
            GatewayKeyRotationRetirementPreparationOutcome.PREPARED,
        )
        self.assertIs(
            result.rotation.status,
            GatewayKeyRotationStatus.RETIREMENT_DEPLOYING,
        )
        checkpoint = result.checkpoint
        self.assertEqual(result.handoff.checkpoint, checkpoint)
        self.assertEqual(result.handoff.fence, ExecutionLeaseFence("worker-a", 1))
        self.assertEqual(checkpoint.base_authored_graph_id, "graph-a")
        self.assertEqual(checkpoint.desired_authored_graph_id, "graph-a")
        self.assertEqual(
            checkpoint.base_realized_projection_id,
            self.overlap_projection_id,
        )
        self.assertEqual(
            checkpoint.desired_realized_projection_id,
            f"gateway-rotation-{self.rotation_id}-retirement",
        )
        self.assertEqual(checkpoint.desired_revision, 3)
        projection = self.connection.execute(
            "SELECT graph_descriptor FROM cpk_realized_graph_projections "
            "WHERE projection_id=%s",
            (checkpoint.desired_realized_projection_id,),
        ).fetchone()[0]
        realized = DEFAULT_GRAPH_CODEC.decode(projection)
        target = realized.node("gateway-a").delegation_verifier_projection
        other = realized.node("gateway-other").delegation_verifier_projection
        self.assertEqual(tuple(key.key_id for key in target.public_keys), ("key-b",))
        self.assertEqual(tuple(key.key_id for key in other.public_keys), ("key-other",))
        self.assertEqual(self.authored_graph_count(), 1)
        self.assertEqual(self.count("cpk_observations"), 0)

        replay = self.program(prefix="replay").prepare(self.command())
        self.assertEqual(
            replay.outcome,
            GatewayKeyRotationRetirementPreparationOutcome.PREPARED_REPLAY,
        )
        self.assertEqual(replay.checkpoint, checkpoint)
        self.assertEqual(replay.handoff, result.handoff)

    def test_premature_drain_rejects_before_child_or_desired_mutation(self) -> None:
        before = self.child_counts()
        pointer = self.desired_pointer()
        with self.assertRaises(GatewayKeyRotationRetirementPreparationConflict):
            self.program(epoch=1_064).prepare(self.command())
        self.assertEqual(self.child_counts(), before)
        self.assertEqual(self.desired_pointer(), pointer)

    def test_stale_lineage_key_truth_approval_and_scope_fail_closed(self) -> None:
        with self.assertRaises(GatewayKeyRotationRetirementPreparationConflict):
            self.program(prefix="stale-version").prepare(
                self.command(expected_version=self.retirement_version + 1)
            )
        with self.assertRaises(GatewayKeyRotationRetirementPreparationConflict):
            self.program(prefix="stale-pointer").prepare(
                self.command(projection_id="projection-forged")
            )
        with self.assertRaises(
            GatewayKeyRotationRetirementPreparationAuthorizationDenied
        ):
            self.program(prefix="missing-scope").prepare(
                self.command(scopes=(PolicyScope.DELEGATION_KEY_ROTATE,))
            )
        self.connection.execute(
            "UPDATE cpk_operation_actions SET payload=jsonb_set("
            "payload, '{review_digest}', to_jsonb(%s::text)) "
            "WHERE idempotency_key='request-rotation-approval'",
            ("f" * 64,),
        )
        with self.assertRaises(
            GatewayKeyRotationRetirementPreparationAuthorizationDenied
        ):
            self.program(prefix="approval-drift").prepare(self.command())

    def test_unexpected_verifier_key_fails_before_projection_publication(self) -> None:
        with self.unit_of_work() as unit_of_work:
            unit_of_work.stores.delegation_signing_keys.register(
                self.signing_key("key-extra", PUBLIC_KEY_OTHER)
            )
            unit_of_work.commit()
        pointer = self.desired_pointer()
        with self.assertRaises(GatewayKeyRotationRetirementPreparationConflict):
            self.program(prefix="extra-key").prepare(self.command())
        self.assertEqual(self.desired_pointer(), pointer)

    def test_restart_after_each_physical_commit_recovers_exact_child(self) -> None:
        for commit_number in range(1, 9):
            with self.subTest(commit_number=commit_number):
                self.reset_truth()
                control = CrashControl(commit_number)
                with self.assertRaises(SimulatedProcessLoss):
                    self.program(
                        unit_of_work_factory=lambda: CrashAfterCommitUnitOfWork(
                            self.unit_of_work(), control
                        ),
                        prefix=f"crash-{commit_number}",
                    ).prepare(self.command())
                recovered = self.program(prefix=f"recover-{commit_number}").prepare(
                    self.command()
                )
                self.assertIn(
                    recovered.outcome,
                    {
                        GatewayKeyRotationRetirementPreparationOutcome.PREPARED,
                        GatewayKeyRotationRetirementPreparationOutcome.PREPARED_REPLAY,
                    },
                )
                self.assertIs(
                    recovered.rotation.status,
                    GatewayKeyRotationStatus.RETIREMENT_DEPLOYING,
                )
                self.assertEqual(self.count("cpk_activity_plans"), 2)
                self.assertEqual(self.count("cpk_execution_requests"), 2)
                self.assertEqual(self.count("cpk_activity_runs"), 2)


if __name__ == "__main__":
    unittest.main()
