from dataclasses import dataclass
import unittest

from control_plane_kit.saga import (
    BeginSagaCompensation,
    CancelSaga,
    FailSagaCompensation,
    FailSagaStep,
    SagaCompensationFailed,
    SagaCompensationStarted,
    SagaCompensationSucceeded,
    SagaStateError,
    SagaStatus,
    SagaStep,
    SagaStepFailed,
    SagaStepId,
    SagaStepStarted,
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


def apply(state, command):
    return evolve_all(state, decide(state, command))


class SagaStateTests(unittest.TestCase):
    def test_successful_sequence_reconstructs_from_the_same_events(self):
        program = chain(saga_step("first"), saga_step("second"))
        state = initial_state(program)
        events = (
            SagaStepStarted(SagaStepId("first")),
            SagaStepSucceeded(SagaStepId("first")),
            SagaStepStarted(SagaStepId("second")),
            SagaStepSucceeded(SagaStepId("second")),
        )

        evolved = evolve_all(state, events)

        self.assertEqual(evolved, reconstruct(program, events))
        self.assertIs(evolved.status, SagaStatus.SUCCEEDED)
        self.assertEqual(
            tuple(value.value for value in evolved.completion_order),
            ("first", "second"),
        )

    def test_parallel_completion_evidence_determines_reverse_compensation(self):
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

    def test_partial_parallel_failure_compensates_only_completed_siblings(self):
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
        self.assertEqual(
            compensation_candidates(state),
            (SagaStepId("right"),),
        )
        self.assertIs(
            state.step(SagaStepId("left")).status,
            SagaStepStatus.FAILED,
        )
        self.assertIs(
            state.step(SagaStepId("after")).status,
            SagaStepStatus.PENDING,
        )

    def test_failure_and_compensation_failure_remain_explicit(self):
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

    def test_invalid_transitions_and_compensation_order_fail_closed(self):
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

    def test_event_variants_are_data_and_replay_rejects_impossible_history(self):
        program = chain(saga_step("first"))
        events = (
            SagaStepStarted(SagaStepId("first")),
            SagaStepFailed(SagaStepId("first")),
        )
        state = reconstruct(program, events)
        self.assertIs(state.status, SagaStatus.FAILED)

        with self.assertRaises(SagaStateError):
            reconstruct(
                program,
                (SagaCompensationStarted(SagaStepId("first")),),
            )
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


if __name__ == "__main__":
    unittest.main()
