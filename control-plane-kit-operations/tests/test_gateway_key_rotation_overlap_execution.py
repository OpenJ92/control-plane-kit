from __future__ import annotations

from dataclasses import replace
import os
import unittest

import psycopg

from gateway_rotation_overlap_fixture import (
    CrashAfterCommitUnitOfWork,
    CrashControl,
    GatewayRotationOverlapFixture,
    SimulatedProcessLoss,
)
from control_plane_kit_core.operations.lifecycle import (
    ActivityEventKind,
    FailureCategory,
)
from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_operations.execution_leases import ExecutionLeaseFence
from control_plane_kit_operations.coordinator import (
    ActivityExecutionOutcome,
    ActivityRealizationContext,
    ExecutionCoordinator,
)
from control_plane_kit_operations.gateway_key_rotation_overlap_execution import (
    GatewayKeyRotationOverlapExecutionAuthorizationDenied,
    GatewayKeyRotationOverlapExecutionConflict,
    GatewayKeyRotationOverlapExecutionOutcome,
    GatewayKeyRotationOverlapExecutionProgram,
    ProgressGatewayKeyRotationOverlap,
)
from control_plane_kit_operations.gateway_key_rotation_overlap_program import (
    GatewayKeyRotationOverlapPreparationProgram,
    PrepareGatewayKeyRotationOverlap,
)
from control_plane_kit_operations.gateway_key_rotations import (
    GatewayKeyRotationDeploymentStatus,
    GatewayKeyRotationStatus,
)
from control_plane_kit_operations.lifecycle import (
    ExecutionLeaseDuration,
    ExecutionWorkerAuthority,
    RunLifecycleCommandService,
)
from control_plane_kit_operations.postgres import PostgresUnitOfWork, install_schema
from control_plane_kit_operations.records import BoundedEvidence, FailureEvidence


class CountingIds:
    def __init__(self, prefix: str) -> None:
        self.prefix = prefix
        self.count = 0

    def __call__(self) -> str:
        self.count += 1
        return f"{self.prefix}-{self.count}"


class RecordingAdapter:
    def __init__(
        self,
        *outcomes: ActivityExecutionOutcome | BaseException,
    ) -> None:
        self.outcomes = list(outcomes)
        self.calls: list[str] = []

    def execute(
        self,
        context: ActivityRealizationContext,
    ) -> ActivityExecutionOutcome:
        self.calls.append(context.activity.activity_id.value)
        if not self.outcomes:
            raise AssertionError("unexpected duplicate runtime effect")
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


class GatewayKeyRotationOverlapExecutionTests(
    GatewayRotationOverlapFixture,
    unittest.TestCase,
):
    def setUp(self) -> None:
        database_url = os.environ.get("CPK_OPERATIONS_TEST_DATABASE_URL")
        if not database_url:
            raise RuntimeError("run through control-plane-kit-operations/test.sh")
        self.database_url = database_url
        self.connection = psycopg.connect(database_url, autocommit=True)
        install_schema(self.connection)
        self.reset_truth()

    def tearDown(self) -> None:
        self.connection.close()

    def reset_truth(self) -> None:
        self.connection.execute("TRUNCATE TABLE cpk_workspaces CASCADE")
        self.seed_graph_and_keys()
        self.seed_rotation_approval()
        prepared = self.preparation_program().prepare(self.preparation_command())
        assert prepared.checkpoint is not None
        self.prepared_version = prepared.rotation.version
        self.checkpoint = prepared.checkpoint

    def unit_of_work(self) -> PostgresUnitOfWork:
        return PostgresUnitOfWork(lambda: psycopg.connect(self.database_url))

    def preparation_program(self) -> GatewayKeyRotationOverlapPreparationProgram:
        timestamps = iter(
            f"2026-08-02T02:{minute:02d}:00Z" for minute in range(30)
        )
        return GatewayKeyRotationOverlapPreparationProgram(
            self.unit_of_work,
            clock=lambda: next(timestamps),
            trusted_epoch_clock=lambda: 2_000,
            id_factory=CountingIds("prepare"),
        )

    def preparation_command(self) -> PrepareGatewayKeyRotationOverlap:
        scopes = (
            PolicyScope.DELEGATION_KEY_ROTATE,
            PolicyScope.PLAN_EXECUTE,
            PolicyScope.EXECUTION_OPERATE,
        )
        return PrepareGatewayKeyRotationOverlap(
            rotation_id=self.rotation_id,
            expected_rotation_version=self.rotation_version,
            expected_authored_graph_id="graph-a",
            expected_current_realized_projection_id="projection-a",
            expected_desired_realized_projection_id="projection-a",
            expected_desired_graph_revision=1,
            actor_id="operator-a",
            actor_scopes=scopes,
            worker_authority=ExecutionWorkerAuthority(
                "worker-a",
                (PolicyScope.EXECUTION_OPERATE,),
            ),
            lease_duration=ExecutionLeaseDuration(1800),
        )

    def command(
        self,
        *,
        expected_version: int | None = None,
        actor_scopes: tuple[PolicyScope, ...] = (
            PolicyScope.DELEGATION_KEY_ROTATE,
        ),
        worker_scopes: tuple[PolicyScope, ...] = (
            PolicyScope.EXECUTION_OPERATE,
        ),
    ) -> ProgressGatewayKeyRotationOverlap:
        return ProgressGatewayKeyRotationOverlap(
            rotation_id=self.rotation_id,
            expected_prepared_rotation_version=(
                self.prepared_version
                if expected_version is None
                else expected_version
            ),
            actor_id="operator-a",
            actor_scopes=actor_scopes,
            worker_authority=ExecutionWorkerAuthority(
                "worker-a",
                worker_scopes,
            ),
            fence=ExecutionLeaseFence("worker-a", 1),
        )

    def program(
        self,
        adapter: RecordingAdapter,
        *,
        unit_of_work_factory=None,
        prefix: str = "execute",
    ) -> GatewayKeyRotationOverlapExecutionProgram:
        factory = unit_of_work_factory or self.unit_of_work
        ids = CountingIds(prefix)
        clock = lambda: "2026-08-02T03:00:00Z"
        lifecycle = RunLifecycleCommandService(
            factory,
            clock=clock,
            id_factory=ids,
        )
        coordinator = ExecutionCoordinator(
            factory,
            lifecycle=lifecycle,
            adapter=adapter,
            clock=clock,
            id_factory=ids,
        )
        return GatewayKeyRotationOverlapExecutionProgram(
            factory,
            coordinator=coordinator,
            clock=clock,
            trusted_epoch_clock=lambda: 3_000,
            id_factory=ids,
        )

    def test_dispatches_accepts_advances_and_replays_without_duplicate_effect(self) -> None:
        activity_count = self._plan_activity_count()
        accepted_outcome = ActivityExecutionOutcome.succeeded(
            BoundedEvidence.from_mapping({"runtime": "accepted"})
        )
        adapter = RecordingAdapter(*(accepted_outcome,) * activity_count)
        program = self.program(adapter)
        command = self.command()

        intermediate = [
            program.progress(command)
            for _ in range(activity_count - 1)
        ]
        result = program.progress(command)

        self.assertTrue(intermediate)
        self.assertTrue(
            all(
                value.outcome
                is GatewayKeyRotationOverlapExecutionOutcome.DISPATCHED
                for value in intermediate
            )
        )
        self.assertIs(
            result.outcome,
            GatewayKeyRotationOverlapExecutionOutcome.ACCEPTED,
        )
        self.assertIs(result.rotation.status, GatewayKeyRotationStatus.OVERLAP_READY)
        self.assertIs(
            result.checkpoint.status,
            GatewayKeyRotationDeploymentStatus.ACCEPTED,
        )
        self.assertEqual(len(adapter.calls), activity_count)
        self.assertEqual(result.effects_attempted, 1)
        self.assertIsNotNone(result.advancement)
        self.assertEqual(
            result.advancement.action.payload["claim_generation"],
            command.fence.generation,
        )
        workspace = self._workspace()
        self.assertEqual(workspace.current_graph_id, "graph-a")
        self.assertEqual(
            workspace.current_realized_projection_id,
            self.checkpoint.desired_realized_projection_id,
        )
        self.assertEqual(workspace.desired_graph_id, "graph-a")
        self.assertEqual(self._authored_graph_count(), 1)

        replay = self.program(adapter, prefix="replay").progress(command)

        self.assertIs(
            replay.outcome,
            GatewayKeyRotationOverlapExecutionOutcome.ACCEPTED_REPLAY,
        )
        self.assertEqual(len(adapter.calls), activity_count)
        self.assertEqual(self._current_advancement_count(), 1)

    def test_stale_fence_translation_is_bounded_and_cause_free(self) -> None:
        adapter = RecordingAdapter(ActivityExecutionOutcome.succeeded())
        command = replace(
            self.command(),
            fence=ExecutionLeaseFence("worker-a", 2),
        )

        with self.assertRaises(
            GatewayKeyRotationOverlapExecutionConflict
        ) as captured:
            self.program(adapter).progress(command)

        self.assertEqual(
            str(captured.exception),
            "deployment coordinator rejected progress",
        )
        self.assertIsNone(captured.exception.__cause__)
        self.assertIsNone(captured.exception.__context__)
        self.assertEqual(adapter.calls, [])

    def test_failed_and_uncertain_effects_block_with_bounded_codes(self) -> None:
        cases = (
            (
                ActivityExecutionOutcome.failed(
                    FailureEvidence(
                        FailureCategory.TERMINAL,
                        "test-effect-failed",
                        "test effect failed",
                    )
                ),
                "overlap-effect-failed",
            ),
            (
                ActivityExecutionOutcome.uncertain(
                    FailureEvidence(
                        FailureCategory.UNCERTAIN,
                        "test-effect-uncertain",
                        "test effect result is unknown",
                    )
                ),
                "overlap-effect-uncertain",
            ),
        )
        for outcome, expected_code in cases:
            with self.subTest(expected_code=expected_code):
                self.reset_truth()
                adapter = RecordingAdapter(outcome)

                result = self.program(adapter).progress(self.command())

                self.assertIs(
                    result.outcome,
                    GatewayKeyRotationOverlapExecutionOutcome.BLOCKED,
                )
                self.assertEqual(result.failure_code, expected_code)
                self.assertEqual(result.rotation.updated_at, "2026-08-02T03:00:00Z")
                self.assertEqual(len(adapter.calls), 1)
                self.assertEqual(
                    self._workspace().current_realized_projection_id,
                    "projection-a",
                )

    def test_process_loss_after_intent_blocks_without_redispatch(self) -> None:
        crashing = RecordingAdapter(SimulatedProcessLoss("lost during effect"))

        with self.assertRaises(SimulatedProcessLoss):
            self.program(crashing).progress(self.command())

        recovered_adapter = RecordingAdapter()
        recovered = self.program(recovered_adapter, prefix="recover").progress(
            self.command()
        )
        self.assertIs(
            recovered.outcome,
            GatewayKeyRotationOverlapExecutionOutcome.BLOCKED,
        )
        self.assertEqual(recovered.failure_code, "overlap-effect-uncertain")
        self.assertEqual(recovered_adapter.calls, [])
        self.assertEqual(self._event_kinds().count(ActivityEventKind.STEP_STARTED), 1)
        self.assertEqual(
            self._workspace().current_realized_projection_id,
            "projection-a",
        )

    def test_restarts_after_each_post_effect_commit_without_duplicate_effect(self) -> None:
        # get rotation, snapshot, step intent, step result, run complete,
        # current projection advance, rotation fold.
        for crash_after_commit in range(4, 8):
            with self.subTest(crash_after_commit=crash_after_commit):
                self.reset_truth()
                activity_count = self._plan_activity_count()
                prior_adapter = RecordingAdapter(
                    *(ActivityExecutionOutcome.succeeded(),)
                    * (activity_count - 1)
                )
                prior_program = self.program(
                    prior_adapter,
                    prefix=f"prior-{crash_after_commit}",
                )
                for _ in range(activity_count - 1):
                    prior = prior_program.progress(self.command())
                    self.assertIs(
                        prior.outcome,
                        GatewayKeyRotationOverlapExecutionOutcome.DISPATCHED,
                    )
                control = CrashControl(crash_after_commit)
                crashing_factory = lambda: CrashAfterCommitUnitOfWork(
                    self.unit_of_work(),
                    control,
                )
                adapter = RecordingAdapter(ActivityExecutionOutcome.succeeded())

                with self.assertRaises(SimulatedProcessLoss):
                    self.program(
                        adapter,
                        unit_of_work_factory=crashing_factory,
                        prefix=f"crash-{crash_after_commit}",
                    ).progress(self.command())

                durable_kinds = self._event_kinds()
                self.assertEqual(
                    durable_kinds.count(ActivityEventKind.STEP_STARTED),
                    activity_count,
                )
                if crash_after_commit >= 4:
                    self.assertEqual(
                        durable_kinds.count(ActivityEventKind.STEP_SUCCEEDED),
                        activity_count,
                    )

                recovered_adapter = RecordingAdapter()
                recovered = self.program(
                    recovered_adapter,
                    prefix=f"recover-{crash_after_commit}",
                ).progress(self.command())

                self.assertIn(
                    recovered.outcome,
                    {
                        GatewayKeyRotationOverlapExecutionOutcome.ACCEPTED,
                        GatewayKeyRotationOverlapExecutionOutcome.ACCEPTED_REPLAY,
                    },
                    recovered.failure_code,
                )
                self.assertEqual(len(prior_adapter.calls), activity_count - 1)
                self.assertEqual(len(adapter.calls), 1)
                self.assertEqual(recovered_adapter.calls, [])
                self.assertEqual(self._current_advancement_count(), 1)
                self.assertEqual(
                    self._workspace().current_realized_projection_id,
                    self.checkpoint.desired_realized_projection_id,
                )

    def test_stale_checkpoint_and_missing_scopes_fail_before_dispatch(self) -> None:
        for command, expected_error in (
            (
                self.command(expected_version=self.prepared_version + 1),
                GatewayKeyRotationOverlapExecutionConflict,
            ),
            (
                self.command(actor_scopes=()),
                GatewayKeyRotationOverlapExecutionAuthorizationDenied,
            ),
            (
                self.command(worker_scopes=()),
                GatewayKeyRotationOverlapExecutionAuthorizationDenied,
            ),
        ):
            with self.subTest(error=expected_error.__name__):
                adapter = RecordingAdapter()
                with self.assertRaises(expected_error):
                    self.program(adapter).progress(command)
                self.assertEqual(adapter.calls, [])

        self.connection.execute(
            "UPDATE cpk_workspaces SET desired_graph_revision=99 "
            "WHERE workspace_id='workspace-a'"
        )
        adapter = RecordingAdapter()
        with self.assertRaises(GatewayKeyRotationOverlapExecutionConflict):
            self.program(adapter).progress(self.command())
        self.assertEqual(adapter.calls, [])

    def _workspace(self):
        with self.unit_of_work() as unit_of_work:
            return unit_of_work.stores.workspaces.get("workspace-a")

    def _plan_activity_count(self) -> int:
        with self.unit_of_work() as unit_of_work:
            plan = unit_of_work.stores.activity_history.get_plan(
                self.checkpoint.plan_id
            )
        count = len(plan.plan.activities)
        self.assertGreater(count, 1)
        return count

    def _event_kinds(self) -> list[ActivityEventKind]:
        with self.unit_of_work() as unit_of_work:
            events = unit_of_work.stores.execution.events_for_run(
                self.checkpoint.run_id
            )
        return [event.kind for event in events]

    def _authored_graph_count(self) -> int:
        return self.connection.execute(
            "SELECT count(*) FROM cpk_graph_versions"
        ).fetchone()[0]

    def _current_advancement_count(self) -> int:
        return self._event_kinds().count(ActivityEventKind.CURRENT_GRAPH_ADVANCED)


if __name__ == "__main__":
    unittest.main()
