from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from itertools import count
import json
import os
from threading import Event, Thread

import psycopg

from control_plane_kit.effects import (
    EffectCapability,
    EffectFailed,
    EffectMaterializationError,
    EffectObservation,
    MaterializedEffectRequest,
    EffectSucceeded,
    EffectUnsupported,
    ObservationKind,
)
from control_plane_kit.execution import (
    ActivityEventKind,
    ActivityEventRecord,
    ActivityRunRecord,
    ActivityRunStatus,
    AdmittedRun,
    BoundedEvidence,
    FailureCategory,
    FailureEvidence,
    ObservationStatus,
    RetryIdentity,
)
from control_plane_kit.stores import GraphVersionRecord, PostgresUnitOfWork, WorkspaceRecord
from control_plane_kit.planning import DEFAULT_ACTIVITY_PLAN_CODEC, compile_activity_plan
from control_plane_kit.topology import DeploymentGraph, diff_graphs, validate_graph
from examples.scenarios import planning_scenarios
from control_plane_kit.workflows import (
    CoordinatorCheckpoint,
    CoordinatorStatus,
    ExecuteActivityRun,
    ExecutionCoordinator,
    ExecutionCoordinatorConflict,
    ExecutionCoordinatorDenied,
    ExecutionWorkerAuthority,
    InjectedCoordinatorCrash,
    RunLifecycleCommandService,
)
from tests.postgres_case import PostgresStoreTestCase
from tests.test_execution_store import ExecutionStoreTests


class MutableClock:
    def __init__(self) -> None:
        self.value = datetime(2026, 7, 16, 0, 5, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.value

    def advance(self, seconds: int) -> None:
        self.value += timedelta(seconds=seconds)

    def text(self) -> str:
        return self.value.isoformat().replace("+00:00", "Z")


class Ids:
    def __init__(self) -> None:
        self._values = count(1)

    def __call__(self) -> str:
        return f"generated-{next(self._values)}"


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
    def __init__(self, database_url: str) -> None:
        self.database_url = database_url
        self.active = 0

    def __call__(self):
        return TrackingUnitOfWork(
            PostgresUnitOfWork(lambda: psycopg.connect(self.database_url)),
            self,
        )


@dataclass
class InspectableFakeInterpreter:
    tracker: TransactionTracker
    fail: bool = False
    observations: bool = False
    remote_unsupported: bool = False
    uncertain: bool = False
    capabilities: frozenset[EffectCapability] = frozenset(EffectCapability)
    requests: list[MaterializedEffectRequest] = field(default_factory=list)

    def execute(self, request: MaterializedEffectRequest) -> EffectSucceeded | EffectFailed | EffectUnsupported:
        if self.tracker.active != 0:
            raise AssertionError("effect executed while a UnitOfWork was active")
        self.requests.append(request)
        observed = ()
        if self.observations:
            observed = (
                EffectObservation(
                    "runtime-a",
                    ObservationKind.HEALTH,
                    ObservationStatus.HEALTHY,
                    BoundedEvidence.from_mapping({"probe": "fake-ready"}),
                ),
            )
        if self.remote_unsupported:
            return EffectUnsupported(request.identity, request.capability)
        if self.uncertain:
            return EffectFailed(
                request.identity,
                FailureEvidence(
                    FailureCategory.UNCERTAIN,
                    "fake.uncertain",
                    "The fake effect may have completed without a result.",
                ),
            )
        if self.fail:
            return EffectFailed(
                request.identity,
                FailureEvidence(
                    FailureCategory.RETRYABLE,
                    "fake.effect-failed",
                    "The fake effect failed after an attempt.",
                ),
                observed,
            )
        return EffectSucceeded(
            request.identity,
            BoundedEvidence.from_mapping({"fake": "completed"}),
            observed,
        )


@dataclass
class BlockingFakeInterpreter(InspectableFakeInterpreter):
    entered: Event = field(default_factory=Event)
    release: Event = field(default_factory=Event)

    def execute(self, request: MaterializedEffectRequest) -> EffectSucceeded | EffectFailed:
        if self.tracker.active != 0:
            raise AssertionError("blocked effect entered while a UnitOfWork was active")
        self.entered.set()
        if not self.release.wait(timeout=5):
            raise AssertionError("test did not release the blocked fake effect")
        return super().execute(request)


class ExecutionCoordinatorTests(PostgresStoreTestCase):
    def setUp(self) -> None:
        super().setUp()
        ExecutionStoreTests._seed_admission_truth(self.stores, include_graphs=True)
        self.stores.execution.add_request(ExecutionStoreTests._request())
        self.stores.execution.add_run(
            ActivityRunRecord(
                run_id="run-a",
                plan_id="plan-a",
                admission=AdmittedRun("execution-request-a"),
                retry=RetryIdentity(1),
                status=ActivityRunStatus.RUNNING,
                created_at="2026-07-16T00:04:00Z",
                started_at="2026-07-16T00:04:30Z",
            )
        )
        for ordinal, kind in enumerate(
            (ActivityEventKind.RUN_OPENED, ActivityEventKind.RUN_STARTED),
            start=1,
        ):
            self.stores.execution.add_event(
                ActivityEventRecord(
                    event_id=f"seed-event-{ordinal}",
                    run_id="run-a",
                    ordinal=ordinal,
                    kind=kind,
                    occurred_at=f"2026-07-16T00:04:{ordinal:02d}Z",
                )
            )
        self.clock = MutableClock()
        self.ids = Ids()
        self.tracker = TransactionTracker(os.environ["CPK_TEST_DATABASE_URL"])

    def _authority(
        self,
        *,
        worker_id: str = "worker-a",
        scopes: tuple[str, ...] = ("execution:operate",),
    ) -> ExecutionWorkerAuthority:
        return ExecutionWorkerAuthority(worker_id, scopes)

    def _command(self, **changes) -> ExecuteActivityRun:
        values = {"run_id": "run-a", "authority": self._authority()}
        values.update(changes)
        return ExecuteActivityRun(**values)

    def _coordinator(
        self,
        interpreter: InspectableFakeInterpreter,
        *,
        crash: CoordinatorCheckpoint | None = None,
    ) -> ExecutionCoordinator:
        lifecycle = RunLifecycleCommandService(
            self.tracker,
            clock=self.clock.text,
            id_factory=self.ids,
        )
        return ExecutionCoordinator(
            self.tracker,
            lifecycle,
            interpreter,
            clock=self.clock,
            id_factory=self.ids,
            injected_crash=crash,
        )

    def _events(self) -> tuple[ActivityEventRecord, ...]:
        return self.stores.execution.events_for_run("run-a")

    def test_success_uses_two_short_transactions_and_settles_run(self) -> None:
        interpreter = InspectableFakeInterpreter(self.tracker, observations=True)

        result = self._coordinator(interpreter).execute(self._command())

        self.assertIs(result.status, CoordinatorStatus.COMPLETED)
        self.assertIs(result.run.status, ActivityRunStatus.SUCCEEDED)
        self.assertEqual(result.effects_attempted, 1)
        self.assertEqual(len(interpreter.requests), 1)
        self.assertEqual(
            tuple(event.kind for event in self._events()),
            (
                ActivityEventKind.RUN_OPENED,
                ActivityEventKind.RUN_STARTED,
                ActivityEventKind.STEP_STARTED,
                ActivityEventKind.STEP_SUCCEEDED,
                ActivityEventKind.RUN_SUCCEEDED,
            ),
        )
        self.assertEqual(
            self.stores.observed_state.latest("workspace-a", "runtime-a").status,
            ObservationStatus.HEALTHY,
        )
        self.assertIsNone(self.stores.workspace.get("workspace-a").current_graph_id)

    def test_completed_replay_does_not_repeat_effect(self) -> None:
        interpreter = InspectableFakeInterpreter(self.tracker)
        coordinator = self._coordinator(interpreter)
        coordinator.execute(self._command())

        replay = coordinator.execute(self._command())

        self.assertIs(replay.status, CoordinatorStatus.COMPLETED)
        self.assertEqual(len(interpreter.requests), 1)

    def test_attempted_failure_is_journaled_and_fails_run(self) -> None:
        interpreter = InspectableFakeInterpreter(self.tracker, fail=True)

        result = self._coordinator(interpreter).execute(self._command())

        self.assertIs(result.status, CoordinatorStatus.FAILED)
        self.assertIs(result.run.status, ActivityRunStatus.FAILED)
        self.assertEqual(len(interpreter.requests), 1)
        self.assertEqual(
            tuple(event.kind for event in self._events())[-3:],
            (
                ActivityEventKind.STEP_STARTED,
                ActivityEventKind.STEP_FAILED,
                ActivityEventKind.RUN_FAILED,
            ),
        )

    def test_failed_effect_observations_commit_with_the_step_result(self) -> None:
        interpreter = InspectableFakeInterpreter(
            self.tracker,
            fail=True,
            observations=True,
        )

        result = self._coordinator(interpreter).execute(self._command())

        self.assertIs(result.status, CoordinatorStatus.FAILED)
        observation = self.stores.observed_state.latest(
            "workspace-a",
            "runtime-a",
        )
        self.assertIsNotNone(observation)
        self.assertIs(observation.status, ObservationStatus.HEALTHY)
        self.assertIs(self._events()[-2].kind, ActivityEventKind.STEP_FAILED)

    def test_unsupported_capability_is_distinct_and_never_attempted(self) -> None:
        interpreter = InspectableFakeInterpreter(
            self.tracker,
            capabilities=frozenset(),
        )

        result = self._coordinator(interpreter).execute(self._command())

        self.assertIs(result.status, CoordinatorStatus.UNSUPPORTED)
        self.assertEqual(interpreter.requests, [])
        self.assertEqual(
            tuple(event.kind for event in self._events())[-2:],
            (ActivityEventKind.STEP_UNSUPPORTED, ActivityEventKind.RUN_FAILED),
        )

    def test_live_capability_rejection_closes_intent_without_mutation(self) -> None:
        interpreter = InspectableFakeInterpreter(
            self.tracker,
            remote_unsupported=True,
        )

        result = self._coordinator(interpreter).execute(self._command())

        self.assertIs(result.status, CoordinatorStatus.UNSUPPORTED)
        self.assertEqual(len(interpreter.requests), 1)
        self.assertEqual(
            tuple(event.kind for event in self._events())[-3:],
            (
                ActivityEventKind.STEP_STARTED,
                ActivityEventKind.STEP_UNSUPPORTED,
                ActivityEventKind.RUN_FAILED,
            ),
        )

    def test_uncertain_adapter_result_preserves_open_run_for_operator_review(self) -> None:
        interpreter = InspectableFakeInterpreter(self.tracker, uncertain=True)

        result = self._coordinator(interpreter).execute(self._command())

        self.assertIs(result.status, CoordinatorStatus.UNCERTAIN)
        self.assertIs(result.run.status, ActivityRunStatus.RUNNING)
        self.assertEqual(len(interpreter.requests), 1)
        self.assertIs(self._events()[-1].kind, ActivityEventKind.STEP_UNCERTAIN)

    def test_crash_after_intent_never_blindly_attempts_effect(self) -> None:
        interpreter = InspectableFakeInterpreter(self.tracker)
        with self.assertRaises(InjectedCoordinatorCrash):
            self._coordinator(
                interpreter,
                crash=CoordinatorCheckpoint.AFTER_INTENT_COMMIT,
            ).execute(self._command())

        in_flight = self._coordinator(interpreter).execute(self._command())
        self.clock.advance(31)
        uncertain = self._coordinator(interpreter).execute(self._command())

        self.assertIs(in_flight.status, CoordinatorStatus.IN_FLIGHT)
        self.assertIs(uncertain.status, CoordinatorStatus.UNCERTAIN)
        self.assertEqual(interpreter.requests, [])
        self.assertIs(self._events()[-1].kind, ActivityEventKind.STEP_UNCERTAIN)

    def test_crash_after_effect_records_uncertainty_without_replay(self) -> None:
        interpreter = InspectableFakeInterpreter(self.tracker)
        with self.assertRaises(InjectedCoordinatorCrash):
            self._coordinator(
                interpreter,
                crash=CoordinatorCheckpoint.AFTER_EFFECT,
            ).execute(self._command())

        self.clock.advance(31)
        uncertain = self._coordinator(interpreter).execute(self._command())

        self.assertIs(uncertain.status, CoordinatorStatus.UNCERTAIN)
        self.assertEqual(len(interpreter.requests), 1)
        self.assertIs(self._events()[-1].kind, ActivityEventKind.STEP_UNCERTAIN)

    def test_result_transaction_crash_rolls_back_result_and_observation(self) -> None:
        interpreter = InspectableFakeInterpreter(self.tracker, observations=True)
        with self.assertRaises(InjectedCoordinatorCrash):
            self._coordinator(
                interpreter,
                crash=CoordinatorCheckpoint.BEFORE_RESULT_COMMIT,
            ).execute(self._command())

        self.assertIs(self._events()[-1].kind, ActivityEventKind.STEP_STARTED)
        self.assertIsNone(
            self.stores.observed_state.latest("workspace-a", "runtime-a")
        )
        self.clock.advance(31)
        uncertain = self._coordinator(interpreter).execute(self._command())
        self.assertIs(uncertain.status, CoordinatorStatus.UNCERTAIN)
        self.assertEqual(len(interpreter.requests), 1)

    def test_failed_result_commit_resumes_run_settlement_without_replay(self) -> None:
        interpreter = InspectableFakeInterpreter(self.tracker, fail=True)
        with self.assertRaises(InjectedCoordinatorCrash):
            self._coordinator(
                interpreter,
                crash=CoordinatorCheckpoint.AFTER_RESULT_COMMIT,
            ).execute(self._command())

        self.assertIs(
            self.stores.execution.get_run("run-a").status,
            ActivityRunStatus.RUNNING,
        )
        self.assertIs(self._events()[-1].kind, ActivityEventKind.STEP_FAILED)

        resumed = self._coordinator(interpreter).execute(self._command())

        self.assertIs(resumed.status, CoordinatorStatus.FAILED)
        self.assertIs(resumed.run.status, ActivityRunStatus.FAILED)
        self.assertEqual(len(interpreter.requests), 1)

    def test_success_result_commit_resumes_run_settlement_without_replay(self) -> None:
        interpreter = InspectableFakeInterpreter(self.tracker)
        with self.assertRaises(InjectedCoordinatorCrash):
            self._coordinator(
                interpreter,
                crash=CoordinatorCheckpoint.AFTER_RESULT_COMMIT,
            ).execute(self._command())

        self.assertIs(
            self.stores.execution.get_run("run-a").status,
            ActivityRunStatus.RUNNING,
        )
        self.assertIs(self._events()[-1].kind, ActivityEventKind.STEP_SUCCEEDED)

        resumed = self._coordinator(interpreter).execute(self._command())

        self.assertIs(resumed.status, CoordinatorStatus.COMPLETED)
        self.assertIs(resumed.run.status, ActivityRunStatus.SUCCEEDED)
        self.assertEqual(len(interpreter.requests), 1)

    def test_concurrent_observer_reports_in_flight_without_duplicate_effect(self) -> None:
        interpreter = BlockingFakeInterpreter(self.tracker)
        coordinator = self._coordinator(interpreter)
        first_result: list[object] = []

        def execute_first() -> None:
            try:
                first_result.append(coordinator.execute(self._command()))
            except BaseException as error:
                first_result.append(error)

        worker = Thread(target=execute_first)
        worker.start()
        self.assertTrue(interpreter.entered.wait(timeout=5))

        observer = self._coordinator(interpreter).execute(self._command())

        self.assertIs(observer.status, CoordinatorStatus.IN_FLIGHT)
        self.assertEqual(interpreter.requests, [])
        self.assertNotIn(
            ActivityEventKind.STEP_UNCERTAIN,
            tuple(event.kind for event in self._events()),
        )

        interpreter.release.set()
        worker.join(timeout=5)
        self.assertFalse(worker.is_alive())
        self.assertEqual(len(first_result), 1)
        self.assertNotIsInstance(first_result[0], BaseException)
        completed = first_result[0]
        self.assertIs(completed.status, CoordinatorStatus.COMPLETED)
        self.assertEqual(len(interpreter.requests), 1)
        self.assertEqual(
            tuple(event.kind for event in self._events())[-3:],
            (
                ActivityEventKind.STEP_STARTED,
                ActivityEventKind.STEP_SUCCEEDED,
                ActivityEventKind.RUN_SUCCEEDED,
            ),
        )

    def test_representative_scenario_executes_in_canonical_plan_order(self) -> None:
        plan, scenario = self._representative_plan()
        self._replace_plan_truth(plan, scenario.current_graph, scenario.desired_graph)
        interpreter = InspectableFakeInterpreter(self.tracker)

        result = self._coordinator(interpreter).execute(self._command())

        self.assertIs(result.status, CoordinatorStatus.COMPLETED)
        self.assertEqual(
            tuple(request.identity.activity_id for request in interpreter.requests),
            tuple(activity.activity_id for activity in plan.activities),
        )

    def test_effect_limit_returns_typed_progress_without_settling_run(self) -> None:
        plan, scenario = self._representative_plan()
        self._replace_plan_truth(plan, scenario.current_graph, scenario.desired_graph)
        interpreter = InspectableFakeInterpreter(self.tracker)

        result = self._coordinator(interpreter).execute(
            self._command(max_effects=1)
        )

        self.assertIs(result.status, CoordinatorStatus.PROGRESSED)
        self.assertIs(result.run.status, ActivityRunStatus.RUNNING)
        self.assertEqual(result.effects_attempted, 1)
        self.assertIsNone(result.activity_id)
        self.assertEqual(len(interpreter.requests), 1)

    def _representative_plan(self):
        for scenario in planning_scenarios():
            candidate = compile_activity_plan(
                diff_graphs(
                    validate_graph(scenario.current_graph),
                    validate_graph(scenario.desired_graph),
                )
            )
            if candidate.ready_for_execution and len(candidate.activities) >= 3:
                return candidate, scenario
        self.fail("planning scenarios must include a multi-step executable plan")

    def _replace_plan_truth(self, plan, base_graph, desired_graph) -> None:
        self.connection.execute(
            "UPDATE cpk_activity_plans SET payload = %s::jsonb WHERE plan_id = 'plan-a'",
            (json.dumps(DEFAULT_ACTIVITY_PLAN_CODEC.encode(plan)),),
        )
        for graph_id, graph in (("graph-a", base_graph), ("graph-b", desired_graph)):
            self.connection.execute(
                "UPDATE cpk_graph_versions SET graph_descriptor = %s::jsonb WHERE graph_id = %s",
                (json.dumps(graph.descriptor()), graph_id),
            )

    def test_worker_identity_and_scope_fail_before_effect(self) -> None:
        interpreter = InspectableFakeInterpreter(self.tracker)
        with self.assertRaises(ExecutionCoordinatorDenied):
            self._coordinator(interpreter).execute(
                self._command(authority=self._authority(scopes=()))
            )
        with self.assertRaises(ExecutionCoordinatorDenied):
            self._coordinator(interpreter).execute(
                self._command(authority=self._authority(worker_id="worker-b"))
            )
        self.assertEqual(interpreter.requests, [])

    def test_materialization_failure_records_no_intent_and_attempts_no_effect(self) -> None:
        self.connection.execute(
            "UPDATE cpk_graph_versions SET graph_descriptor = %s::jsonb WHERE graph_id = 'graph-b'",
            (json.dumps(DeploymentGraph("missing-runtime").descriptor()),),
        )
        interpreter = InspectableFakeInterpreter(self.tracker)

        with self.assertRaisesRegex(EffectMaterializationError, "no runtime 'runtime-a'"):
            self._coordinator(interpreter).execute(self._command())

        self.assertEqual(interpreter.requests, [])
        self.assertNotIn(
            ActivityEventKind.STEP_STARTED,
            tuple(event.kind for event in self._events()),
        )

    def test_cross_workspace_graph_substitution_fails_before_intent(self) -> None:
        self.stores.workspace.create(WorkspaceRecord("workspace-b", "Foreign"))
        self.connection.execute(
            "UPDATE cpk_graph_versions SET workspace_id = 'workspace-b', version = 1 WHERE graph_id = 'graph-b'"
        )
        interpreter = InspectableFakeInterpreter(self.tracker)

        with self.assertRaisesRegex(ExecutionCoordinatorConflict, "different workspace"):
            self._coordinator(interpreter).execute(self._command())

        self.assertEqual(interpreter.requests, [])
        self.assertNotIn(
            ActivityEventKind.STEP_STARTED,
            tuple(event.kind for event in self._events()),
        )

    def test_later_workspace_pointer_does_not_retarget_pinned_graph_material(self) -> None:
        self.stores.graph_topology.save(
            GraphVersionRecord.from_graph(
                graph_id="graph-c",
                workspace_id="workspace-a",
                version=3,
                graph=DeploymentGraph("later"),
                created_by="other-operator",
                created_at="2026-07-16T00:04:45Z",
            )
        )
        self.stores.workspace.set_desired_graph("workspace-a", "graph-c")
        interpreter = InspectableFakeInterpreter(self.tracker)

        self._coordinator(interpreter).execute(self._command())

        self.assertEqual(len(interpreter.requests), 1)
        self.assertEqual(interpreter.requests[0].graphs.desired_graph_id, "graph-b")
        self.assertEqual(interpreter.requests[0].material_graph_id, "graph-b")


if __name__ == "__main__":
    import unittest

    unittest.main()
