import unittest

from control_plane_kit import DeploymentGraph, compile_recipe
from control_plane_kit.application.deploy import (
    InitialDeployment,
    NoOpDeployment,
    TeardownDeployment,
    UpdateDeployment,
    classify_transition,
)
from examples.router_swap import recipe


class DeploymentApplicationValueTests(unittest.TestCase):
    def setUp(self) -> None:
        self.empty = DeploymentGraph("router-swap")
        self.blue = compile_recipe(recipe("api-v1"))
        self.green = compile_recipe(recipe("api-v2"))

    def test_one_interpreter_classifies_all_transition_forms(self) -> None:
        transitions = (
            classify_transition(self.empty, self.blue),
            classify_transition(self.blue, self.green),
            classify_transition(self.blue, self.empty),
            classify_transition(self.blue, self.blue),
        )

        match transitions:
            case (
                InitialDeployment(),
                UpdateDeployment(),
                TeardownDeployment(),
                NoOpDeployment(),
            ):
                pass
            case _:
                self.fail(f"unexpected transition forms: {transitions!r}")

    def test_transition_constructors_make_invalid_forms_unrepresentable(self) -> None:
        invalid = (
            lambda: InitialDeployment(self.blue, self.green),
            lambda: UpdateDeployment(self.empty, self.green),
            lambda: TeardownDeployment(self.empty, self.empty),
            lambda: NoOpDeployment(self.blue, self.green),
        )

        for construct in invalid:
            with self.subTest(construct=construct), self.assertRaises(ValueError):
                construct()

    def test_two_distinct_empty_graphs_are_an_update_not_a_no_op(self) -> None:
        transition = classify_transition(
            DeploymentGraph("before"),
            DeploymentGraph("after"),
        )

        self.assertIsInstance(transition, UpdateDeployment)


if __name__ == "__main__":
    unittest.main()
