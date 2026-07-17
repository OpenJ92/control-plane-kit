from dataclasses import dataclass
import unittest

from control_plane_kit.saga import (
    End,
    ParallelNode,
    SagaProgramError,
    SagaStep,
    SagaStepId,
    StepNode,
    chain,
    initial_state,
    parallel,
    program_steps,
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


class SagaProgramTests(unittest.TestCase):
    def test_chain_and_then_build_immutable_callback_free_syntax(self):
        first = saga_step("first")
        second = saga_step("second")
        original = chain(first)

        appended = then(original, second)

        self.assertIsInstance(original, StepNode)
        self.assertIsInstance(original.next, End)
        self.assertEqual(
            tuple(value.step_id.value for value in program_steps(appended)),
            ("first", "second"),
        )
        self.assertEqual(appended.step.effect, DemoEffect("do:first"))
        self.assertFalse(callable(appended.step.effect))

    def test_parallel_preserves_declared_fan_out_and_shared_continuation(self):
        fan_out = parallel(saga_step("left"), saga_step("right"))
        program = then(fan_out, saga_step("after"))

        self.assertIsInstance(program, ParallelNode)
        self.assertEqual(len(program.branches), 2)
        self.assertEqual(
            tuple(value.step_id.value for value in program_steps(program)),
            ("left", "right", "after"),
        )

    def test_invalid_parallel_and_duplicate_identity_fail_structurally(self):
        with self.assertRaises(SagaProgramError):
            parallel(saga_step("only"))
        with self.assertRaises(SagaProgramError):
            initial_state(chain(saga_step("same"), saga_step("same")))


if __name__ == "__main__":
    unittest.main()
