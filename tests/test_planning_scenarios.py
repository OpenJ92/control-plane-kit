from __future__ import annotations

from collections import Counter
import os
from unittest import TestCase, main

import psycopg

from control_plane_kit.planning import RiskLevel, compile_activity_plan
from control_plane_kit.stores import (
    GraphVersionRecord,
    PostgresUnitOfWork,
    WorkspaceRecord,
)
from control_plane_kit.topology import diff_graphs, validate_graph
from control_plane_kit.workflows import (
    ActivityPlanningCommandService,
    ApprovalCommandService,
    DesiredGraphCommandService,
    OperationCommandService,
)
from examples.scenarios import (
    PlanningWorkflowServices,
    operation_expectation,
    plan_graph_transition,
    planning_scenarios,
)
from tests.postgres_case import PostgresStoreTestCase


RISK_RANK = {
    RiskLevel.INFORMATIONAL: 0,
    RiskLevel.LOW: 1,
    RiskLevel.MEDIUM: 2,
    RiskLevel.HIGH: 3,
    RiskLevel.CRITICAL: 4,
}


class Sequence:
    def __init__(self, *values: str) -> None:
        self._values = list(values)

    def __call__(self) -> str:
        return self._values.pop(0)


class PlanningScenarioAlgebraTests(TestCase):
    def test_catalog_ids_are_unique_and_every_graph_is_valid(self):
        scenarios = planning_scenarios()

        self.assertEqual(
            len({scenario.scenario_id for scenario in scenarios}),
            len(scenarios),
        )
        for scenario in scenarios:
            with self.subTest(scenario=scenario.scenario_id):
                self.assertTrue(validate_graph(scenario.current_graph).valid)
                self.assertTrue(validate_graph(scenario.desired_graph).valid)

    def test_every_scenario_compiles_to_its_typed_semantic_contract(self):
        for scenario in planning_scenarios():
            with self.subTest(scenario=scenario.scenario_id):
                plan = self._compile(scenario)
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

    def test_required_scenario_dependencies_survive_compilation(self):
        for scenario in planning_scenarios():
            with self.subTest(scenario=scenario.scenario_id):
                plan = self._compile(scenario)
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

    def test_scenario_compilation_is_pure_and_deterministic(self):
        for scenario in planning_scenarios():
            with self.subTest(scenario=scenario.scenario_id):
                current_before = scenario.current_graph.descriptor()
                desired_before = scenario.desired_graph.descriptor()

                first = self._compile(scenario)
                second = self._compile(scenario)

                self.assertEqual(first, second)
                self.assertEqual(scenario.current_graph.descriptor(), current_before)
                self.assertEqual(scenario.desired_graph.descriptor(), desired_before)

    @staticmethod
    def _compile(scenario):
        current = validate_graph(scenario.current_graph)
        desired = validate_graph(scenario.desired_graph)
        current.require_valid()
        desired.require_valid()
        return compile_activity_plan(
            diff_graphs(current, desired)
        )


class PlanningScenarioWorkflowTests(PostgresStoreTestCase):
    def test_every_scenario_runs_through_the_postgres_planning_workflow(self):
        for ordinal, scenario in enumerate(planning_scenarios(), start=1):
            with self.subTest(scenario=scenario.scenario_id):
                workspace_id = f"workspace-{ordinal}"
                current_graph_id = f"current-{ordinal}"
                self.stores.workspace.create(
                    WorkspaceRecord(workspace_id, scenario.title)
                )
                self.stores.graph_topology.save(
                    GraphVersionRecord.from_graph(
                        graph_id=current_graph_id,
                        workspace_id=workspace_id,
                        version=1,
                        graph=scenario.current_graph,
                        created_by="scenario-runner",
                        created_at="2026-07-16T00:00:00Z",
                    )
                )
                self.stores.workspace.set_current_graph(
                    workspace_id,
                    current_graph_id,
                )
                self.stores.workspace.set_desired_graph(
                    workspace_id,
                    current_graph_id,
                )

                result = plan_graph_transition(
                    self._services(scenario.scenario_id),
                    workspace_id=workspace_id,
                    actor_id="scenario-runner",
                    title=scenario.title,
                    approval_comment=scenario.approval_comment,
                    current_graph_id=current_graph_id,
                    expected_desired_graph_id=current_graph_id,
                    desired_graph=scenario.desired_graph,
                    idempotency_prefix=scenario.scenario_id,
                )

                self.assertEqual(
                    Counter(
                        operation_expectation(activity.operation)
                        for activity in result.plan.plan_record.plan.activities
                    ),
                    Counter(scenario.expectation.operations),
                )
                if scenario.expectation.ready_for_execution:
                    self.assertIsNotNone(result.approval)
                    assert result.approval is not None
                    self.assertEqual(
                        result.approval.request.max_risk,
                        scenario.expectation.max_risk,
                    )
                else:
                    self.assertIsNone(result.approval)
                    self.assertEqual(
                        self.stores.activity_history.approval_requests_for_session(
                            result.session.session.session_id
                        ),
                        (),
                    )
                self.assertEqual(
                    result.descriptor()["runtime_effects_executed"],
                    False,
                )
                self.assertEqual(
                    self.stores.execution.runs_for_plan(
                        result.plan.plan_record.plan_id
                    ),
                    (),
                )

    @staticmethod
    def _services(prefix: str) -> PlanningWorkflowServices:
        unit_of_work_factory = lambda: PostgresUnitOfWork(
            lambda: psycopg.connect(os.environ["CPK_TEST_DATABASE_URL"])
        )
        return PlanningWorkflowServices(
            operations=OperationCommandService(
                unit_of_work_factory,
                clock=lambda: "2026-07-16T00:01:00Z",
                id_factory=Sequence(f"{prefix}:session", f"{prefix}:session-action"),
            ),
            desired_graphs=DesiredGraphCommandService(
                unit_of_work_factory,
                clock=lambda: "2026-07-16T00:02:00Z",
                id_factory=Sequence(f"{prefix}:graph", f"{prefix}:graph-action"),
            ),
            plans=ActivityPlanningCommandService(
                unit_of_work_factory,
                clock=lambda: "2026-07-16T00:03:00Z",
                id_factory=Sequence(f"{prefix}:plan", f"{prefix}:plan-action"),
            ),
            approvals=ApprovalCommandService(
                unit_of_work_factory,
                clock=lambda: "2026-07-16T00:04:00Z",
                id_factory=Sequence(
                    f"{prefix}:approval",
                    f"{prefix}:approval-action",
                ),
            ),
        )


if __name__ == "__main__":
    main()
