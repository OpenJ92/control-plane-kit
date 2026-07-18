from __future__ import annotations

import os
from unittest import main

import psycopg

from control_plane_kit import ReconcileNode
from control_plane_kit.stores import (
    GraphVersionRecord,
    OperationActionKind,
    PostgresUnitOfWork,
    WorkspaceRecord,
)
from control_plane_kit.workflows import (
    ActivityPlanningCommandService,
    ApprovalCommandService,
    DesiredGraphCommandService,
    OperationCommandService,
)
from examples.backend_swap_planning import (
    PlanningWorkflowServices,
    plan_backend_swap,
)
from examples.router_runtime import router_graph
from tests.postgres_case import PostgresStoreTestCase


class Sequence:
    def __init__(self, *values: str) -> None:
        self._values = list(values)

    def __call__(self) -> str:
        return self._values.pop(0)


class BackendSwapPlanningExampleTests(PostgresStoreTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.stores.workspace.create(
            WorkspaceRecord("workspace-a", "Backend swap demo")
        )
        self.stores.graph_topology.save(
            GraphVersionRecord.from_graph(
                graph_id="graph-current",
                workspace_id="workspace-a",
                version=1,
                graph=router_graph("api-v1"),
                created_by="operator",
                created_at="2026-07-16T00:00:00Z",
            )
        )
        self.stores.workspace.set_current_graph("workspace-a", "graph-current")
        self.stores.workspace.set_desired_graph("workspace-a", "graph-current")

    def test_complete_workflow_persists_reviewable_plan_without_runtime_effects(self):
        result = plan_backend_swap(
            self._services(
                operation_ids=("session-a", "action-session"),
                graph_ids=("graph-desired", "action-desired"),
                plan_ids=("plan-a", "action-plan"),
                approval_ids=("approval-a", "action-approval"),
            ),
            workspace_id="workspace-a",
            actor_id="operator",
            current_graph_id="graph-current",
            expected_desired_graph_id="graph-current",
            desired_graph=router_graph("api-v2"),
            idempotency_prefix="backend-swap",
        )

        descriptor = result.descriptor()
        actions = self.stores.activity_history.actions_for_session("session-a")
        persisted_plan = self.stores.activity_history.get_plan("plan-a")
        pending = self.stores.activity_history.get_approval_request("approval-a")

        self.assertEqual(
            [action.action_type for action in actions],
            [
                OperationActionKind.SESSION_STARTED,
                OperationActionKind.SET_DESIRED_GRAPH,
                OperationActionKind.PLAN_REQUESTED,
                OperationActionKind.APPROVAL_REQUESTED,
            ],
        )
        self.assertTrue(
            any(
                isinstance(activity.operation, ReconcileNode)
                for activity in persisted_plan.plan.activities
            )
        )
        self.assertEqual(pending.plan_id, "plan-a")
        self.assertEqual(descriptor["approval"]["state"], "pending")
        self.assertFalse(descriptor["runtime_effects_executed"])
        self.assertEqual(self.stores.execution.runs_for_plan("plan-a"), ())

    def test_replaying_the_complete_workflow_reuses_every_durable_fact(self):
        command = {
            "workspace_id": "workspace-a",
            "actor_id": "operator",
            "current_graph_id": "graph-current",
            "expected_desired_graph_id": "graph-current",
            "desired_graph": router_graph("api-v2"),
            "idempotency_prefix": "backend-swap",
        }
        first = plan_backend_swap(
            self._services(
                operation_ids=("session-a", "action-session"),
                graph_ids=("graph-desired", "action-desired"),
                plan_ids=("plan-a", "action-plan"),
                approval_ids=("approval-a", "action-approval"),
            ),
            **command,
        )
        replay = plan_backend_swap(
            self._services(
                operation_ids=("unused-session", "unused-session-action"),
                graph_ids=("unused-graph", "unused-graph-action"),
                plan_ids=("unused-plan", "unused-plan-action"),
                approval_ids=("unused-approval", "unused-approval-action"),
            ),
            **command,
        )

        self.assertFalse(first.session.replayed)
        self.assertTrue(replay.session.replayed)
        self.assertTrue(replay.desired_graph.replayed)
        self.assertTrue(replay.plan.replayed)
        self.assertTrue(replay.approval.replayed)
        self.assertEqual(
            replay.descriptor()["plan"]["plan_id"],
            first.descriptor()["plan"]["plan_id"],
        )
        self.assertEqual(
            len(self.stores.activity_history.actions_for_session("session-a")),
            4,
        )

    def _services(
        self,
        *,
        operation_ids: tuple[str, ...],
        graph_ids: tuple[str, ...],
        plan_ids: tuple[str, ...],
        approval_ids: tuple[str, ...],
    ) -> PlanningWorkflowServices:
        unit_of_work_factory = lambda: PostgresUnitOfWork(
            lambda: psycopg.connect(os.environ["CPK_TEST_DATABASE_URL"])
        )
        return PlanningWorkflowServices(
            operations=OperationCommandService(
                unit_of_work_factory,
                clock=lambda: "2026-07-16T00:01:00Z",
                id_factory=Sequence(*operation_ids),
            ),
            desired_graphs=DesiredGraphCommandService(
                unit_of_work_factory,
                clock=lambda: "2026-07-16T00:02:00Z",
                id_factory=Sequence(*graph_ids),
            ),
            plans=ActivityPlanningCommandService(
                unit_of_work_factory,
                clock=lambda: "2026-07-16T00:03:00Z",
                id_factory=Sequence(*plan_ids),
            ),
            approvals=ApprovalCommandService(
                unit_of_work_factory,
                clock=lambda: "2026-07-16T00:04:00Z",
                id_factory=Sequence(*approval_ids),
            ),
        )


if __name__ == "__main__":
    main()
