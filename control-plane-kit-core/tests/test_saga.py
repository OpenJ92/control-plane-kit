from __future__ import annotations

from dataclasses import dataclass, replace
import unittest

from control_plane_kit_core.planning.activity_plan import (
    ActivityDependency,
    ActivityId,
    ActivityPlan,
    NodeTarget,
    PlannedActivity,
    RuntimeTarget,
    StartNode,
    StartRuntime,
)
from control_plane_kit_core.planning.saga import (
    ActivityJournalEvent,
    ActivityJournalEventKind,
    BeginSagaCompensation,
    CancelSaga,
    FailSagaCompensation,
    FailSagaStep,
    RequestSagaCompensation,
    SagaCompensationFailed,
    SagaCompensationRequested,
    SagaCompensationStarted,
    SagaCompensationSucceeded,
    SagaProgramError,
    SagaState,
    SagaStateError,
    SagaStatus,
    SagaStep,
    SagaStepFailed,
    SagaStepId,
    SagaStepStarted,
    SagaStepState,
    SagaStepStatus,
    SagaStepSucceeded,
    StartSagaStep,
    SucceedSagaCompensation,
    SucceedSagaStep,
    chain,
    compensation_candidates,
    decide,
    evolve_all,
    initial_state,
    parallel,
    program_steps,
    project_activity_journal,
    reconstruct,
    then,
)


@dataclass(frozen=True)
class DemoEffect:
    name: str


def saga_step(name: str, *, compensatable: bool = True) -> SagaStep[DemoEffect]:
    return SagaStep(
        SagaStepId(name),
        DemoEffect(f"do:{name}"),
        DemoEffect(f"undo:{name}") if compensatable else None,
    )


def apply(state: SagaState, command: object) -> SagaState:
    return evolve_all(state, decide(state, command))


def activity(name: str, *predecessors: str) -> PlannedActivity:
    return PlannedActivity(
        ActivityId(name),
        StartNode(NodeTarget(name)),
        tuple(
            ActivityDependency(ActivityId(value))
            for value in predecessors
        ),
    )


def plan() -> ActivityPlan:
    return ActivityPlan(
        (
            PlannedActivity(
                ActivityId("start-runtime"),
                StartRuntime(RuntimeTarget("runtime")),
            ),
        )
    )


def event(
    ordinal: int,
    kind: ActivityJournalEventKind,
    *,
    activity_id: str = "start-runtime",
    run_id: str = "run-a",
) -> ActivityJournalEvent:
    return ActivityJournalEvent(
        event_id=f"event-{ordinal}",
        run_id=run_id,
        ordinal=ordinal,
        kind=kind,
        activity_id=activity_id,
    )


def run_event(
    ordinal: int,
    kind: ActivityJournalEventKind,
    *,
    run_id: str = "run-a",
) -> ActivityJournalEvent:
    return ActivityJournalEvent(
        event_id=f"event-{ordinal}",
        run_id=run_id,
        ordinal=ordinal,
        kind=kind,
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


class SagaProgramSuccessorTests(unittest.TestCase):
    def test_chain_and_then_build_immutable_callback_free_syntax(self) -> None:
        first = saga_step("first")
        second = saga_step("second")
        original = chain(first)

        appended = then(original, second)

        self.assertEqual(
            tuple(value.step_id.value for value in program_steps(original)),
            ("first",),
        )
        self.assertEqual(
            tuple(value.step_id.value for value in program_steps(appended)),
            ("first", "second"),
        )
        self.assertEqual(appended.step.effect, DemoEffect("do:first"))
        self.assertFalse(callable(appended.step.effect))

    def test_parallel_preserves_declared_fan_out_and_shared_continuation(self) -> None:
        fan_out = parallel(saga_step("left"), saga_step("right"))
        program = then(fan_out, saga_step("after"))

        self.assertEqual(len(program.branches), 2)
        self.assertEqual(
            tuple(value.step_id.value for value in program_steps(program)),
            ("left", "right", "after"),
        )

    def test_invalid_parallel_and_duplicate_identity_fail_structurally(self) -> None:
        with self.assertRaises(SagaProgramError):
            parallel(saga_step("only"))
        with self.assertRaises(SagaProgramError):
            initial_state(chain(saga_step("same"), saga_step("same")))


class SagaStateSuccessorTests(unittest.TestCase):
    def test_successful_sequence_reconstructs_from_the_same_events(self) -> None:
        program = chain(saga_step("first"), saga_step("second"))
        events = (
            SagaStepStarted(SagaStepId("first")),
            SagaStepSucceeded(SagaStepId("first")),
            SagaStepStarted(SagaStepId("second")),
            SagaStepSucceeded(SagaStepId("second")),
        )

        evolved = evolve_all(initial_state(program), events)

        self.assertEqual(evolved, reconstruct(program, events))
        self.assertIs(evolved.status, SagaStatus.SUCCEEDED)
        self.assertEqual(
            tuple(value.value for value in evolved.completion_order),
            ("first", "second"),
        )

    def test_parallel_completion_evidence_determines_reverse_compensation(self) -> None:
        program = then(
            parallel(saga_step("left"), saga_step("right")),
            saga_step("after"),
        )
        state = initial_state(program)
        for command in (
            StartSagaStep(SagaStepId("left")),
            StartSagaStep(SagaStepId("right")),
            SucceedSagaStep(SagaStepId("right")),
            SucceedSagaStep(SagaStepId("left")),
        ):
            state = apply(state, command)
        state = apply(state, CancelSaga())

        self.assertEqual(
            tuple(value.value for value in compensation_candidates(state)),
            ("left", "right"),
        )
        state = apply(state, BeginSagaCompensation(SagaStepId("left")))
        state = apply(state, SucceedSagaCompensation(SagaStepId("left")))
        state = apply(state, BeginSagaCompensation(SagaStepId("right")))
        state = apply(state, SucceedSagaCompensation(SagaStepId("right")))
        self.assertIs(state.status, SagaStatus.COMPENSATED)

    def test_explicit_compensation_admission_is_pure_replayable_state(self) -> None:
        program = chain(saga_step("first"), saga_step("second"))
        completed = (
            SagaStepStarted(SagaStepId("first")),
            SagaStepSucceeded(SagaStepId("first")),
            SagaCompensationRequested(),
        )

        state = reconstruct(program, completed)

        self.assertTrue(state.compensation_requested)
        self.assertIs(state.status, SagaStatus.COMPENSATING)
        self.assertEqual(compensation_candidates(state), (SagaStepId("first"),))
        self.assertEqual(state, reconstruct(program, completed))
        with self.assertRaisesRegex(SagaStateError, "not active"):
            decide(state, StartSagaStep(SagaStepId("second")))
        with self.assertRaisesRegex(SagaStateError, "already requested"):
            decide(state, RequestSagaCompensation())

    def test_compensation_admission_preserves_in_flight_forward_evidence(self) -> None:
        program = parallel(saga_step("completed"), saga_step("running"))
        state = reconstruct(
            program,
            (
                SagaStepStarted(SagaStepId("completed")),
                SagaStepSucceeded(SagaStepId("completed")),
                SagaStepStarted(SagaStepId("running")),
                SagaCompensationRequested(),
            ),
        )

        self.assertIs(
            state.step(SagaStepId("running")).status,
            SagaStepStatus.RUNNING,
        )
        self.assertIs(state.status, SagaStatus.COMPENSATING)

    def test_compensation_admission_without_completed_inverse_fails_closed(self) -> None:
        with self.assertRaisesRegex(
            SagaStateError,
            "requires completed compensatable work",
        ):
            reconstruct(chain(saga_step("first")), (SagaCompensationRequested(),))

    def test_partial_parallel_failure_compensates_only_completed_siblings(self) -> None:
        program = then(
            parallel(saga_step("left"), saga_step("right")),
            saga_step("after"),
        )
        state = initial_state(program)
        for command in (
            StartSagaStep(SagaStepId("left")),
            StartSagaStep(SagaStepId("right")),
            SucceedSagaStep(SagaStepId("right")),
            FailSagaStep(SagaStepId("left")),
        ):
            state = apply(state, command)

        self.assertIs(state.status, SagaStatus.FAILED)
        self.assertEqual(compensation_candidates(state), (SagaStepId("right"),))
        self.assertIs(state.step(SagaStepId("left")).status, SagaStepStatus.FAILED)
        self.assertIs(state.step(SagaStepId("after")).status, SagaStepStatus.PENDING)

    def test_failure_and_compensation_failure_remain_explicit(self) -> None:
        program = chain(saga_step("first"), saga_step("second"))
        state = initial_state(program)
        for command in (
            StartSagaStep(SagaStepId("first")),
            SucceedSagaStep(SagaStepId("first")),
            StartSagaStep(SagaStepId("second")),
            FailSagaStep(SagaStepId("second")),
            BeginSagaCompensation(SagaStepId("first")),
            FailSagaCompensation(SagaStepId("first")),
        ):
            state = apply(state, command)

        self.assertIs(state.status, SagaStatus.PARTIALLY_COMPENSATED)
        self.assertIs(
            state.step(SagaStepId("first")).status,
            SagaStepStatus.COMPENSATION_FAILED,
        )
        self.assertEqual(state.failed_steps, (SagaStepId("second"),))

    def test_invalid_transitions_and_compensation_order_fail_closed(self) -> None:
        program = chain(saga_step("first"), saga_step("second"))
        state = initial_state(program)
        with self.assertRaises(SagaStateError):
            decide(state, SucceedSagaStep(SagaStepId("first")))

        state = apply(state, StartSagaStep(SagaStepId("first")))
        state = apply(state, SucceedSagaStep(SagaStepId("first")))
        state = apply(state, StartSagaStep(SagaStepId("second")))
        state = apply(state, FailSagaStep(SagaStepId("second")))
        with self.assertRaises(SagaStateError):
            decide(state, BeginSagaCompensation(SagaStepId("second")))

    def test_event_variants_are_data_and_replay_rejects_impossible_history(self) -> None:
        program = chain(saga_step("first"))
        events = (
            SagaStepStarted(SagaStepId("first")),
            SagaStepFailed(SagaStepId("first")),
        )
        state = reconstruct(program, events)
        self.assertIs(state.status, SagaStatus.FAILED)

        with self.assertRaises(SagaStateError):
            reconstruct(program, (SagaCompensationStarted(SagaStepId("first")),))
        with self.assertRaises(SagaStateError):
            reconstruct(
                program,
                (
                    *events,
                    SagaCompensationStarted(SagaStepId("first")),
                    SagaCompensationSucceeded(SagaStepId("first")),
                    SagaCompensationFailed(SagaStepId("first")),
                ),
            )


class ActivityPlanSagaBridgeTests(unittest.TestCase):
    def test_initial_state_for_plan_uses_activity_ids_and_compensation_specs(self) -> None:
        plan = ActivityPlan(
            (
                PlannedActivity(ActivityId("start-runtime"), StartRuntime(RuntimeTarget("docker"))),
                PlannedActivity(ActivityId("start-api"), StartNode(NodeTarget("api"))),
            )
        )

        state = SagaState.initial_for_plan(plan)

        self.assertEqual(
            tuple(value.step_id.value for value in state.steps),
            tuple(value.activity_id.value for value in plan.activities),
        )
        self.assertTrue(all(value.compensation_available for value in state.steps))


class SagaJournalSuccessorTests(unittest.TestCase):
    def test_success_reconstructs_from_canonical_activity_events(self) -> None:
        projection = project_activity_journal(
            plan(),
            (
                event(1, ActivityJournalEventKind.STEP_STARTED),
                event(2, ActivityJournalEventKind.STEP_SUCCEEDED),
            ),
        )

        self.assertIs(projection.state.steps[0].status, SagaStepStatus.SUCCEEDED)
        self.assertEqual(projection.in_flight, ())
        self.assertEqual(projection.uncertain, ())

    def test_run_compensation_admission_reconstructs_pure_saga_intent(self) -> None:
        projection = project_activity_journal(
            plan(),
            (
                event(1, ActivityJournalEventKind.STEP_STARTED),
                event(2, ActivityJournalEventKind.STEP_SUCCEEDED),
                run_event(3, ActivityJournalEventKind.RUN_COMPENSATION_STARTED),
            ),
        )

        self.assertTrue(projection.state.compensation_requested)
        self.assertIs(projection.state.status, SagaStatus.COMPENSATING)

    def test_uncertain_attempt_remains_running_but_is_not_in_flight(self) -> None:
        uncertainty = event(2, ActivityJournalEventKind.STEP_UNCERTAIN)
        projection = project_activity_journal(
            plan(),
            (event(1, ActivityJournalEventKind.STEP_STARTED), uncertainty),
        )

        self.assertIs(projection.state.steps[0].status, SagaStepStatus.RUNNING)
        self.assertEqual(projection.in_flight, ())
        self.assertEqual(projection.uncertain, (uncertainty,))

    def test_uncertainty_resolution_replays_into_terminal_step_evidence(self) -> None:
        for kind, expected in (
            (
                ActivityJournalEventKind.STEP_UNCERTAINTY_RESOLVED_SUCCEEDED,
                SagaStepStatus.SUCCEEDED,
            ),
            (
                ActivityJournalEventKind.STEP_UNCERTAINTY_RESOLVED_FAILED,
                SagaStepStatus.FAILED,
            ),
        ):
            with self.subTest(kind=kind):
                projection = project_activity_journal(
                    plan(),
                    (
                        event(1, ActivityJournalEventKind.STEP_STARTED),
                        event(2, ActivityJournalEventKind.STEP_UNCERTAIN),
                        event(3, kind),
                    ),
                )

                self.assertIs(projection.state.steps[0].status, expected)
                self.assertEqual(projection.in_flight, ())
                self.assertEqual(projection.uncertain, ())

    def test_uncertainty_resolution_requires_prior_uncertain_evidence(self) -> None:
        with self.assertRaisesRegex(ValueError, "prior uncertain evidence"):
            project_activity_journal(
                plan(),
                (
                    event(1, ActivityJournalEventKind.STEP_STARTED),
                    event(
                        2,
                        ActivityJournalEventKind.STEP_UNCERTAINTY_RESOLVED_SUCCEEDED,
                    ),
                ),
            )

    def test_unsupported_is_distinct_durable_failure_evidence(self) -> None:
        projection = project_activity_journal(
            plan(),
            (event(1, ActivityJournalEventKind.STEP_UNSUPPORTED),),
        )

        self.assertIs(projection.state.steps[0].status, SagaStepStatus.FAILED)

        after_discovery = project_activity_journal(
            plan(),
            (
                event(1, ActivityJournalEventKind.STEP_STARTED),
                event(2, ActivityJournalEventKind.STEP_UNSUPPORTED),
            ),
        )
        self.assertIs(after_discovery.state.steps[0].status, SagaStepStatus.FAILED)

    def test_foreign_and_impossible_histories_fail_closed(self) -> None:
        with self.assertRaises(ValueError):
            project_activity_journal(
                plan(),
                (
                    event(
                        1,
                        ActivityJournalEventKind.STEP_STARTED,
                        activity_id="foreign",
                    ),
                ),
            )
        with self.assertRaises(SagaStateError):
            project_activity_journal(
                plan(),
                (event(1, ActivityJournalEventKind.STEP_SUCCEEDED),),
            )

    def test_mixed_runs_and_nonmonotonic_ordinals_fail_closed(self) -> None:
        first = event(1, ActivityJournalEventKind.STEP_STARTED)
        foreign_run = event(
            2,
            ActivityJournalEventKind.STEP_SUCCEEDED,
            run_id="run-b",
        )
        with self.assertRaises(ValueError):
            project_activity_journal(plan(), (first, foreign_run))
        with self.assertRaises(ValueError):
            project_activity_journal(
                plan(),
                (
                    event(2, ActivityJournalEventKind.STEP_STARTED),
                    event(1, ActivityJournalEventKind.STEP_SUCCEEDED),
                ),
            )


if __name__ == "__main__":
    unittest.main()
