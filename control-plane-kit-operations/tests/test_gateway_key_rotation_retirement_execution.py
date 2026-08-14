from __future__ import annotations

from dataclasses import replace
import unittest

from gateway_rotation_overlap_fixture import (
    CrashAfterCommitUnitOfWork,
    CrashControl,
    SimulatedProcessLoss,
)
from gateway_rotation_retirement_fixture import (
    GatewayRotationRetirementFixture,
    RecordingAdapter,
)
from control_plane_kit_core.operations.lifecycle import (
    ActivityEventKind,
    FailureCategory,
)
from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_operations.coordinator import ActivityExecutionOutcome
from control_plane_kit_operations.execution_leases import ExecutionLeaseFence
from control_plane_kit_operations.delegation_signing_keys import (
    RegisteredDelegationSigningKeyStatus,
)
from control_plane_kit_operations.gateway_key_rotation_retirement_execution import (
    GatewayKeyRotationRetirementExecutionAuthorizationDenied,
    GatewayKeyRotationRetirementExecutionConflict,
    GatewayKeyRotationRetirementExecutionOutcome,
)
from control_plane_kit_operations.gateway_key_rotations import (
    GatewayKeyRotationDeploymentStatus,
    GatewayKeyRotationStatus,
)
from control_plane_kit_operations.records import BoundedEvidence, FailureEvidence


class GatewayKeyRotationRetirementExecutionTests(
    GatewayRotationRetirementFixture,
    unittest.TestCase,
):
    def test_stale_fence_translation_is_bounded_and_cause_free(self) -> None:
        self.prepare_retirement_execution()
        adapter = RecordingAdapter(ActivityExecutionOutcome.succeeded())
        command = replace(
            self.execution_command(),
            fence=ExecutionLeaseFence("worker-a", 2),
        )

        with self.assertRaises(
            GatewayKeyRotationRetirementExecutionConflict
        ) as captured:
            self.execution_program(adapter).progress(command)

        self.assertEqual(
            str(captured.exception),
            "deployment coordinator rejected progress",
        )
        self.assertIsNone(captured.exception.__cause__)
        self.assertIsNone(captured.exception.__context__)
        self.assertEqual(adapter.calls, [])

    def test_executes_b_only_child_accepts_and_replays_without_key_retirement(
        self,
    ) -> None:
        self.prepare_retirement_execution()
        activity_count = self.retirement_activity_count()
        accepted_outcome = ActivityExecutionOutcome.succeeded(
            BoundedEvidence.from_mapping({"runtime": "accepted"})
        )
        adapter = RecordingAdapter(*(accepted_outcome,) * activity_count)
        program = self.execution_program(adapter)

        intermediate = [
            program.progress(self.execution_command())
            for _ in range(activity_count - 1)
        ]
        result = program.progress(self.execution_command())

        self.assertGreater(activity_count, 1)
        self.assertTrue(
            all(
                value.outcome
                is GatewayKeyRotationRetirementExecutionOutcome.DISPATCHED
                for value in intermediate
            )
        )
        self.assertIs(
            result.outcome,
            GatewayKeyRotationRetirementExecutionOutcome.ACCEPTED,
        )
        self.assertIs(result.rotation.status, GatewayKeyRotationStatus.RETIREMENT_READY)
        self.assertIs(
            result.checkpoint.status,
            GatewayKeyRotationDeploymentStatus.ACCEPTED,
        )
        self.assertEqual(len(adapter.calls), activity_count)
        self.assertEqual(result.effects_attempted, 1)
        workspace = self.workspace()
        self.assertEqual(workspace.current_graph_id, "graph-a")
        self.assertEqual(
            workspace.current_realized_projection_id,
            self.retirement_checkpoint.desired_realized_projection_id,
        )
        self.assertEqual(workspace.desired_graph_id, "graph-a")
        self.assertEqual(self.authored_graph_count(), 1)
        self.assertIs(
            self.old_key().status,
            RegisteredDelegationSigningKeyStatus.VERIFY_ONLY,
        )
        self.assertIsNone(result.rotation.old_key_retired_at)
        self.assertIsNone(result.rotation.old_secret_revoked_at)

        replay = self.execution_program(adapter, prefix="replay").progress(
            self.execution_command()
        )
        self.assertIs(
            replay.outcome,
            GatewayKeyRotationRetirementExecutionOutcome.ACCEPTED_REPLAY,
        )
        self.assertEqual(len(adapter.calls), activity_count)
        self.assertEqual(self.retirement_advancement_count(), 1)

    def test_failure_classes_block_with_bounded_codes(self) -> None:
        cases = (
            (
                ActivityExecutionOutcome.failed(
                    FailureEvidence(
                        FailureCategory.TERMINAL,
                        "test-effect-failed",
                        "test effect failed",
                    )
                ),
                "retirement-effect-failed",
            ),
            (
                ActivityExecutionOutcome.unsupported(
                    FailureEvidence(
                        FailureCategory.TERMINAL,
                        "test-effect-unsupported",
                        "test effect is unsupported",
                    )
                ),
                "retirement-effect-unsupported",
            ),
            (
                ActivityExecutionOutcome.uncertain(
                    FailureEvidence(
                        FailureCategory.UNCERTAIN,
                        "test-effect-uncertain",
                        "test effect result is unknown",
                    )
                ),
                "retirement-effect-uncertain",
            ),
        )
        for outcome, expected_code in cases:
            with self.subTest(expected_code=expected_code):
                self.reset_truth()
                self.prepare_retirement_execution()
                adapter = RecordingAdapter(outcome)

                result = self.execution_program(adapter).progress(
                    self.execution_command()
                )

                self.assertIs(
                    result.outcome,
                    GatewayKeyRotationRetirementExecutionOutcome.BLOCKED,
                )
                self.assertEqual(result.failure_code, expected_code)
                self.assertEqual(len(adapter.calls), 1)
                self.assertEqual(
                    self.workspace().current_realized_projection_id,
                    self.overlap_projection_id,
                )

    def test_process_loss_after_intent_blocks_without_redispatch(self) -> None:
        self.prepare_retirement_execution()
        crashing = RecordingAdapter(SimulatedProcessLoss("lost during effect"))

        with self.assertRaises(SimulatedProcessLoss):
            self.execution_program(crashing).progress(self.execution_command())

        recovered_adapter = RecordingAdapter()
        recovered = self.execution_program(
            recovered_adapter,
            prefix="recover",
        ).progress(self.execution_command())
        self.assertIs(
            recovered.outcome,
            GatewayKeyRotationRetirementExecutionOutcome.BLOCKED,
        )
        self.assertEqual(recovered.failure_code, "retirement-effect-uncertain")
        self.assertEqual(recovered_adapter.calls, [])
        self.assertEqual(
            self.retirement_event_kinds().count(ActivityEventKind.STEP_STARTED),
            1,
        )
        self.assertEqual(
            self.workspace().current_realized_projection_id,
            self.overlap_projection_id,
        )

    def test_restarts_after_post_effect_commits_without_redispatch(self) -> None:
        for crash_after_commit in range(4, 8):
            with self.subTest(crash_after_commit=crash_after_commit):
                self.reset_truth()
                self.prepare_retirement_execution()
                activity_count = self.retirement_activity_count()
                prior_adapter = RecordingAdapter(
                    *(ActivityExecutionOutcome.succeeded(),)
                    * (activity_count - 1)
                )
                prior_program = self.execution_program(
                    prior_adapter,
                    prefix=f"prior-{crash_after_commit}",
                )
                for _ in range(activity_count - 1):
                    prior = prior_program.progress(self.execution_command())
                    self.assertIs(
                        prior.outcome,
                        GatewayKeyRotationRetirementExecutionOutcome.DISPATCHED,
                    )
                control = CrashControl(crash_after_commit)
                adapter = RecordingAdapter(ActivityExecutionOutcome.succeeded())

                with self.assertRaises(SimulatedProcessLoss):
                    self.execution_program(
                        adapter,
                        unit_of_work_factory=lambda: CrashAfterCommitUnitOfWork(
                            self.unit_of_work(),
                            control,
                        ),
                        prefix=f"crash-{crash_after_commit}",
                    ).progress(self.execution_command())

                recovered_adapter = RecordingAdapter()
                recovered = self.execution_program(
                    recovered_adapter,
                    prefix=f"recover-{crash_after_commit}",
                ).progress(self.execution_command())

                self.assertIn(
                    recovered.outcome,
                    {
                        GatewayKeyRotationRetirementExecutionOutcome.ACCEPTED,
                        GatewayKeyRotationRetirementExecutionOutcome.ACCEPTED_REPLAY,
                    },
                    recovered.failure_code,
                )
                self.assertEqual(len(prior_adapter.calls), activity_count - 1)
                self.assertEqual(len(adapter.calls), 1)
                self.assertEqual(recovered_adapter.calls, [])
                self.assertEqual(self.retirement_advancement_count(), 1)
                self.assertEqual(
                    self.workspace().current_realized_projection_id,
                    self.retirement_checkpoint.desired_realized_projection_id,
                )

    def test_stale_checkpoint_and_missing_scopes_fail_before_io(self) -> None:
        self.prepare_retirement_execution()
        for command, expected_error in (
            (
                self.execution_command(
                    expected_version=self.retirement_prepared_version + 1
                ),
                GatewayKeyRotationRetirementExecutionConflict,
            ),
            (
                self.execution_command(actor_scopes=()),
                GatewayKeyRotationRetirementExecutionAuthorizationDenied,
            ),
            (
                self.execution_command(worker_scopes=()),
                GatewayKeyRotationRetirementExecutionAuthorizationDenied,
            ),
        ):
            with self.subTest(error=expected_error.__name__):
                adapter = RecordingAdapter()
                with self.assertRaises(expected_error):
                    self.execution_program(adapter).progress(command)
                self.assertEqual(adapter.calls, [])

        self.connection.execute(
            "UPDATE cpk_workspaces SET desired_graph_revision=99 "
            "WHERE workspace_id='workspace-a'"
        )
        adapter = RecordingAdapter()
        with self.assertRaises(GatewayKeyRotationRetirementExecutionConflict):
            self.execution_program(adapter).progress(self.execution_command())
        self.assertEqual(adapter.calls, [])


if __name__ == "__main__":
    unittest.main()
