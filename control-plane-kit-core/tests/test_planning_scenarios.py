from __future__ import annotations

from collections import Counter
from dataclasses import replace
import unittest

from control_plane_kit_core.planning import (
    ActivityImpact,
    ReconcileNode,
    ReviewChange,
    RiskLevel,
    WaitForHealthy,
    compile_activity_plan,
)
from control_plane_kit_core.planning.scenarios import (
    AdmissionExpectation,
    ApprovalExpectation,
    EventExpectation,
    EventOrderExpectation,
    ExecutableScenario,
    ExecutionScenario,
    ExecutionScenarioExpectation,
    ExternalReadinessGated,
    ExternalReadinessRequirement,
    GraphAdvancementExpectation,
    NoChanges,
    NoRunExpected,
    OperationExpectation,
    ReviewBlockReason,
    ReviewBlocked,
    RunExpected,
    ScenarioActivityEventKind,
    ScenarioCoordinatorStatus,
    ScenarioObservationStatus,
    ScenarioRunStatus,
    execution_scenario_cases,
    execution_scenarios,
    operation_expectation,
    planning_scenarios,
)
from control_plane_kit_core.probe_intents import ProbeKind, ProbeOutcome
from control_plane_kit_core.topology import diff_graphs, validate_graph


RISK_RANK = {
    RiskLevel.INFORMATIONAL: 0,
    RiskLevel.LOW: 1,
    RiskLevel.MEDIUM: 2,
    RiskLevel.HIGH: 3,
    RiskLevel.CRITICAL: 4,
}


class PlanningScenarioAlgebraTests(unittest.TestCase):
    def test_catalog_ids_are_unique_and_every_graph_is_valid(self) -> None:
        scenarios = planning_scenarios()

        self.assertEqual(
            len({scenario.scenario_id for scenario in scenarios}),
            len(scenarios),
        )
        for scenario in scenarios:
            with self.subTest(scenario=scenario.scenario_id):
                self.assertTrue(validate_graph(scenario.current_graph).valid)
                self.assertTrue(validate_graph(scenario.desired_graph).valid)

    def test_every_scenario_compiles_to_its_typed_semantic_contract(self) -> None:
        for scenario in planning_scenarios():
            with self.subTest(scenario=scenario.scenario_id):
                plan = _compile(scenario)
                actual = Counter(
                    operation_expectation(activity.operation)
                    for activity in plan.activities
                )
                expected = Counter(scenario.expectation.operations)

                self.assertEqual(actual, expected)
                self.assertEqual(
                    max(
                        (activity.risk for activity in plan.activities),
                        key=RISK_RANK.__getitem__,
                        default=RiskLevel.INFORMATIONAL,
                    ),
                    scenario.expectation.max_risk,
                )
                self.assertEqual(
                    plan.ready_for_execution,
                    scenario.expectation.ready_for_execution,
                )

    def test_required_scenario_dependencies_survive_compilation(self) -> None:
        for scenario in planning_scenarios():
            with self.subTest(scenario=scenario.scenario_id):
                plan = _compile(scenario)
                activities = {
                    operation_expectation(activity.operation): activity
                    for activity in plan.activities
                }
                self.assertEqual(len(activities), len(plan.activities))

                for dependency in scenario.expectation.required_dependencies:
                    predecessor = activities[dependency.predecessor]
                    successor = activities[dependency.successor]
                    self.assertIn(
                        predecessor.activity_id,
                        {
                            value.predecessor
                            for value in successor.dependencies
                        },
                    )

    def test_scenario_compilation_is_pure_and_deterministic(self) -> None:
        for scenario in planning_scenarios():
            with self.subTest(scenario=scenario.scenario_id):
                current_before = scenario.current_graph.descriptor()
                desired_before = scenario.desired_graph.descriptor()

                first = _compile(scenario)
                second = _compile(scenario)

                self.assertEqual(first, second)
                self.assertEqual(scenario.current_graph.descriptor(), current_before)
                self.assertEqual(scenario.desired_graph.descriptor(), desired_before)

    def test_postgres_workflow_law_is_not_claimed_by_core_scenario_catalogue(self) -> None:
        for scenario in planning_scenarios():
            self.assertFalse(hasattr(scenario, "workspace_id"))
            self.assertFalse(hasattr(scenario, "plan_graph_transition"))


class ExecutionScenarioExpectationTests(unittest.TestCase):
    def test_every_planning_scenario_has_one_execution_expectation(self) -> None:
        planning = planning_scenarios()
        execution = execution_scenarios()

        self.assertEqual(
            tuple(scenario.scenario_id for scenario in execution),
            tuple(scenario.scenario_id for scenario in planning),
        )
        self.assertEqual(
            len({scenario.scenario_id for scenario in execution}),
            len(planning),
        )
        for planning_scenario, execution_scenario in zip(
            planning, execution, strict=True
        ):
            self.assertEqual(execution_scenario.planning, planning_scenario)
            self.assertEqual(
                execution_scenario.scenario_id,
                planning_scenario.scenario_id,
            )

    def test_safe_scenarios_expect_canonical_success_evidence(self) -> None:
        for scenario in execution_scenarios():
            if not isinstance(scenario.expectation.eligibility, ExecutableScenario):
                continue
            with self.subTest(scenario=scenario.scenario_id):
                expectation = scenario.expectation
                self.assertIs(expectation.approval, ApprovalExpectation.APPROVED)
                self.assertIs(expectation.admission, AdmissionExpectation.ADMITTED)
                self.assertEqual(
                    expectation.run,
                    RunExpected(
                        ScenarioRunStatus.SUCCEEDED,
                        ScenarioCoordinatorStatus.COMPLETED,
                    ),
                )
                self.assertIs(
                    expectation.graph_advancement,
                    GraphAdvancementExpectation.ADVANCED_TO_DESIRED,
                )
                self.assertIn(
                    EventOrderExpectation(
                        EventExpectation(ScenarioActivityEventKind.RUN_OPENED),
                        EventExpectation(ScenarioActivityEventKind.RUN_STARTED),
                    ),
                    expectation.event_order,
                )
                self.assertIn(
                    EventOrderExpectation(
                        EventExpectation(ScenarioActivityEventKind.RUN_SUCCEEDED),
                        EventExpectation(
                            ScenarioActivityEventKind.CURRENT_GRAPH_ADVANCED
                        ),
                    ),
                    expectation.event_order,
                )

    def test_health_operations_have_provider_neutral_observation_expectations(self) -> None:
        for scenario in execution_scenarios():
            if not isinstance(scenario.expectation.run, RunExpected):
                continue
            operations = tuple(
                operation
                for operation in scenario.planning.expectation.operations
                if operation.operation_type is WaitForHealthy
            )
            self.assertEqual(
                tuple(value.subject_id for value in scenario.expectation.observations),
                tuple(operation.target_id for operation in operations),
            )
            for observation in scenario.expectation.observations:
                self.assertIs(observation.status, ScenarioObservationStatus.HEALTHY)
                self.assertIs(observation.probe_kind, ProbeKind.APPLICATION_HEALTH)
                self.assertIs(observation.probe_outcome, ProbeOutcome.HEALTHY)

    def test_database_cutover_requires_external_readiness_and_creates_no_run(self) -> None:
        scenario = _scenario("switch-database-endpoint")

        self.assertEqual(
            scenario.expectation.eligibility,
            ExternalReadinessGated(
                ExternalReadinessRequirement.DATABASE_ENDPOINT_CUTOVER
            ),
        )
        self.assertIs(scenario.expectation.approval, ApprovalExpectation.APPROVED)
        self.assertIs(
            scenario.expectation.admission,
            AdmissionExpectation.NOT_ADMITTED,
        )
        self.assertEqual(scenario.expectation.run, NoRunExpected())
        self.assertEqual(scenario.expectation.events, ())
        self.assertIs(
            scenario.expectation.graph_advancement,
            GraphAdvancementExpectation.UNCHANGED,
        )

    def test_no_change_is_planning_evidence_without_approval_or_execution(self) -> None:
        scenario = _scenario("no-change")

        self.assertEqual(scenario.expectation.eligibility, NoChanges())
        self.assertIs(
            scenario.expectation.approval,
            ApprovalExpectation.NOT_REQUESTED,
        )
        self.assertIs(
            scenario.expectation.admission,
            AdmissionExpectation.NOT_ADMITTED,
        )
        self.assertEqual(scenario.expectation.run, NoRunExpected())
        self.assertEqual(scenario.planning.expectation.operations, ())
        self.assertIs(
            scenario.expectation.graph_advancement,
            GraphAdvancementExpectation.UNCHANGED,
        )

    def test_unsupported_transition_remains_review_blocked(self) -> None:
        scenario = _scenario("unsupported-implementation-transition")

        self.assertEqual(
            scenario.expectation.eligibility,
            ReviewBlocked(ReviewBlockReason.UNSUPPORTED_CHANGE),
        )
        self.assertIs(
            scenario.expectation.approval,
            ApprovalExpectation.NOT_REQUESTED,
        )
        self.assertEqual(scenario.expectation.run, NoRunExpected())

    def test_event_partial_order_must_reference_declared_events(self) -> None:
        opened = EventExpectation(ScenarioActivityEventKind.RUN_OPENED)
        started = EventExpectation(ScenarioActivityEventKind.RUN_STARTED)

        with self.assertRaisesRegex(ValueError, "declared semantic events"):
            ExecutionScenarioExpectation(
                eligibility=ExecutableScenario(),
                approval=ApprovalExpectation.REQUESTED,
                admission=AdmissionExpectation.NOT_ADMITTED,
                run=RunExpected(
                    ScenarioRunStatus.PAUSED,
                    ScenarioCoordinatorStatus.PAUSED,
                ),
                events=(opened,),
                event_order=(EventOrderExpectation(opened, started),),
            )

    def test_event_scope_reuses_canonical_activity_event_law(self) -> None:
        operation = planning_scenarios()[0].expectation.operations[0]

        with self.assertRaisesRegex(ValueError, "requires an operation"):
            EventExpectation(ScenarioActivityEventKind.STEP_STARTED)
        with self.assertRaisesRegex(ValueError, "cannot reference an operation"):
            EventExpectation(ScenarioActivityEventKind.RUN_STARTED, operation)

    def test_no_run_cannot_claim_runtime_evidence_or_graph_advancement(self) -> None:
        with self.assertRaisesRegex(ValueError, "no-run expectation"):
            ExecutionScenarioExpectation(
                eligibility=ReviewBlocked(ReviewBlockReason.UNSUPPORTED_CHANGE),
                approval=ApprovalExpectation.NOT_REQUESTED,
                admission=AdmissionExpectation.NOT_ADMITTED,
                run=NoRunExpected(),
                events=(EventExpectation(ScenarioActivityEventKind.RUN_OPENED),),
            )

        with self.assertRaisesRegex(ValueError, "cannot advance graph truth"):
            ExecutionScenarioExpectation(
                eligibility=ReviewBlocked(ReviewBlockReason.UNSUPPORTED_CHANGE),
                approval=ApprovalExpectation.NOT_REQUESTED,
                admission=AdmissionExpectation.NOT_ADMITTED,
                run=NoRunExpected(),
                graph_advancement=GraphAdvancementExpectation.ADVANCED_TO_DESIRED,
            )

    def test_execution_expectations_only_reference_planning_operations(self) -> None:
        scenario = planning_scenarios()[0]
        foreign = OperationExpectation(WaitForHealthy, "foreign-node")
        expectation = replace(
            execution_scenarios()[0].expectation,
            events=(
                EventExpectation(ScenarioActivityEventKind.STEP_STARTED, foreign),
                EventExpectation(ScenarioActivityEventKind.STEP_SUCCEEDED, foreign),
            ),
            event_order=(),
        )

        with self.assertRaisesRegex(ValueError, "canonical planning operations"):
            ExecutionScenario(scenario, expectation)

    def test_execution_expectations_reference_planning_operations_not_strings(self) -> None:
        scenario = _scenario("fresh-deployment")
        for event in scenario.expectation.events:
            if event.operation is not None:
                self.assertIsInstance(event.operation, OperationExpectation)
                self.assertNotIsInstance(event.operation, str)

    def test_catalog_is_pure_and_deterministic(self) -> None:
        first = execution_scenarios()
        second = execution_scenarios()

        self.assertEqual(first, second)
        self.assertIsNot(first, second)
        self.assertEqual(
            tuple(scenario.planning.expectation for scenario in first),
            tuple(scenario.expectation for scenario in planning_scenarios()),
        )

    def test_acceptance_case_catalog_is_closed_unique_and_deterministic(self) -> None:
        first = execution_scenario_cases()
        second = execution_scenario_cases()

        self.assertEqual(first, second)
        self.assertIsNot(first, second)
        self.assertEqual(len({case.case_id for case in first}), len(first))
        self.assertEqual(
            {
                case.scenario.scenario_id
                for case in first
                if case.case_id.startswith("canonical:")
            },
            {scenario.scenario_id for scenario in execution_scenarios()},
        )
        self.assertTrue(
            {
                "independent-leaf-failure",
                "shared-leaf-failure",
                "uncertain-paused",
                "uncertainty-resolved-and-resumed",
                "reverse-order-compensation",
                "compensation-failure",
            }.issubset({case.case_id for case in first})
        )


def _compile(scenario):
    current = validate_graph(scenario.current_graph)
    desired = validate_graph(scenario.desired_graph)
    current.require_valid()
    desired.require_valid()
    return compile_activity_plan(diff_graphs(current, desired))


def _scenario(scenario_id: str):
    return next(
        scenario
        for scenario in execution_scenarios()
        if scenario.scenario_id == scenario_id
    )


if __name__ == "__main__":
    unittest.main()
