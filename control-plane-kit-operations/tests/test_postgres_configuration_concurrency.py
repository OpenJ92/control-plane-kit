"""Two real PostgreSQL overlaps over genuine nonempty accepted history."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timezone
import queue
import re
import threading
import unittest
from unittest import mock

import psycopg

from control_plane_kit_core.algebra import DeploymentTopology, DockerRuntime
from control_plane_kit_core.operations import ActivityEventKind, ActivityRunStatus, RecoveryScope, RunId
from control_plane_kit_core.planning import ActivityDependency, ActivityId, ActivityPlan, PlannedActivity, RuntimeTarget, StartRuntime
from control_plane_kit_core.runtime_effects import RuntimeEffectFailure, RuntimeEffectResult
from control_plane_kit_core.topology import compile_topology
from control_plane_kit_operations.advancement import CurrentGraphAdvancementCommandService, CurrentGraphAdvancementConflict
from control_plane_kit_operations.coordinator import CoordinatorStatus
from control_plane_kit_operations.execution_lease_recovery import RecoveryAuthority
from control_plane_kit_operations.failed_run_compensation import (
    BeginFailedRunCompensation, FailedRunCompensationCommandService, FailedRunCompensationReason,
)
from control_plane_kit_operations.failed_run_compensation_attempt import (
    FailedRunCompensationAttemptConflict, FailedRunCompensationAttemptStartService, StartFailedRunCompensationAttempt,
)
from control_plane_kit_operations.lifecycle import RunLifecycleCommandService
from control_plane_kit_operations.postgres import PostgresUnitOfWork
from control_plane_kit_operations.postgres.configuration_acceptance_store import ConfigurationAcceptanceStore
from control_plane_kit_operations.postgres.effect_attempt_store import EffectAttemptStore
from control_plane_kit_operations.postgres.graph_store import PostgresGraphTopologyStore, PostgresWorkspaceStore
from control_plane_kit_operations.records import ExecutionRequestStatus
from control_plane_kit_operations.workflows import IdempotencyKey
from tests import test_postgres_configuration_carry as carry_fixture
from tests import test_postgres_effect_attempt_start_concurrency as concurrency
from tests import test_execution_coordinator as coordinator_fixture
from tests.postgres_effect_attempt_coordinator_fixture import GeneratedIds


def live_clock():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class PostgresConfigurationConcurrencyTests(unittest.TestCase):
    _factory_with_pids = concurrency.PostgresEffectAttemptStartConcurrencyTests._factory_with_pids
    _wait_until_blocked_by = concurrency.PostgresEffectAttemptStartConcurrencyTests._wait_until_blocked_by

    def setUp(self):
        self.carry = carry_fixture.PostgresConfigurationCarryTests()
        self.carry.node_ids = ("api",)
        self.addCleanup(self.cleanup_fixture)
        self.carry.setUp()
        self.base, self.fixture = self.carry.base, self.carry.fixture
        self.connection, self.database_url = self.base.connection, self.base.database_url

    def cleanup_fixture(self):
        self.assertTrue(self.carry.doCleanups(), "concurrency fixture cleanup failed")

    def runtime_graph(self, names):
        graph = self.carry.graph
        for name in names:
            extra = compile_topology(DeploymentTopology("extra", DockerRuntime(runtime_id=name)))
            graph = graph.add_runtime(extra.runtimes[name])
        return graph

    @contextmanager
    def paused_prepare(self, run_id):
        entered, release = threading.Event(), threading.Event()
        original = ConfigurationAcceptanceStore._prepare

        def prepare(store, stores, workspace, request, run, *args):
            if run.run_id == run_id:
                entered.set()
                if not release.wait(timeout=15):
                    raise AssertionError("advancement prepare pause timed out")
            return original(store, stores, workspace, request, run, *args)

        try:
            with mock.patch.object(ConfigurationAcceptanceStore, "_prepare", prepare):
                yield entered, release
        finally:
            release.set()

    def wait_for_prepare(self, entered, future):
        if not entered.wait(timeout=5):
            if future.done():
                future.result(timeout=1)
            self.fail("advancement did not reach its workspace-locked preparation")

    def advance_service(self, factory, prefix):
        return CurrentGraphAdvancementCommandService(factory, clock=live_clock,
            id_factory=GeneratedIds(prefix))

    def protection(self):
        return tuple(self.connection.execute("SELECT * FROM " + table +
            " ORDER BY run_id,activity_id,attempt,artifact_id").fetchall()
            for table in ("cpk_effect_configuration_refs", "cpk_configuration_claims"))

    def assert_publication(self, command, accepted, before):
        self.carry.assert_membership(accepted, self.fixture.refs)
        with self.base.unit_of_work() as uow:
            workspace = uow.stores.workspaces.get("workspace-a")
        self.assertEqual((workspace.current_graph_id, workspace.current_realized_projection_id,
            workspace.desired_graph_revision), (command.desired_graph_id,
                command.desired_realized_projection_id, command.desired_graph_revision))
        after = dict(self.base.retained_snapshot())
        for table, rows in before:
            if table == "cpk_workspaces":
                self.assertEqual(len(after[table]), len(rows))
                continue
            added = {"cpk_operation_actions": 1, "cpk_activity_events": 1,
                "cpk_configuration_acceptances": 1, "cpk_configuration_accepted_slots": len(self.fixture.refs)}.get(table, 0)
            self.assertEqual(len(after[table]), len(rows) + added, table)
            self.assertTrue(all(row in after[table] for row in rows), table)

    def test_nonempty_distinct_key_advancements_have_one_atomic_winner(self):
        command = self.carry.prepare("race", "graph-race", StartRuntime(RuntimeTarget("runtime-b")),
            graph=self.runtime_graph(("runtime-b",)))
        before, protection = self.base.retained_snapshot(), self.protection()
        pids = queue.Queue()
        first = self.advance_service(self._factory_with_pids(pids), "winner")
        second = CurrentGraphAdvancementCommandService(self._factory_with_pids(pids), clock=live_clock,
            id_factory=lambda: self.fail("losing advancement allocated an ID"))
        with self.paused_prepare(command.run_id) as (entered, release), ThreadPoolExecutor(max_workers=2) as executor:
            winner = executor.submit(first.execute, command)
            try:
                self.wait_for_prepare(entered, winner)
                first_pid = pids.get(timeout=5)
                loser = executor.submit(second.execute, replace(command, idempotency_key=IdempotencyKey("advance-loser")))
                self._wait_until_blocked_by(pids.get(timeout=5), first_pid)
                release.set()
                accepted = winner.result(timeout=10)
                with self.assertRaises(CurrentGraphAdvancementConflict):
                    loser.result(timeout=10)
            finally:
                release.set()
        self.assert_publication(command, accepted, before)
        self.assertEqual(self.protection(), protection)
        self.assertEqual(self.connection.execute("SELECT count(*) FROM cpk_operation_actions "
            "WHERE idempotency_key='advance-loser'").fetchone(), (0,))

    def plan(self, label):
        first = ActivityId(label + "-b")
        return ActivityPlan((PlannedActivity(first, StartRuntime(RuntimeTarget("runtime-b"))),
            PlannedActivity(ActivityId(label + "-c"), StartRuntime(RuntimeTarget("runtime-c")),
                (ActivityDependency(first),))))

    def execute_plan(self, command, *, failed=False):
        engine = self.base.engine
        success = lambda _context, request: RuntimeEffectResult.succeeded(request.effect_id,
            evidence={"adapter": "configuration-concurrency"})
        failure = lambda _context, request: RuntimeEffectResult.failed(request.effect_id,
            RuntimeEffectFailure("configuration.concurrency", "Simulated terminal runtime failure."))
        adapter = coordinator_fixture.RecordingAdapter(engine.tracker, success, failure if failed else success)
        lifecycle = RunLifecycleCommandService(self.base.unit_of_work, clock=live_clock,
            id_factory=GeneratedIds("lifecycle-" + command.run_id))
        result = engine.coordinator(adapter, lifecycle=lifecycle, clock=live_clock).execute(replace(
            engine.command(generation=command.fence.generation, max_effects=2,
                idempotency_key="execute-" + command.run_id), run_id=command.run_id))
        self.assertIs(result.status, CoordinatorStatus.FAILED if failed else CoordinatorStatus.COMPLETED)
        self.assertEqual(len(adapter.calls), 2)
        self.assertEqual(adapter.active_during_calls, [0, 0])

    def failed_snapshot(self):
        return tuple((table, self.connection.execute("SELECT * FROM " + table + " WHERE " + where +
            " ORDER BY " + order).fetchall()) for table, where, order in (
            ("cpk_execution_requests", "request_id='request-failed'", "request_id"),
            ("cpk_activity_runs", "run_id='run-failed'", "run_id"),
            ("cpk_effect_attempts", "run_id='run-failed'", "activity_id,attempt"),
            ("cpk_effect_attempt_intents", "run_id='run-failed'", "activity_id,attempt"),
            ("cpk_effect_attempt_outcomes", "run_id='run-failed'", "activity_id,attempt"),
            ("cpk_activity_events", "run_id='run-failed'", "ordinal"),
            ("cpk_operation_actions", "session_id='session-failed'", "ordinal"),
            ("cpk_failed_run_compensations", "program_id='compensation-program'", "program_id"),
            ("cpk_failed_run_compensation_steps", "program_id='compensation-program'", "position"),
            ("cpk_failed_run_compensation_attempt_bindings", "program_id='compensation-program'", "position")))

    def test_advancement_precedes_no_lifecycle_lock_compensation_replay(self):
        failed = self.carry.admit("failed", "graph-overlap", StartRuntime(RuntimeTarget("runtime-b")),
            graph=self.runtime_graph(("runtime-b", "runtime-c")), plan=self.plan("failed"), clock=live_clock)
        self.execute_plan(failed, failed=True)
        with self.base.unit_of_work() as uow:
            request = uow.stores.execution.get_request("request-failed")
            run = uow.stores.execution.get_run("run-failed")
            plan = uow.stores.activity_history.get_plan("plan-failed")
            terminal = uow.stores.execution.events_for_run(run.run_id)[-1]
            self.assertFalse(uow.stores.execution.observe_request_lease_for_update(request.identity.request_id).expired)
        self.assertIs(request.status, ExecutionRequestStatus.CLAIMED)
        self.assertIs(run.status, ActivityRunStatus.FAILED)
        self.assertIs(terminal.kind, ActivityEventKind.RUN_FAILED)
        compensation = FailedRunCompensationCommandService(self.base.unit_of_work, clock=live_clock,
            id_factory=iter(("compensation-program", "compensation-event", "compensation-action")).__next__).execute(
            BeginFailedRunCompensation(workspace_id="workspace-a", request_id=request.identity.request_id,
                run_id=RunId(run.run_id), plan_id=plan.plan_id, expected_current_graph_id=plan.base_graph_id,
                desired_graph_id=plan.desired_graph_id, expected_desired_graph_revision=plan.desired_graph_revision,
                execution_intent_fingerprint=request.idempotency.intent_fingerprint,
                authority=RecoveryAuthority("operator-a", "concurrency-recovery", (RecoveryScope.COMPENSATE,)),
                reason=FailedRunCompensationReason.POST_EFFECT_FAILURE, source_failure=terminal.failure,
                idempotency_key=IdempotencyKey("begin-compensation")))
        self.assertIs(compensation.run.status, ActivityRunStatus.COMPENSATING)
        self.assertEqual(len(compensation.program.steps), 1)
        step = compensation.program.steps[0]
        with self.base.unit_of_work() as uow:
            original = uow.stores.effect_attempt_intents.get(step.source_effect.attempt_identity)
        replay_command = StartFailedRunCompensationAttempt(program_id=compensation.program.program_id,
            position=step.position, intent=replace(original.intent, operation=step.operation),
            authority=self.base.engine.authority(), fence=failed.fence)
        bound = FailedRunCompensationAttemptStartService(self.base.unit_of_work,
            id_factory=lambda: "inverse-start").execute(replay_command)
        self.assertFalse(bound.replayed)
        # Preserve exactly P/Q/projections/revision; a second desired selection
        # would invalidate F before the intended overlap.
        successful = self.carry.admit("successful", "graph-overlap", StartRuntime(RuntimeTarget("runtime-b")),
            plan=self.plan("successful"), reuse_selected_desired=True, clock=live_clock)
        for name in ("expected_current_graph_id", "expected_current_realized_projection_id", "desired_graph_id",
                "desired_realized_projection_id", "desired_graph_revision"):
            self.assertEqual(getattr(successful, name), getattr(failed, name), name)
        self.execute_plan(successful)
        before, protected, failed_before = self.base.retained_snapshot(), self.protection(), self.failed_snapshot()
        pids, role, locked_attempts = queue.Queue(), threading.local(), []
        original_attempt, original_workspace = EffectAttemptStore.get_for_update, PostgresWorkspaceStore.get_for_update
        original_lifecycle = PostgresGraphTopologyStore.lock_receiver_lifecycle
        workspace_requested = threading.Event()

        def attempt(store, identity):
            value = original_attempt(store, identity)
            if getattr(role, "value", None) == "replay":
                locked_attempts.append(identity)
            return value

        def workspace(store, workspace_id):
            if getattr(role, "value", None) == "replay":
                workspace_requested.set()
            return original_workspace(store, workspace_id)

        def lifecycle(store, *args, **kwargs):
            self.assertNotEqual(getattr(role, "value", None), "replay", "existing binding replay requested L")
            return original_lifecycle(store, *args, **kwargs)

        def execute(label, service, command):
            role.value = label
            return service.execute(command)

        advancement = self.advance_service(self._factory_with_pids(pids), "overlap")
        replay_queries = []

        class ObservedReplayConnection:
            def __init__(self, connection):
                self.connection = connection

            def __getattr__(self, name):
                return getattr(self.connection, name)

            def execute(self, query, *args, **kwargs):
                replay_queries.append(query.as_string(self.connection) if hasattr(query, "as_string") else str(query))
                return self.connection.execute(query, *args, **kwargs)

            def cursor(self, *args, **kwargs):
                raise AssertionError("replay SQL observer does not admit unobserved cursor dispatch")

            def executemany(self, *args, **kwargs):
                raise AssertionError("replay SQL observer does not admit unobserved executemany")

            def copy(self, *args, **kwargs):
                raise AssertionError("replay SQL observer does not admit unobserved COPY")

        def replay_connection():
            connection = ObservedReplayConnection(psycopg.connect(self.database_url))
            connection.execute("SET lock_timeout = '10s'")
            connection.execute("SET statement_timeout = '12s'")
            pids.put(connection.info.backend_pid)
            return connection

        replay = FailedRunCompensationAttemptStartService(lambda: PostgresUnitOfWork(replay_connection),
            id_factory=lambda: self.fail("existing compensation replay allocated an ID"))
        with mock.patch.object(EffectAttemptStore, "get_for_update", attempt), \
                mock.patch.object(PostgresWorkspaceStore, "get_for_update", workspace), \
                mock.patch.object(PostgresGraphTopologyStore, "lock_receiver_lifecycle", lifecycle), \
                self.paused_prepare(successful.run_id) as (entered, release), ThreadPoolExecutor(max_workers=2) as executor:
            winner = executor.submit(execute, "advancement", advancement, successful)
            try:
                self.wait_for_prepare(entered, winner)
                winner_pid = pids.get(timeout=5)
                contender = executor.submit(execute, "replay", replay, replay_command)
                replay_pid = pids.get(timeout=5)
                self.assertTrue(workspace_requested.wait(timeout=5))
                self.assertCountEqual(locked_attempts, (bound.binding.source_attempt, bound.binding.inverse_attempt))
                self._wait_until_blocked_by(replay_pid, winner_pid)
                release.set()
                accepted = winner.result(timeout=10)
                with self.assertRaisesRegex(FailedRunCompensationAttemptConflict, "compensation lineage is incongruent"):
                    contender.result(timeout=10)
            finally:
                release.set()
        self.assert_publication(successful, accepted, before)
        self.assertTrue(any(query.lstrip().upper().startswith("SELECT") for query in replay_queries))
        for query in replay_queries:
            # Row-lock clauses are reads. DML anywhere else (including a WITH
            # body) is forbidden even if the enclosing transaction rolls back.
            without_row_locks = re.sub(r"\bFOR\s+(?:NO\s+KEY\s+|KEY\s+)?(?:UPDATE|SHARE)\b", "", query,
                flags=re.IGNORECASE)
            self.assertIsNone(re.search(r"\b(?:INSERT|UPDATE|DELETE|MERGE|TRUNCATE|ALTER|CREATE|DROP|COPY)\b",
                without_row_locks, flags=re.IGNORECASE), query)
        self.assertEqual(self.protection(), protected)
        self.assertEqual(self.failed_snapshot(), failed_before)
