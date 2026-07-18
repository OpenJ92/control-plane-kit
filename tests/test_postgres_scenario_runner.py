from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from itertools import count
import os

import psycopg

from control_plane_kit.read_services import InstanceReadService
from control_plane_kit.stores import (
    GraphVersionRecord,
    PostgresUnitOfWork,
    WorkspaceRecord,
)
from control_plane_kit.workflows import (
    ActivityPlanningCommandService,
    ApprovalCommandService,
    CoordinatorStatus,
    CurrentGraphAdvancementCommandService,
    DesiredGraphCommandService,
    ExecutionAdmissionCommandService,
    ExecutionCoordinator,
    ExecutionWorkerAuthority,
    OperationCommandService,
    RunLifecycleCommandService,
)
from examples.scenarios import (
    EventOrderExpectation,
    ExecutionScenario,
    execution_scenario_cases,
    execution_scenarios,
)
from examples.scenarios.runner import (
    ScenarioEffectDirective,
    ScenarioEffectDisposition,
    ScenarioEffectInterpreter,
    ScenarioEffectProgram,
    ScenarioRunContext,
    ScenarioRunnerServices,
    evaluate_execution_scenario,
    run_execution_scenario,
)
from examples.scenarios.workflow import PlanningWorkflowServices
from tests.postgres_case import PostgresStoreTestCase


class Ids:
    def __init__(self, prefix: str) -> None:
        self._prefix = prefix
        self._values = count(1)

    def __call__(self) -> str:
        return f"{self._prefix}-{next(self._values)}"


class TrackingUnitOfWork:
    def __init__(self, inner: PostgresUnitOfWork, tracker: "TransactionTracker") -> None:
        self._inner = inner
        self._tracker = tracker

    def __enter__(self):
        self._inner.__enter__()
        self._tracker.active += 1
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return self._inner.__exit__(exc_type, exc_value, traceback)
        finally:
            self._tracker.active -= 1

    @property
    def stores(self):
        return self._inner.stores

    def commit(self) -> None:
        self._inner.commit()


class TransactionTracker:
    def __init__(self) -> None:
        self.active = 0

    def __call__(self):
        return TrackingUnitOfWork(
            PostgresUnitOfWork(
                lambda: psycopg.connect(os.environ["CPK_TEST_DATABASE_URL"])
            ),
            self,
        )


class PostgresScenarioRunnerTests(PostgresStoreTestCase):
    def test_complete_acceptance_corpus_uses_canonical_pipeline(self):
        for ordinal, case in enumerate(execution_scenario_cases(), start=1):
            with self.subTest(case=case.case_id):
                context, services, interpreter = self._prepare(
                    case.scenario,
                    program=case.effects,
                    workspace_suffix=f"corpus-{ordinal}",
                )

                result = run_execution_scenario(
                    services,
                    case.scenario,
                    context,
                    case.recovery,
                )

                result.evaluation.require_satisfied()
                self.assertEqual(self._tracker.active, 0)
                if result.opened is None:
                    self.assertEqual(interpreter.requests, [])

    def test_executable_scenario_uses_canonical_pipeline_and_projection(self):
        scenario = self._scenario("backend-switch")
        context, services, interpreter = self._prepare(scenario)

        result = run_execution_scenario(services, scenario, context)

        result.evaluation.require_satisfied()
        self.assertGreater(len(interpreter.requests), 0)
        self.assertEqual(self._tracker.active, 0)
        self.assertEqual(
            result.workspace_view.workspace.current_graph_id,
            result.planning.desired_graph.graph_version.graph_id,
        )

    def test_readiness_gated_scenario_records_approval_but_no_effect(self):
        scenario = self._scenario("switch-database-endpoint")
        context, services, interpreter = self._prepare(scenario)

        result = run_execution_scenario(services, scenario, context)

        result.evaluation.require_satisfied()
        self.assertIsNotNone(result.approval)
        self.assertIsNone(result.admission)
        self.assertIsNone(result.opened)
        self.assertEqual(interpreter.requests, [])
        self.assertEqual(
            result.workspace_view.workspace.current_graph_id,
            context.current_graph_id,
        )

    def test_review_blocked_scenario_never_requests_approval_or_effect(self):
        scenario = self._scenario("unsupported-implementation-transition")
        context, services, interpreter = self._prepare(scenario)

        result = run_execution_scenario(services, scenario, context)

        result.evaluation.require_satisfied()
        self.assertIsNone(result.planning.approval)
        self.assertIsNone(result.approval)
        self.assertIsNone(result.admission)
        self.assertEqual(interpreter.requests, [])

    def test_no_change_records_plan_but_no_approval_run_or_effect(self):
        scenario = self._scenario("no-change")
        context, services, interpreter = self._prepare(scenario)

        result = run_execution_scenario(services, scenario, context)

        result.evaluation.require_satisfied()
        self.assertIsNone(result.planning.approval)
        self.assertIsNone(result.approval)
        self.assertIsNone(result.admission)
        self.assertIsNone(result.opened)
        self.assertEqual(result.planning.plan.plan_record.plan.activities, ())
        self.assertEqual(interpreter.requests, [])
        self.assertEqual(
            result.workspace_view.workspace.current_graph_id,
            context.current_graph_id,
        )

    def test_programmed_failure_uses_real_coordinator_and_does_not_advance(self):
        scenario = self._scenario("backend-switch")
        operation = scenario.planning.expectation.operations[0]
        program = ScenarioEffectProgram(
            (
                ScenarioEffectDirective(
                    operation,
                    ScenarioEffectDisposition.FAIL,
                ),
            )
        )
        context, services, interpreter = self._prepare(scenario, program=program)

        result = run_execution_scenario(services, scenario, context)

        self.assertIsNotNone(result.coordinator)
        assert result.coordinator is not None
        self.assertIs(result.coordinator.status, CoordinatorStatus.FAILED)
        self.assertEqual(
            result.workspace_view.workspace.current_graph_id,
            context.current_graph_id,
        )
        self.assertGreater(len(interpreter.requests), 0)
        self.assertFalse(result.evaluation.satisfied)

    def test_programmed_uncertainty_remains_visible_without_advancement(self):
        scenario = self._scenario("backend-switch")
        operation = scenario.planning.expectation.operations[0]
        program = ScenarioEffectProgram(
            (
                ScenarioEffectDirective(
                    operation,
                    ScenarioEffectDisposition.UNCERTAIN,
                ),
            )
        )
        context, services, interpreter = self._prepare(scenario, program=program)

        result = run_execution_scenario(services, scenario, context)

        self.assertIsNotNone(result.coordinator)
        assert result.coordinator is not None
        self.assertIs(result.coordinator.status, CoordinatorStatus.UNCERTAIN)
        self.assertEqual(
            result.workspace_view.workspace.current_graph_id,
            context.current_graph_id,
        )
        self.assertGreater(len(interpreter.requests), 0)
        self.assertFalse(result.evaluation.satisfied)

    def test_repeated_runs_have_distinct_workspaces_and_fake_state(self):
        scenario = self._scenario("backend-switch")
        first_context, first_services, first_interpreter = self._prepare(
            scenario,
            workspace_suffix="first",
        )
        second_context, second_services, second_interpreter = self._prepare(
            scenario,
            workspace_suffix="second",
        )

        first = run_execution_scenario(first_services, scenario, first_context)
        second = run_execution_scenario(second_services, scenario, second_context)

        first.evaluation.require_satisfied()
        second.evaluation.require_satisfied()
        self.assertNotEqual(first_context.workspace_id, second_context.workspace_id)
        self.assertNotEqual(
            first.planning.plan.plan_record.plan_id,
            second.planning.plan.plan_record.plan_id,
        )
        self.assertIsNot(first_interpreter.requests, second_interpreter.requests)
        self.assertEqual(
            len(first_interpreter.requests),
            len(second_interpreter.requests),
        )
        self.assertTrue(
            all(
                request.graphs.workspace_id == first_context.workspace_id
                for request in first_interpreter.requests
            )
        )
        self.assertTrue(
            all(
                request.graphs.workspace_id == second_context.workspace_id
                for request in second_interpreter.requests
            )
        )

    def test_scenario_order_does_not_change_semantic_results(self):
        scenarios = (
            self._scenario("backend-switch"),
            self._scenario("switch-database-endpoint"),
            self._scenario("unsupported-implementation-transition"),
        )

        forward = self._run_order(scenarios, order="forward")
        reverse = self._run_order(tuple(reversed(scenarios)), order="reverse")

        self.assertEqual(
            {scenario_id: result.evaluation for scenario_id, result in forward},
            {scenario_id: result.evaluation for scenario_id, result in reverse},
        )
        self.assertTrue(all(result.evaluation.satisfied for _, result in forward))
        self.assertTrue(all(result.evaluation.satisfied for _, result in reverse))

    def test_semantic_diagnostic_reports_event_order_not_generated_values(self):
        scenario = self._scenario("backend-switch")
        context, services, _ = self._prepare(scenario)
        completed = run_execution_scenario(services, scenario, context)
        order = scenario.expectation.event_order[0]
        contradicted = ExecutionScenario(
            scenario.planning,
            replace(
                scenario.expectation,
                event_order=(
                    EventOrderExpectation(order.successor, order.predecessor),
                ),
            ),
        )

        evaluated = evaluate_execution_scenario(contradicted, completed)

        self.assertFalse(evaluated.satisfied)
        self.assertEqual(len(evaluated.findings), 1)
        self.assertIn("event order violated", evaluated.findings[0])
        self.assertIsNotNone(completed.opened)
        assert completed.opened is not None
        self.assertNotIn(completed.opened.run.run_id, evaluated.findings[0])

    def _run_order(self, scenarios, *, order: str):
        results = []
        for ordinal, scenario in enumerate(scenarios, start=1):
            context, services, _ = self._prepare(
                scenario,
                workspace_suffix=f"{order}-{ordinal}",
            )
            results.append(
                (
                    scenario.scenario_id,
                    run_execution_scenario(services, scenario, context),
                )
            )
        return tuple(results)

    def _prepare(
        self,
        scenario,
        *,
        program=ScenarioEffectProgram(),
        workspace_suffix: str | None = None,
    ):
        suffix = "" if workspace_suffix is None else f":{workspace_suffix}"
        workspace_id = f"scenario-workspace:{scenario.scenario_id}{suffix}"
        current_graph_id = f"scenario-current:{scenario.scenario_id}{suffix}"
        self.stores.workspace.create(WorkspaceRecord(workspace_id, scenario.planning.title))
        self.stores.graph_topology.save(
            GraphVersionRecord.from_graph(
                graph_id=current_graph_id,
                workspace_id=workspace_id,
                version=1,
                graph=scenario.planning.current_graph,
                created_by="scenario-fixture",
                created_at="2026-07-18T00:00:00Z",
            )
        )
        self.stores.workspace.set_current_graph(workspace_id, current_graph_id)
        self.stores.workspace.set_desired_graph(workspace_id, current_graph_id)

        self._tracker = TransactionTracker()
        prefix = f"{scenario.scenario_id}{suffix}"
        approval = ApprovalCommandService(
            self._tracker,
            clock=_text_clock,
            id_factory=Ids(f"{prefix}:approval"),
        )
        planning = PlanningWorkflowServices(
            OperationCommandService(
                self._tracker,
                clock=_text_clock,
                id_factory=Ids(f"{prefix}:operation"),
            ),
            DesiredGraphCommandService(
                self._tracker,
                clock=_text_clock,
                id_factory=Ids(f"{prefix}:graph"),
            ),
            ActivityPlanningCommandService(
                self._tracker,
                clock=_text_clock,
                id_factory=Ids(f"{prefix}:plan"),
            ),
            approval,
        )
        lifecycle = RunLifecycleCommandService(
            self._tracker,
            clock=_text_clock,
            id_factory=Ids(f"{prefix}:run"),
        )
        interpreter = ScenarioEffectInterpreter(
            program=program,
            transaction_active=lambda: self._tracker.active > 0,
        )
        coordinator = ExecutionCoordinator(
            self._tracker,
            lifecycle,
            interpreter,
            clock=_datetime_clock,
            id_factory=Ids(f"{prefix}:coordinator"),
        )
        services = ScenarioRunnerServices(
            planning=planning,
            approvals=approval,
            admission=ExecutionAdmissionCommandService(
                self._tracker,
                clock=_text_clock,
                id_factory=Ids(f"{prefix}:admission"),
            ),
            lifecycle=lifecycle,
            coordinator=coordinator,
            advancement=CurrentGraphAdvancementCommandService(
                self._tracker,
                clock=_text_clock,
                id_factory=Ids(f"{prefix}:advance"),
            ),
            reads=InstanceReadService(
                workspace_store=self.stores.workspace,
                graph_topology_store=self.stores.graph_topology,
                activity_history_store=self.stores.activity_history,
                execution_store=self.stores.execution,
                observed_state_store=self.stores.observed_state,
                clock=_datetime_clock,
            ),
        )
        return (
            ScenarioRunContext(
                workspace_id=workspace_id,
                current_graph_id=current_graph_id,
                actor_id="scenario-operator",
                approver_id="scenario-approver",
                worker=ExecutionWorkerAuthority(
                    "scenario-worker",
                    ("execution:operate",),
                ),
                lease_expires_at="2026-07-18T01:00:00Z",
            ),
            services,
            interpreter,
        )

    @staticmethod
    def _scenario(scenario_id: str):
        return next(
            scenario
            for scenario in execution_scenarios()
            if scenario.scenario_id == scenario_id
        )


def _text_clock() -> str:
    return "2026-07-18T00:10:00Z"


def _datetime_clock() -> datetime:
    return datetime(2026, 7, 18, 0, 10, tzinfo=timezone.utc)
