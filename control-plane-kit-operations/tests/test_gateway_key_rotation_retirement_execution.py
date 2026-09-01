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
    LifecycleOperationKind,
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
    AdvanceGatewayKeyRotationDeployment,
    AdvanceGatewayKeyRotation,
    GatewayKeyRotationConflict,
    GatewayKeyRotationDeploymentHandoff,
    GatewayKeyRotationDeploymentPhase,
    GatewayKeyRotationDeploymentStatus,
    ReadGatewayKeyRotationDeploymentHandoff,
    GatewayKeyRotationService,
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
            "retirement checkpoint does not match durable child truth",
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
            program.progress(
                self.execution_command(
                    idempotency_key=f"retirement-step-{position}"
                )
            )
            for position in range(1, activity_count)
        ]
        result = program.progress(
            self.execution_command(
                idempotency_key=f"retirement-step-{activity_count}"
            )
        )

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
                ActivityEventKind.STEP_FAILED,
            ),
            (
                ActivityExecutionOutcome.unsupported(
                    FailureEvidence(
                        FailureCategory.TERMINAL,
                        "test-effect-unsupported",
                        "test effect is unsupported",
                    )
                ),
                "retirement-effect-failed",
                ActivityEventKind.STEP_UNSUPPORTED,
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
                ActivityEventKind.STEP_UNCERTAIN,
            ),
        )
        for outcome, expected_code, expected_event_kind in cases:
            with self.subTest(
                expected_code=expected_code,
                expected_event_kind=expected_event_kind,
            ):
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
                    [
                        kind
                        for kind in self.retirement_event_kinds()
                        if kind
                        in (
                            ActivityEventKind.STEP_FAILED,
                            ActivityEventKind.STEP_UNSUPPORTED,
                            ActivityEventKind.STEP_UNCERTAIN,
                        )
                    ],
                    [expected_event_kind],
                )
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
        self.connection.execute(
            "UPDATE cpk_execution_requests SET claim_generation=2 "
            "WHERE request_id=%s",
            (self.retirement_checkpoint.execution_request_id,),
        )
        stale_adapter = RecordingAdapter()
        with self.assertRaises(
            GatewayKeyRotationRetirementExecutionConflict
        ) as captured:
            self.execution_program(
                stale_adapter,
                prefix="stale-replay",
            ).progress(self.execution_command())
        self.assertEqual(
            str(captured.exception),
            "gateway deployment execution authority is stale",
        )
        self.assertIsNone(captured.exception.__cause__)
        self.assertIsNone(captured.exception.__context__)
        self.assertEqual(stale_adapter.calls, [])

    def test_restarts_after_post_effect_commits_without_redispatch(self) -> None:
        for crash_after_commit in range(5, 10):
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
                for position in range(1, activity_count):
                    prior = prior_program.progress(
                        self.execution_command(
                            idempotency_key=(
                                f"retirement-{crash_after_commit}-prior-{position}"
                            )
                        )
                    )
                    self.assertIs(
                        prior.outcome,
                        GatewayKeyRotationRetirementExecutionOutcome.DISPATCHED,
                    )
                control = CrashControl(crash_after_commit)
                adapter = RecordingAdapter(ActivityExecutionOutcome.succeeded())
                crash_command = self.execution_command(
                    idempotency_key=f"retirement-{crash_after_commit}-terminal"
                )

                with self.assertRaises(SimulatedProcessLoss):
                    self.execution_program(
                        adapter,
                        unit_of_work_factory=lambda: CrashAfterCommitUnitOfWork(
                            self.unit_of_work(),
                            control,
                        ),
                        prefix=f"crash-{crash_after_commit}",
                    ).progress(crash_command)

                if crash_after_commit == 8:
                    rotations = GatewayKeyRotationService(
                        self.unit_of_work,
                        clock=lambda: 5_000,
                    )
                    rotation_before_stale = rotations.get(self.rotation_id)
                    transitions_before_stale = rotations.transitions(
                        self.rotation_id
                    )
                    events_before_stale = tuple(self.retirement_event_kinds())
                    actions_before_stale = self._current_advancement_action_count()
                    self.assertIs(
                        rotation_before_stale.status,
                        GatewayKeyRotationStatus.RETIREMENT_DEPLOYING,
                    )
                    self.assertEqual(
                        rotation_before_stale.retirement_deployment,
                        self.retirement_checkpoint,
                    )
                    self.assertEqual(
                        self.workspace().current_realized_projection_id,
                        self.retirement_checkpoint.desired_realized_projection_id,
                    )
                    self.connection.execute(
                        "UPDATE cpk_execution_requests SET claim_generation=2 "
                        "WHERE request_id=%s",
                        (self.retirement_checkpoint.execution_request_id,),
                    )
                    stale_adapter = RecordingAdapter()
                    with self.assertRaises(
                        GatewayKeyRotationRetirementExecutionConflict
                    ) as captured:
                        self.execution_program(
                            stale_adapter,
                            prefix="stale-after-graph-advance",
                        ).progress(crash_command)
                    self.assertEqual(
                        str(captured.exception),
                        "retirement checkpoint does not match durable child truth",
                    )
                    self.assertEqual(stale_adapter.calls, [])
                    self.assertEqual(
                        rotations.get(self.rotation_id),
                        rotation_before_stale,
                    )
                    self.assertEqual(
                        rotations.transitions(self.rotation_id),
                        transitions_before_stale,
                    )
                    self.assertEqual(
                        tuple(self.retirement_event_kinds()),
                        events_before_stale,
                    )
                    self.assertEqual(self.retirement_advancement_count(), 1)
                    self.assertEqual(
                        self._current_advancement_action_count(),
                        actions_before_stale,
                    )
                    replacement_handoff = rotations.deployment_handoff(
                        ReadGatewayKeyRotationDeploymentHandoff(
                            self.rotation_id,
                            GatewayKeyRotationDeploymentPhase.RETIREMENT,
                            self.execution_command().worker_authority,
                        )
                    )
                    accepted_checkpoint = replace(
                        self.retirement_checkpoint,
                        status=GatewayKeyRotationDeploymentStatus.ACCEPTED,
                        accepted_current_graph_id=(
                            self.retirement_checkpoint.desired_authored_graph_id
                        ),
                        accepted_current_projection_id=(
                            self.retirement_checkpoint.desired_realized_projection_id
                        ),
                        accepted_at="2026-08-02T05:00:00Z",
                    )
                    recovered_rotation = rotations.advance_deployment(
                        AdvanceGatewayKeyRotationDeployment(
                            transition=AdvanceGatewayKeyRotation(
                                rotation_id=self.rotation_id,
                                transition_id="replacement-retirement-fold",
                                expected_status=(
                                    GatewayKeyRotationStatus.RETIREMENT_DEPLOYING
                                ),
                                expected_version=rotation_before_stale.version,
                                target_status=(
                                    GatewayKeyRotationStatus.RETIREMENT_READY
                                ),
                                advanced_by="operator-a",
                                advanced_at=accepted_checkpoint.accepted_at,
                                actor_scopes=(PolicyScope.DELEGATION_KEY_ROTATE,),
                                deployment=accepted_checkpoint,
                            ),
                            handoff=GatewayKeyRotationDeploymentHandoff(
                                self.rotation_id,
                                accepted_checkpoint,
                                replacement_handoff.fence,
                            ),
                        )
                    )
                    recovered_adapter = RecordingAdapter()
                    self.assertIs(
                        recovered_rotation.status,
                        GatewayKeyRotationStatus.RETIREMENT_READY,
                    )
                else:
                    recovered_adapter = RecordingAdapter()
                    recovered = self.execution_program(
                        recovered_adapter,
                        prefix=f"recover-{crash_after_commit}",
                    ).progress(crash_command)

                if crash_after_commit != 8:
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

    def test_accepted_fold_without_graph_advancement_evidence_is_rejected(
        self,
    ) -> None:
        self.prepare_retirement_execution()
        rotations = GatewayKeyRotationService(
            self.unit_of_work,
            clock=lambda: 5_000,
        )
        handoff = rotations.deployment_handoff(
            ReadGatewayKeyRotationDeploymentHandoff(
                self.rotation_id,
                GatewayKeyRotationDeploymentPhase.RETIREMENT,
                self.execution_command().worker_authority,
            )
        )
        accepted = replace(
            self.retirement_checkpoint,
            status=GatewayKeyRotationDeploymentStatus.ACCEPTED,
            accepted_current_graph_id=(
                self.retirement_checkpoint.desired_authored_graph_id
            ),
            accepted_current_projection_id=(
                self.retirement_checkpoint.desired_realized_projection_id
            ),
            accepted_at="2026-08-02T05:00:00Z",
        )
        before = rotations.get(self.rotation_id)
        transitions = rotations.transitions(self.rotation_id)
        event_count = len(self.retirement_event_kinds())
        action_count = self._current_advancement_action_count()

        with self.assertRaisesRegex(
            GatewayKeyRotationConflict,
            "^gateway deployment acceptance evidence is incongruent$",
        ) as captured:
            rotations.advance_deployment(
                AdvanceGatewayKeyRotationDeployment(
                    transition=AdvanceGatewayKeyRotation(
                        rotation_id=self.rotation_id,
                        transition_id="retirement-accepted-without-evidence",
                        expected_status=(
                            GatewayKeyRotationStatus.RETIREMENT_DEPLOYING
                        ),
                        expected_version=self.retirement_prepared_version,
                        target_status=GatewayKeyRotationStatus.RETIREMENT_READY,
                        advanced_by="operator-a",
                        advanced_at=accepted.accepted_at,
                        actor_scopes=(PolicyScope.DELEGATION_KEY_ROTATE,),
                        deployment=accepted,
                    ),
                    handoff=GatewayKeyRotationDeploymentHandoff(
                        self.rotation_id,
                        accepted,
                        handoff.fence,
                    ),
                )
            )

        self.assertIsNone(captured.exception.__cause__)
        self.assertIsNone(captured.exception.__context__)
        self.assertEqual(rotations.get(self.rotation_id), before)
        self.assertEqual(rotations.transitions(self.rotation_id), transitions)
        self.assertEqual(len(self.retirement_event_kinds()), event_count)
        self.assertEqual(self._current_advancement_action_count(), action_count)

    def _current_advancement_action_count(self) -> int:
        return self.connection.execute(
            "SELECT count(*) FROM cpk_operation_actions WHERE action_type=%s",
            (LifecycleOperationKind.ADVANCE_CURRENT_GRAPH.value,),
        ).fetchone()[0]


if __name__ == "__main__":
    unittest.main()
