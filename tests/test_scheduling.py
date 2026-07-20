from __future__ import annotations

from dataclasses import replace
import unittest

from control_plane_kit.core.planning import (
    ActivityDependency,
    ActivityId,
    ActivityPlan,
    NodeTarget,
    PlannedActivity,
    StartNode,
)
from control_plane_kit.saga import (
    SagaState,
    SagaStepId,
    SagaStepState,
    SagaStepStatus,
)
from control_plane_kit.scheduling import (
    BlockReason,
    ScheduleEvidenceError,
    derive_schedule,
)


def activity(name: str, *predecessors: str) -> PlannedActivity:
    return PlannedActivity(
        ActivityId(name),
        StartNode(NodeTarget(name)),
        tuple(
            ActivityDependency(ActivityId(value))
            for value in predecessors
        ),
    )


def evidence_for(
    plan: ActivityPlan,
    statuses: dict[str, SagaStepStatus] | None = None,
    *,
    compensatable: tuple[str, ...] = (),
    completion_order: tuple[str, ...] = (),
    failed_steps: tuple[str, ...] = (),
    cancelled: bool = False,
    compensation_requested: bool = False,
) -> SagaState:
    statuses = {} if statuses is None else statuses
    return SagaState(
        tuple(
            SagaStepState(
                SagaStepId(value.activity_id.value),
                statuses.get(value.activity_id.value, SagaStepStatus.PENDING),
                value.activity_id.value in compensatable,
            )
            for value in plan.activities
        ),
        tuple(SagaStepId(value) for value in completion_order),
        tuple(SagaStepId(value) for value in failed_steps),
        cancelled,
        compensation_requested,
    )


class SchedulingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.plan = ActivityPlan(
            (
                activity("root"),
                activity("left", "root"),
                activity("right", "root"),
                activity("after", "left", "right"),
            )
        )

    def test_diamond_exposes_ready_fan_out_without_layer_barriers(self):
        initial = derive_schedule(self.plan, evidence_for(self.plan))
        self.assertEqual(self._ids(initial.ready), ("root",))
        self.assertEqual(self._ids(initial.waiting), ("left", "right", "after"))

        fan_out = derive_schedule(
            self.plan,
            evidence_for(
                self.plan,
                {"root": SagaStepStatus.SUCCEEDED},
                completion_order=("root",),
            ),
        )
        self.assertEqual(self._ids(fan_out.ready), ("left", "right"))
        self.assertEqual(self._ids(fan_out.waiting), ("after",))

        partial = derive_schedule(
            self.plan,
            evidence_for(
                self.plan,
                {
                    "root": SagaStepStatus.SUCCEEDED,
                    "left": SagaStepStatus.SUCCEEDED,
                    "right": SagaStepStatus.RUNNING,
                },
                completion_order=("root", "left"),
            ),
        )
        self.assertEqual(self._ids(partial.running), ("right",))
        self.assertEqual(self._ids(partial.waiting), ("after",))

    def test_failed_branch_blocks_join_and_unstarted_forward_work(self):
        independent = activity("independent")
        plan = ActivityPlan((*self.plan.activities, independent))
        schedule = derive_schedule(
            plan,
            evidence_for(
                plan,
                {
                    "root": SagaStepStatus.SUCCEEDED,
                    "left": SagaStepStatus.FAILED,
                    "right": SagaStepStatus.SUCCEEDED,
                },
                compensatable=("root", "right"),
                completion_order=("root", "right"),
                failed_steps=("left",),
            ),
        )

        blocked = {value.activity.activity_id.value: value for value in schedule.blocked}
        self.assertIs(blocked["after"].reason, BlockReason.FAILED_PREDECESSOR)
        self.assertEqual(
            tuple(value.value for value in blocked["after"].predecessors),
            ("left",),
        )
        self.assertIs(blocked["independent"].reason, BlockReason.SAGA_FAILED)
        self.assertEqual(self._ids(schedule.compensation_ready), ("right", "root"))
        self.assertFalse(schedule.terminal)

    def test_blocking_propagates_transitively(self):
        plan = ActivityPlan(
            (
                activity("first"),
                activity("second", "first"),
                activity("third", "second"),
            )
        )
        schedule = derive_schedule(
            plan,
            evidence_for(
                plan,
                {"first": SagaStepStatus.FAILED},
                failed_steps=("first",),
            ),
        )

        blocked = {value.activity.activity_id.value: value for value in schedule.blocked}
        self.assertIs(blocked["second"].reason, BlockReason.FAILED_PREDECESSOR)
        self.assertIs(blocked["third"].reason, BlockReason.BLOCKED_PREDECESSOR)
        self.assertTrue(schedule.terminal)

    def test_cancellation_blocks_pending_but_preserves_running_evidence(self):
        schedule = derive_schedule(
            self.plan,
            evidence_for(
                self.plan,
                {
                    "root": SagaStepStatus.SUCCEEDED,
                    "left": SagaStepStatus.RUNNING,
                },
                compensatable=("root",),
                completion_order=("root",),
                cancelled=True,
            ),
        )

        self.assertEqual(self._ids(schedule.running), ("left",))
        self.assertEqual(
            {value.reason for value in schedule.blocked},
            {BlockReason.SAGA_CANCELLED},
        )
        self.assertFalse(schedule.compensation_ready)

        settled = derive_schedule(
            self.plan,
            evidence_for(
                self.plan,
                {
                    "root": SagaStepStatus.SUCCEEDED,
                    "left": SagaStepStatus.SUCCEEDED,
                },
                compensatable=("root", "left"),
                completion_order=("root", "left"),
                cancelled=True,
            ),
        )
        self.assertEqual(
            self._ids(settled.compensation_ready),
            ("left", "root"),
        )

    def test_same_plan_and_evidence_reconstruct_the_same_schedule(self):
        evidence = evidence_for(
            self.plan,
            {"root": SagaStepStatus.SUCCEEDED},
            completion_order=("root",),
        )

        self.assertEqual(
            derive_schedule(self.plan, evidence),
            derive_schedule(self.plan, replace(evidence)),
        )

    def test_compensation_waits_for_in_flight_forward_and_compensation(self):
        forward_running = evidence_for(
            self.plan,
            {
                "root": SagaStepStatus.SUCCEEDED,
                "left": SagaStepStatus.FAILED,
                "right": SagaStepStatus.RUNNING,
            },
            compensatable=("root",),
            completion_order=("root",),
            failed_steps=("left",),
        )
        self.assertFalse(
            derive_schedule(self.plan, forward_running).compensation_ready
        )

        compensation_running = evidence_for(
            self.plan,
            {
                "root": SagaStepStatus.SUCCEEDED,
                "left": SagaStepStatus.COMPENSATING,
                "right": SagaStepStatus.FAILED,
            },
            compensatable=("root", "left"),
            completion_order=("root", "left"),
            failed_steps=("right",),
        )
        self.assertFalse(
            derive_schedule(self.plan, compensation_running).compensation_ready
        )

    def test_explicit_compensation_admission_drives_reverse_schedule(self):
        schedule = derive_schedule(
            self.plan,
            evidence_for(
                self.plan,
                {
                    "root": SagaStepStatus.SUCCEEDED,
                    "left": SagaStepStatus.SUCCEEDED,
                },
                compensatable=("root", "left"),
                completion_order=("root", "left"),
                compensation_requested=True,
            ),
        )

        self.assertEqual(self._ids(schedule.compensation_ready), ("left", "root"))
        self.assertFalse(schedule.ready)

    def test_missing_foreign_and_duplicate_evidence_fail_closed(self):
        complete = evidence_for(self.plan)
        with self.assertRaises(ScheduleEvidenceError):
            derive_schedule(self.plan, replace(complete, steps=complete.steps[:-1]))
        with self.assertRaises(ScheduleEvidenceError):
            derive_schedule(
                self.plan,
                replace(
                    complete,
                    steps=(
                        *complete.steps[:-1],
                        SagaStepState(SagaStepId("foreign")),
                    ),
                ),
            )
        with self.assertRaises(ScheduleEvidenceError):
            derive_schedule(
                self.plan,
                replace(complete, steps=(*complete.steps, complete.steps[0])),
            )

    def test_incoherent_completion_and_failure_evidence_fail_closed(self):
        complete = evidence_for(self.plan)
        with self.assertRaises(ScheduleEvidenceError):
            derive_schedule(
                self.plan,
                replace(complete, completion_order=(SagaStepId("root"),)),
            )
        with self.assertRaises(ScheduleEvidenceError):
            derive_schedule(
                self.plan,
                replace(complete, failed_steps=(SagaStepId("root"),)),
            )

        succeeded_without_order = evidence_for(
            self.plan,
            {"root": SagaStepStatus.SUCCEEDED},
        )
        with self.assertRaises(ScheduleEvidenceError):
            derive_schedule(self.plan, succeeded_without_order)

    @staticmethod
    def _ids(values) -> tuple[str, ...]:
        return tuple(value.activity_id.value for value in values)


if __name__ == "__main__":
    unittest.main()
