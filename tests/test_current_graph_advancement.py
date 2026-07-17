from __future__ import annotations

import concurrent.futures
import os
from dataclasses import replace

import psycopg

from control_plane_kit import (
    ActivityEventKind,
    ActivityEventRecord,
    ActivityRunRecord,
    ActivityRunStatus,
    AdmittedRun,
    BoundedEvidence,
    DeploymentGraph,
    ObservationRecord,
    ObservationStatus,
    RetryIdentity,
)
from control_plane_kit.stores import (
    GraphVersionRecord,
    OperationActionKind,
    OperationActionRecord,
    PostgresUnitOfWork,
)
from control_plane_kit.workflows import (
    AdvanceCurrentGraph,
    CurrentGraphAdvancementCommandService,
    CurrentGraphAdvancementConflict,
    CurrentGraphAdvancementDenied,
    CurrentGraphAdvancementIncomplete,
    ExecutionWorkerAuthority,
    IdempotencyKey,
)
from tests.postgres_case import PostgresStoreTestCase
from tests.test_execution_store import ExecutionStoreTests


class Sequence:
    def __init__(self, *values: str) -> None:
        self.values = list(values)

    def __call__(self) -> str:
        return self.values.pop(0)


class CurrentGraphAdvancementTests(PostgresStoreTestCase):
    def setUp(self) -> None:
        super().setUp()
        ExecutionStoreTests._seed_admission_truth(self.stores)
        self.stores.graph_topology.save(
            GraphVersionRecord.from_graph(
                graph_id="graph-a",
                workspace_id="workspace-a",
                version=1,
                graph=DeploymentGraph("current"),
                created_by="operator",
                created_at="2026-07-16T00:00:00Z",
            )
        )
        self.stores.graph_topology.save(
            GraphVersionRecord.from_graph(
                graph_id="graph-b",
                workspace_id="workspace-a",
                version=2,
                graph=DeploymentGraph("desired"),
                created_by="operator",
                created_at="2026-07-16T00:01:00Z",
            )
        )
        self.stores.workspace.set_current_graph("workspace-a", "graph-a")
        self.stores.workspace.set_desired_graph("workspace-a", "graph-b")
        self.stores.execution.add_request(ExecutionStoreTests._request())

    def _unit_of_work(self) -> PostgresUnitOfWork:
        database_url = os.environ["CPK_TEST_DATABASE_URL"]
        return PostgresUnitOfWork(lambda: psycopg.connect(database_url))

    def _service(self, *ids: str) -> CurrentGraphAdvancementCommandService:
        return CurrentGraphAdvancementCommandService(
            self._unit_of_work,
            clock=lambda: "2026-07-16T00:06:00Z",
            id_factory=Sequence(*ids),
        )

    def _authority(
        self,
        worker_id: str = "worker-a",
        scopes: tuple[str, ...] = ("execution:operate",),
    ) -> ExecutionWorkerAuthority:
        return ExecutionWorkerAuthority(worker_id, scopes)

    def _command(self, *, key: str = "advance-a", **changes) -> AdvanceCurrentGraph:
        values = {
            "workspace_id": "workspace-a",
            "run_id": "run-a",
            "plan_id": "plan-a",
            "expected_current_graph_id": "graph-a",
            "desired_graph_id": "graph-b",
            "authority": self._authority(),
            "idempotency_key": IdempotencyKey(key),
        }
        values.update(changes)
        return AdvanceCurrentGraph(**values)

    def _seed_succeeded_run(
        self,
        *,
        step_kind: ActivityEventKind = ActivityEventKind.STEP_SUCCEEDED,
        step_activity_id: str = "start-runtime-a",
    ) -> None:
        self.stores.execution.add_run(
            ActivityRunRecord(
                run_id="run-a",
                plan_id="plan-a",
                admission=AdmittedRun("execution-request-a"),
                retry=RetryIdentity(1),
                status=ActivityRunStatus.SUCCEEDED,
                created_at="2026-07-16T00:04:00Z",
                started_at="2026-07-16T00:04:10Z",
                settled_at="2026-07-16T00:05:00Z",
            )
        )
        values = (
            (ActivityEventKind.RUN_OPENED, None),
            (ActivityEventKind.RUN_STARTED, None),
            (ActivityEventKind.STEP_STARTED, "start-runtime-a"),
            (step_kind, step_activity_id),
            (ActivityEventKind.RUN_SUCCEEDED, None),
        )
        for ordinal, (kind, activity_id) in enumerate(values, start=1):
            self.stores.execution.add_event(
                ActivityEventRecord(
                    event_id=f"event-{ordinal}",
                    run_id="run-a",
                    ordinal=ordinal,
                    kind=kind,
                    activity_id=activity_id,
                    occurred_at=f"2026-07-16T00:04:{ordinal:02d}Z",
                )
            )

    def test_complete_durable_success_advances_pointer_and_replays_evidence(self):
        self._seed_succeeded_run()
        service = self._service("event-advance", "action-advance")

        result = service.execute(self._command())
        replay = self._service("unused", "unused").execute(self._command())

        self.assertEqual(
            self.stores.workspace.get("workspace-a").current_graph_id,
            "graph-b",
        )
        self.assertIs(result.event.kind, ActivityEventKind.CURRENT_GRAPH_ADVANCED)
        self.assertIs(
            result.action.action_type,
            OperationActionKind.CURRENT_GRAPH_ADVANCED,
        )
        self.assertTrue(replay.replayed)
        self.assertEqual(replay.event, result.event)
        self.assertEqual(replay.action, result.action)
        self.assertEqual(len(self.stores.execution.events_for_run("run-a")), 6)

    def test_projection_and_observation_without_complete_steps_cannot_advance(self):
        self._seed_succeeded_run(
            step_kind=ActivityEventKind.STEP_UNCERTAIN,
        )
        self.stores.observed_state.put(
            ObservationRecord(
                "observation-a",
                "workspace-a",
                "runtime-a",
                ObservationStatus.HEALTHY,
                "2026-07-16T00:05:30Z",
                BoundedEvidence.from_mapping({"probe": "ready"}),
            )
        )

        with self.assertRaises(CurrentGraphAdvancementIncomplete):
            self._service("unused", "unused").execute(self._command())

        self.assertEqual(
            self.stores.workspace.get("workspace-a").current_graph_id,
            "graph-a",
        )
        self.assertEqual(self.stores.activity_history.actions_for_session("session-a"), ())

    def test_unsupported_step_cannot_advance_current_graph(self):
        self._seed_succeeded_run(
            step_kind=ActivityEventKind.STEP_UNSUPPORTED,
        )

        with self.assertRaises(CurrentGraphAdvancementIncomplete):
            self._service("unused", "unused").execute(self._command())

        self.assertEqual(
            self.stores.workspace.get("workspace-a").current_graph_id,
            "graph-a",
        )

    def test_missing_step_success_evidence_fails_closed(self):
        self._seed_succeeded_run(step_kind=ActivityEventKind.STEP_STARTED)

        with self.assertRaises(CurrentGraphAdvancementIncomplete):
            self._service("unused", "unused").execute(self._command())

    def test_foreign_step_success_evidence_fails_closed(self):
        self._seed_succeeded_run(step_activity_id="foreign-activity")

        with self.assertRaises(CurrentGraphAdvancementIncomplete):
            self._service("unused", "unused").execute(self._command())

    def test_duplicate_terminal_success_evidence_fails_closed(self):
        self._seed_succeeded_run()
        self.stores.execution.add_event(
            ActivityEventRecord(
                event_id="event-duplicate-success",
                run_id="run-a",
                ordinal=6,
                kind=ActivityEventKind.RUN_SUCCEEDED,
                occurred_at="2026-07-16T00:05:01Z",
            )
        )

        with self.assertRaises(CurrentGraphAdvancementIncomplete):
            self._service("unused", "unused").execute(self._command())

    def test_authority_worker_and_stale_pointer_fail_closed(self):
        self._seed_succeeded_run()
        with self.assertRaises(CurrentGraphAdvancementDenied):
            self._service("unused", "unused").execute(
                self._command(authority=self._authority(scopes=()))
            )
        with self.assertRaises(CurrentGraphAdvancementDenied):
            self._service("unused", "unused").execute(
                self._command(authority=self._authority("worker-b"))
            )
        with self.assertRaises(CurrentGraphAdvancementConflict):
            self._service("unused", "unused").execute(
                self._command(expected_current_graph_id="graph-stale")
            )

    def test_late_evidence_failure_rolls_back_pointer_and_event(self):
        self._seed_succeeded_run()
        self.stores.activity_history.add_action(
            OperationActionRecord(
                action_id="action-duplicate",
                session_id="session-a",
                ordinal=1,
                action_type=OperationActionKind.ADD_BLOCK,
                actor_id="operator",
                created_at="2026-07-16T00:05:30Z",
                idempotency_key="seed-action",
                intent_fingerprint="seed-fingerprint",
            )
        )

        with self.assertRaises(psycopg.errors.UniqueViolation):
            self._service("event-advance", "action-duplicate").execute(self._command())

        self.assertEqual(
            self.stores.workspace.get("workspace-a").current_graph_id,
            "graph-a",
        )
        self.assertEqual(len(self.stores.execution.events_for_run("run-a")), 5)

    def test_concurrent_advancement_has_one_winner(self):
        self._seed_succeeded_run()

        def advance(label: str):
            try:
                return self._service(
                    f"event-{label}",
                    f"action-{label}",
                ).execute(self._command(key=f"advance-{label}"))
            except CurrentGraphAdvancementConflict:
                return None

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            results = tuple(executor.map(advance, ("one", "two")))

        self.assertEqual(len(tuple(value for value in results if value is not None)), 1)
        self.assertEqual(
            self.stores.workspace.get("workspace-a").current_graph_id,
            "graph-b",
        )
        self.assertEqual(
            len(
                tuple(
                    event
                    for event in self.stores.execution.events_for_run("run-a")
                    if event.kind is ActivityEventKind.CURRENT_GRAPH_ADVANCED
                )
            ),
            1,
        )


if __name__ == "__main__":
    import unittest

    unittest.main()
