from __future__ import annotations

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
from examples.scenarios import execution_scenarios
from examples.scenarios.runner import (
    ScenarioEffectDirective,
    ScenarioEffectDisposition,
    ScenarioEffectInterpreter,
    ScenarioEffectProgram,
    ScenarioRunContext,
    ScenarioRunnerServices,
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

    def _prepare(self, scenario, *, program=ScenarioEffectProgram()):
        workspace_id = f"scenario-workspace:{scenario.scenario_id}"
        current_graph_id = f"scenario-current:{scenario.scenario_id}"
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
        prefix = scenario.scenario_id
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
