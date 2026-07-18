from __future__ import annotations

from datetime import datetime, timezone
from itertools import count
import os
from unittest import main

import psycopg

from control_plane_kit.application.deploy import (
    ApprovalGrant,
    ApprovalSuspension,
    DeploymentPlanRequest,
    DeploymentProgram,
    DeploymentProgramServices,
    PlanningServices,
)
from control_plane_kit.planning import compile_activity_plan
from control_plane_kit.stores import (
    GraphVersionRecord,
    PostgresUnitOfWork,
    WorkspaceRecord,
)
from control_plane_kit.topology import DeploymentGraph, diff_graphs, validate_graph
from control_plane_kit.workflows import (
    ActivityPlanningCommandService,
    ActivityPlanningGraphInvalid,
    ApprovalCommandService,
    CurrentGraphAdvancementCommandService,
    DeploymentPlanContextQueryService,
    DesiredGraphCommandService,
    ExecutionAdmissionCommandService,
    ExecutionCoordinator,
    IdempotencyKey,
    OperationCommandService,
    RunLifecycleCommandService,
)
from examples.generated_hello_graphs import (
    HelloGraphShape,
    MissingDatabaseConnection,
    generated_hello_graph,
)
from examples.scenarios.runner import ScenarioEffectInterpreter
from tests.postgres_case import PostgresStoreTestCase


class Ids:
    def __init__(self, prefix: str) -> None:
        self._prefix = prefix
        self._values = count(1)

    def __call__(self) -> str:
        return f"{self._prefix}-{next(self._values)}"


class GeneratedHelloProgramTests(PostgresStoreTestCase):
    def test_generated_graphs_plan_and_reconstruct_through_deployment_program(self):
        for ordinal, shape in enumerate(
            (HelloGraphShape(1, 1), HelloGraphShape(2, 2)),
            start=1,
        ):
            with self.subTest(shape=shape):
                prefix = f"generated-program-{ordinal}"
                current = DeploymentGraph(f"{prefix}-empty")
                desired = generated_hello_graph(shape)
                current_graph_id = self._initialize(prefix, current)
                program, interpreter = self._program(f"{prefix}-plan")
                deployment = program.between(current, desired)

                prepared = deployment.plan(
                    DeploymentPlanRequest(
                        transition=deployment.transition,
                        workspace_id=prefix,
                        current_graph_id=current_graph_id,
                        expected_desired_graph_id=current_graph_id,
                        actor_id="generated-operator",
                        title=f"Generated graph {shape.branching_factor}x{shape.depth}",
                        approval_comment="Approve generated topology stress plan.",
                        idempotency_prefix=prefix,
                    )
                )

                self.assertIsInstance(prepared, ApprovalSuspension)
                assert isinstance(prepared, ApprovalSuspension)
                expected = compile_activity_plan(
                    diff_graphs(validate_graph(current), validate_graph(desired))
                )
                self.assertEqual(prepared.preparation.plan.plan_record.plan, expected)
                plan_id = prepared.preparation.plan.plan_record.plan_id
                request_id = prepared.approval_request.request.request_id

                approved = self._program(f"{prefix}-approve")[0].for_plan(plan_id).approve(
                    request_id,
                    ApprovalGrant(
                        actor_id="generated-approver",
                        actor_scopes=(
                            prepared.approval_request.request.required_scope,
                        ),
                        idempotency_key=IdempotencyKey(f"{prefix}:decision"),
                    ),
                )

                self.assertEqual(approved.approval.request.plan_id, plan_id)
                self.assertEqual(interpreter.requests, [])

    def test_invalid_generated_graph_records_no_plan_approval_or_effect(self):
        prefix = "generated-invalid"
        current = DeploymentGraph("generated-invalid-empty")
        desired = generated_hello_graph(
            HelloGraphShape(2, 1),
            MissingDatabaseConnection(),
        )
        current_graph_id = self._initialize(prefix, current)
        program, interpreter = self._program(prefix)
        deployment = program.between(current, desired)

        with self.assertRaises(ActivityPlanningGraphInvalid):
            deployment.plan(
                DeploymentPlanRequest(
                    transition=deployment.transition,
                    workspace_id=prefix,
                    current_graph_id=current_graph_id,
                    expected_desired_graph_id=current_graph_id,
                    actor_id="generated-operator",
                    title="Invalid generated graph",
                    approval_comment="This invalid graph cannot request approval.",
                    idempotency_prefix=prefix,
                )
            )

        session = self.stores.activity_history.sessions_for_workspace(prefix)[0]
        self.assertEqual(self.stores.activity_history.plans_for_session(session.session_id), ())
        self.assertEqual(
            self.stores.activity_history.approval_requests_for_session(session.session_id),
            (),
        )
        self.assertEqual(interpreter.requests, [])

    def _initialize(self, workspace_id: str, current: DeploymentGraph) -> str:
        graph_id = f"{workspace_id}-current"
        self.stores.workspace.create(WorkspaceRecord(workspace_id, workspace_id))
        self.stores.graph_topology.save(
            GraphVersionRecord.from_graph(
                graph_id=graph_id,
                workspace_id=workspace_id,
                version=1,
                graph=current,
                created_by="generated-fixture",
                created_at=_text_clock(),
            )
        )
        self.stores.workspace.set_current_graph(workspace_id, graph_id)
        self.stores.workspace.set_desired_graph(workspace_id, graph_id)
        return graph_id

    def _program(self, prefix: str):
        factory = lambda: PostgresUnitOfWork(
            lambda: psycopg.connect(os.environ["CPK_TEST_DATABASE_URL"])
        )
        approval = ApprovalCommandService(
            factory,
            clock=_text_clock,
            id_factory=Ids(f"{prefix}-approval"),
        )
        planning = PlanningServices(
            OperationCommandService(
                factory,
                clock=_text_clock,
                id_factory=Ids(f"{prefix}-operation"),
            ),
            DesiredGraphCommandService(
                factory,
                clock=_text_clock,
                id_factory=Ids(f"{prefix}-graph"),
            ),
            ActivityPlanningCommandService(
                factory,
                clock=_text_clock,
                id_factory=Ids(f"{prefix}-plan"),
            ),
            approval,
        )
        lifecycle = RunLifecycleCommandService(
            factory,
            clock=_text_clock,
            id_factory=Ids(f"{prefix}-run"),
        )
        interpreter = ScenarioEffectInterpreter()
        return (
            DeploymentProgram(
                DeploymentProgramServices(
                    planning=planning,
                    approvals=approval,
                    admission=ExecutionAdmissionCommandService(
                        factory,
                        clock=_text_clock,
                        id_factory=Ids(f"{prefix}-admission"),
                    ),
                    lifecycle=lifecycle,
                    coordinator=ExecutionCoordinator(
                        factory,
                        lifecycle,
                        interpreter,
                        clock=_datetime_clock,
                        id_factory=Ids(f"{prefix}-coordinator"),
                    ),
                    advancement=CurrentGraphAdvancementCommandService(
                        factory,
                        clock=_text_clock,
                        id_factory=Ids(f"{prefix}-advance"),
                    ),
                    contexts=DeploymentPlanContextQueryService(factory),
                )
            ),
            interpreter,
        )


def _text_clock() -> str:
    return "2026-07-18T12:00:00Z"


def _datetime_clock() -> datetime:
    return datetime(2026, 7, 18, 12, tzinfo=timezone.utc)


if __name__ == "__main__":
    main()
