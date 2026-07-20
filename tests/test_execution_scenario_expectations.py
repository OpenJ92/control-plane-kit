from __future__ import annotations

from dataclasses import replace
from unittest import TestCase, main

from control_plane_kit.execution import ActivityEventKind, ActivityRunStatus
from control_plane_kit.core.planning import WaitForHealthy
from control_plane_kit.workflows import CoordinatorStatus
from examples.scenarios import (
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
    ReviewBlocked,
    ReviewBlockReason,
    RunExpected,
    execution_scenarios,
    execution_scenario_cases,
    planning_scenarios,
)
from examples.scenarios.model import OperationExpectation


class ExecutionScenarioExpectationTests(TestCase):
    def test_every_planning_scenario_has_one_execution_expectation(self):
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

    def test_safe_scenarios_expect_canonical_success_evidence(self):
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
                        ActivityRunStatus.SUCCEEDED,
                        CoordinatorStatus.COMPLETED,
                    ),
                )
                self.assertIs(
                    expectation.graph_advancement,
                    GraphAdvancementExpectation.ADVANCED_TO_DESIRED,
                )
                self.assertIn(
                    EventOrderExpectation(
                        EventExpectation(ActivityEventKind.RUN_OPENED),
                        EventExpectation(ActivityEventKind.RUN_STARTED),
                    ),
                    expectation.event_order,
                )
                self.assertIn(
                    EventOrderExpectation(
                        EventExpectation(ActivityEventKind.RUN_SUCCEEDED),
                        EventExpectation(ActivityEventKind.CURRENT_GRAPH_ADVANCED),
                    ),
                    expectation.event_order,
                )

    def test_health_operations_have_provider_neutral_observation_expectations(self):
        for scenario in execution_scenarios():
            operations = tuple(
                operation
                for operation in scenario.planning.expectation.operations
                if operation.operation_type is WaitForHealthy
            )
            self.assertEqual(
                tuple(value.subject_id for value in scenario.expectation.observations),
                tuple(operation.target_id for operation in operations),
            )

    def test_database_cutover_requires_external_readiness_and_creates_no_run(self):
        scenario = self._scenario("switch-database-endpoint")

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

    def test_no_change_is_planning_evidence_without_approval_or_execution(self):
        scenario = self._scenario("no-change")

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

    def test_unsupported_transition_remains_review_blocked(self):
        scenario = self._scenario("unsupported-implementation-transition")

        self.assertEqual(
            scenario.expectation.eligibility,
            ReviewBlocked(ReviewBlockReason.UNSUPPORTED_CHANGE),
        )
        self.assertIs(
            scenario.expectation.approval,
            ApprovalExpectation.NOT_REQUESTED,
        )
        self.assertEqual(scenario.expectation.run, NoRunExpected())

    def test_event_partial_order_must_reference_declared_events(self):
        opened = EventExpectation(ActivityEventKind.RUN_OPENED)
        started = EventExpectation(ActivityEventKind.RUN_STARTED)

        with self.assertRaisesRegex(ValueError, "declared semantic events"):
            ExecutionScenarioExpectation(
                eligibility=ExecutableScenario(),
                approval=ApprovalExpectation.REQUESTED,
                admission=AdmissionExpectation.NOT_ADMITTED,
                run=RunExpected(
                    ActivityRunStatus.PAUSED,
                    CoordinatorStatus.PAUSED,
                ),
                events=(opened,),
                event_order=(EventOrderExpectation(opened, started),),
            )

    def test_event_scope_reuses_canonical_activity_event_law(self):
        operation = planning_scenarios()[0].expectation.operations[0]

        with self.assertRaisesRegex(ValueError, "requires an operation"):
            EventExpectation(ActivityEventKind.STEP_STARTED)
        with self.assertRaisesRegex(ValueError, "cannot reference an operation"):
            EventExpectation(ActivityEventKind.RUN_STARTED, operation)

    def test_no_run_cannot_claim_runtime_evidence_or_graph_advancement(self):
        with self.assertRaisesRegex(ValueError, "no-run expectation"):
            ExecutionScenarioExpectation(
                eligibility=ReviewBlocked(ReviewBlockReason.UNSUPPORTED_CHANGE),
                approval=ApprovalExpectation.NOT_REQUESTED,
                admission=AdmissionExpectation.NOT_ADMITTED,
                run=NoRunExpected(),
                events=(EventExpectation(ActivityEventKind.RUN_OPENED),),
            )

        with self.assertRaisesRegex(ValueError, "cannot advance graph truth"):
            ExecutionScenarioExpectation(
                eligibility=ReviewBlocked(ReviewBlockReason.UNSUPPORTED_CHANGE),
                approval=ApprovalExpectation.NOT_REQUESTED,
                admission=AdmissionExpectation.NOT_ADMITTED,
                run=NoRunExpected(),
                graph_advancement=GraphAdvancementExpectation.ADVANCED_TO_DESIRED,
            )

    def test_execution_expectations_only_reference_planning_operations(self):
        scenario = planning_scenarios()[0]
        foreign = OperationExpectation(WaitForHealthy, "foreign-node")
        expectation = replace(
            execution_scenarios()[0].expectation,
            events=(
                EventExpectation(ActivityEventKind.STEP_STARTED, foreign),
                EventExpectation(ActivityEventKind.STEP_SUCCEEDED, foreign),
            ),
            event_order=(),
        )

        with self.assertRaisesRegex(ValueError, "canonical planning operations"):
            ExecutionScenario(scenario, expectation)

    def test_catalog_is_pure_and_deterministic(self):
        first = execution_scenarios()
        second = execution_scenarios()

        self.assertEqual(first, second)
        self.assertIsNot(first, second)
        self.assertEqual(
            tuple(scenario.planning.expectation for scenario in first),
            tuple(scenario.expectation for scenario in planning_scenarios()),
        )

    def test_acceptance_case_catalog_is_closed_unique_and_deterministic(self):
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

    @staticmethod
    def _scenario(scenario_id: str):
        return next(
            scenario
            for scenario in execution_scenarios()
            if scenario.scenario_id == scenario_id
        )


if __name__ == "__main__":
    main()
