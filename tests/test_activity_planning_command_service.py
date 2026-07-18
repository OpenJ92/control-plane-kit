"""Postgres integration tests for the authoritative planning command."""

from __future__ import annotations

import concurrent.futures
import os
import threading
import unittest

import psycopg

from control_plane_kit.planning.activity_plan import ReconcileNode
from control_plane_kit.topology.graph import DeploymentGraph
from control_plane_kit.stores import (
    GraphVersionRecord,
    OperationActionKind,
    PostgresUnitOfWork,
    WorkspaceRecord,
)
from control_plane_kit.workflows import (
    ActivityPlanningCommandService,
    ActivityPlanningGraphInvalid,
    ActivityPlanningGraphStateConflict,
    ActivityPlanningIdempotencyConflict,
    ActivityPlanningSessionConflict,
    CloseOperationSession,
    IdempotencyKey,
    OperationCommandService,
    RequestActivityPlan,
    StartOperationSession,
)
from examples.router_runtime import router_graph
from tests.postgres_case import PostgresStoreTestCase


class Sequence:
    def __init__(self, *values: str) -> None:
        self._values = list(values)

    def __call__(self) -> str:
        return self._values.pop(0)


class ActivityPlanningCommandServiceTests(PostgresStoreTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.stores.workspace.create(WorkspaceRecord("workspace-a", "Workspace A"))
        self._save_graph("graph-current", 1, router_graph("api-v1"))
        self._save_graph("graph-desired", 2, router_graph("api-v2"))
        self.stores.workspace.set_current_graph("workspace-a", "graph-current")
        self.stores.workspace.set_desired_graph("workspace-a", "graph-desired")
        self._operation_service("session-a", "start-action").execute(
            StartOperationSession(
                "workspace-a",
                "jacob",
                "Plan router switch",
                IdempotencyKey("start"),
            )
        )

    def _operation_service(self, *ids: str) -> OperationCommandService:
        database_url = os.environ["CPK_TEST_DATABASE_URL"]
        return OperationCommandService(
            lambda: PostgresUnitOfWork(lambda: psycopg.connect(database_url)),
            clock=lambda: "2026-07-16T00:00:00Z",
            id_factory=Sequence(*ids),
        )

    def _planning_service(self, *ids: str) -> ActivityPlanningCommandService:
        database_url = os.environ["CPK_TEST_DATABASE_URL"]
        return ActivityPlanningCommandService(
            lambda: PostgresUnitOfWork(lambda: psycopg.connect(database_url)),
            clock=lambda: "2026-07-16T00:01:00Z",
            id_factory=Sequence(*ids),
        )

    def _save_graph(
        self,
        graph_id: str,
        version: int,
        graph: DeploymentGraph,
    ) -> None:
        self.stores.graph_topology.save(
            GraphVersionRecord.from_graph(
                graph_id=graph_id,
                workspace_id="workspace-a",
                version=version,
                graph=graph,
                created_by="jacob",
                created_at="2026-07-16T00:00:00Z",
            )
        )

    def _command(
        self,
        *,
        actor_id: str = "jacob",
        key: str = "plan-router-switch",
    ) -> RequestActivityPlan:
        return RequestActivityPlan(
            session_id="session-a",
            workspace_id="workspace-a",
            actor_id=actor_id,
            expected_current_graph_id="graph-current",
            expected_desired_graph_id="graph-desired",
            idempotency_key=IdempotencyKey(key),
        )

    def test_plan_and_action_commit_as_one_operator_command(self):
        result = self._planning_service("plan-a", "plan-action").execute(
            self._command()
        )

        persisted = self.stores.activity_history.get_plan("plan-a")
        actions = self.stores.activity_history.actions_for_session("session-a")
        self.assertEqual(persisted.plan, result.plan_record.plan)
        self.assertTrue(
            any(
                isinstance(activity.operation, ReconcileNode)
                for activity in persisted.plan.activities
            )
        )
        self.assertEqual(actions[-1].action_type, OperationActionKind.PLAN_REQUESTED)
        self.assertEqual(actions[-1].ordinal, 2)
        self.assertEqual(actions[-1].payload["plan_id"], "plan-a")
        self.assertEqual(result.descriptor()["ready_for_execution"], True)

    def test_stale_workspace_pointers_reject_without_writes(self):
        self.stores.workspace.set_desired_graph("workspace-a", "graph-current")

        with self.assertRaises(ActivityPlanningGraphStateConflict):
            self._planning_service("plan-a", "plan-action").execute(self._command())

        self.assertEqual(
            self.stores.activity_history.plans_for_session("session-a"),
            (),
        )
        self.assertEqual(
            len(self.stores.activity_history.actions_for_session("session-a")),
            1,
        )

    def test_malformed_durable_graph_rejects_without_writes(self):
        self.stores.graph_topology.save(
            GraphVersionRecord(
                graph_id="graph-invalid",
                workspace_id="workspace-a",
                version=3,
                graph_descriptor={"name": "invalid", "nodes": "not-a-mapping"},
                created_by="jacob",
                created_at="2026-07-16T00:00:00Z",
            )
        )
        self.stores.workspace.set_desired_graph("workspace-a", "graph-invalid")
        command = RequestActivityPlan(
            session_id="session-a",
            workspace_id="workspace-a",
            actor_id="jacob",
            expected_current_graph_id="graph-current",
            expected_desired_graph_id="graph-invalid",
            idempotency_key=IdempotencyKey("invalid-plan"),
        )

        with self.assertRaises(ActivityPlanningGraphInvalid):
            self._planning_service("plan-a", "plan-action").execute(command)

        self.assertEqual(
            self.stores.activity_history.plans_for_session("session-a"),
            (),
        )

    def test_late_action_failure_rolls_back_plan_insert(self):
        with self.assertRaises(psycopg.errors.UniqueViolation):
            self._planning_service("plan-fail", "start-action").execute(
                self._command()
            )

        with self.assertRaises(KeyError):
            self.stores.activity_history.get_plan("plan-fail")
        self.assertEqual(
            len(self.stores.activity_history.actions_for_session("session-a")),
            1,
        )

    def test_identical_request_replays_original_plan_and_evidence(self):
        command = self._command()
        first = self._planning_service("plan-a", "plan-action").execute(command)
        replay = self._planning_service("unused-plan", "unused-action").execute(command)

        self.assertFalse(first.replayed)
        self.assertTrue(replay.replayed)
        self.assertEqual(replay.plan_record.plan_id, "plan-a")
        self.assertEqual(replay.action.action_id, "plan-action")
        self.assertEqual(
            len(self.stores.activity_history.plans_for_session("session-a")),
            1,
        )
        self.assertEqual(
            len(self.stores.activity_history.actions_for_session("session-a")),
            2,
        )

    def test_same_key_with_different_intent_fails_explicitly(self):
        self._planning_service("plan-a", "plan-action").execute(self._command())

        with self.assertRaises(ActivityPlanningIdempotencyConflict):
            self._planning_service("unused-plan", "unused-action").execute(
                self._command(actor_id="another-operator")
            )

    def test_replay_survives_later_pointer_and_session_state_changes(self):
        command = self._command()
        first = self._planning_service("plan-a", "plan-action").execute(command)
        self.stores.workspace.set_desired_graph("workspace-a", "graph-current")
        self._operation_service("close-action").execute(
            CloseOperationSession(
                "session-a",
                "jacob",
                IdempotencyKey("close"),
            )
        )

        replay = self._planning_service("unused-plan", "unused-action").execute(command)

        self.assertTrue(replay.replayed)
        self.assertEqual(replay.plan_record.plan_id, first.plan_record.plan_id)

    def test_concurrent_identical_requests_converge_on_one_plan(self):
        barrier = threading.Barrier(2)

        def submit(ids: tuple[str, str]):
            barrier.wait(timeout=5)
            return self._planning_service(*ids).execute(self._command())

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            results = tuple(
                executor.map(
                    submit,
                    (("plan-a", "action-a"), ("plan-b", "action-b")),
                )
            )

        plan_ids = {result.plan_record.plan_id for result in results}
        action_ids = {result.action.action_id for result in results}
        self.assertEqual(len(plan_ids), 1)
        self.assertEqual(len(action_ids), 1)
        self.assertEqual(sum(result.replayed for result in results), 1)
        self.assertEqual(
            len(self.stores.activity_history.plans_for_session("session-a")),
            1,
        )
        self.assertEqual(
            len(self.stores.activity_history.actions_for_session("session-a")),
            2,
        )

    def test_concurrent_close_and_plan_publish_in_a_serial_session_order(self):
        barrier = threading.Barrier(2)

        def plan():
            barrier.wait(timeout=5)
            try:
                return self._planning_service("plan-a", "plan-action").execute(
                    self._command()
                )
            except ActivityPlanningSessionConflict as error:
                return error

        def close():
            barrier.wait(timeout=5)
            return self._operation_service("close-action").execute(
                CloseOperationSession(
                    "session-a",
                    "jacob",
                    IdempotencyKey("close"),
                )
            )

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            plan_future = executor.submit(plan)
            close_future = executor.submit(close)
            plan_outcome = plan_future.result()
            close_future.result()

        kinds = tuple(
            action.action_type
            for action in self.stores.activity_history.actions_for_session("session-a")
        )
        if isinstance(plan_outcome, ActivityPlanningSessionConflict):
            self.assertEqual(
                kinds,
                (
                    OperationActionKind.SESSION_STARTED,
                    OperationActionKind.SESSION_CLOSED,
                ),
            )
        else:
            self.assertEqual(
                kinds,
                (
                    OperationActionKind.SESSION_STARTED,
                    OperationActionKind.PLAN_REQUESTED,
                    OperationActionKind.SESSION_CLOSED,
                ),
            )


if __name__ == "__main__":
    unittest.main()
