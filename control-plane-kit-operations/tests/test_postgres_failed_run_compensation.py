from __future__ import annotations

import concurrent.futures
import dataclasses
import importlib
import inspect
import threading
import unittest

import psycopg

from control_plane_kit_core.operations import (
    ActivityEventKind,
    ActivityRunStatus,
    RecoveryScope,
)
from control_plane_kit_core.planning import StopNode, StopRuntime
from control_plane_kit_operations.execution_lease_recovery import RecoveryAuthority
from control_plane_kit_operations.lifecycle import RunLifecycleConflict, RunLifecycleDenied
from control_plane_kit_operations.postgres import PostgresUnitOfWork
from control_plane_kit_operations.records import OperationsRecordError
from control_plane_kit_operations.workflows import IdempotencyKey

from tests.failed_run_compensation_fixture import (
    FailedRunCompensationFixture,
    Sequence,
    TARGET_MODULE,
    compensation_module,
)


class PostgresFailedRunCompensationTests(
    FailedRunCompensationFixture,
    unittest.TestCase,
):
    def test_first_admission_is_exact_atomic_and_preserves_originals(self) -> None:
        self.require_contract()
        self.seed_truth()
        before = self.original_truth()
        sequence = Sequence("program-a", "compensation-started", "action-a")

        result = self.service(sequence).execute(self.command())

        self.assertFalse(result.replayed)
        self.assertEqual(sequence.calls, ["program-a", "compensation-started", "action-a"])
        self.assertIs(result.run.status, ActivityRunStatus.COMPENSATING)
        self.assertIs(result.event.kind, ActivityEventKind.RUN_COMPENSATION_STARTED)
        self.assertEqual(result.event.ordinal, 10)
        self.assertEqual(result.action.action_type.value, "begin-compensation")
        self.assertEqual(result.program.program_id, "program-a")
        self.assertEqual(
            [step.source_effect.attempt_identity.activity_id for step in result.program.steps],
            ["start-node", "start-runtime"],
        )
        self.assertEqual(
            [type(step.operation) for step in result.program.steps],
            [StopNode, StopRuntime],
        )
        self.assertEqual(
            [step.source_effect.completion_ordinal for step in result.program.steps],
            [6, 4],
        )
        self.assertEqual(self.original_truth(), before)
        self.assertEqual(
            self.connection.execute(
                "SELECT status FROM cpk_activity_runs WHERE run_id='run-a'"
            ).fetchone(),
            ("compensating",),
        )
        self.assertEqual(
            self.connection.execute(
                "SELECT count(*) FROM cpk_failed_run_compensations"
            ).fetchone(),
            (1,),
        )
        self.assertEqual(
            tuple(
                self.connection.execute(
                    "SELECT position, source_activity_id, source_attempt, "
                    "source_completion_ordinal FROM "
                    "cpk_failed_run_compensation_steps ORDER BY position"
                ).fetchall()
            ),
            ((1, "start-node", 1, 6), (2, "start-runtime", 1, 4)),
        )
        self.assertEqual(result.record.program_fingerprint, result.program.fingerprint())
        self.assertRegex(result.record.evidence_fingerprint, r"^[0-9a-f]{64}$")
        self.assertRegex(result.record.authority_reference_fingerprint, r"^[0-9a-f]{64}$")

    def test_exact_replay_is_write_free_and_restart_stable(self) -> None:
        module = self.require_contract()
        self.seed_truth()
        first = self.service(
            Sequence("program-a", "compensation-started", "action-a")
        ).execute(self.command())
        before = self.snapshot_all()

        class Bomb:
            def __init__(self):
                self.calls = 0

            def __call__(self):
                self.calls += 1
                raise AssertionError("replay allocated fresh material")

        clock = Bomb()
        ids = Bomb()
        replayed = module.FailedRunCompensationCommandService(
            self.unit_of_work,
            clock=clock,
            id_factory=ids,
        ).execute(self.command())

        self.assertEqual(replayed, dataclasses.replace(first, replayed=True))
        self.assertEqual((clock.calls, ids.calls), (0, 0))
        self.assertEqual(self.snapshot_all(), before)

    def test_replay_rejects_changed_private_authority_provenance(self) -> None:
        module = self.require_contract()
        self.seed_truth()
        self.service(
            Sequence("program-a", "compensation-started", "action-a")
        ).execute(self.command())
        before = self.snapshot_all()
        changed_authority = RecoveryAuthority(
            "operator-a",
            "authority-reference-reissued",
            (RecoveryScope.COMPENSATE,),
        )

        with self.assertRaises(module.FailedRunCompensationIdempotencyConflict):
            self.service(Sequence()).execute(
                self.command(authority=changed_authority)
            )
        self.assertEqual(self.snapshot_all(), before)

    def test_replay_rejects_tampered_authority_fingerprint(self) -> None:
        self.seed_truth()
        self.service(
            Sequence("program-a", "compensation-started", "action-a")
        ).execute(self.command())
        self.connection.execute(
            "UPDATE cpk_failed_run_compensations SET "
            "authority_reference_fingerprint=repeat('f', 64) "
            "WHERE program_id='program-a'"
        )
        tampered = self.snapshot_all()
        with self.assertRaises(RunLifecycleConflict):
            self.service(Sequence()).execute(self.command())
        self.assertEqual(self.snapshot_all(), tampered)

    def test_restart_replay_rejects_relational_step_drift(self) -> None:
        mutations = (
            (
                "deleted-step",
                (
                    "DELETE FROM cpk_failed_run_compensation_steps "
                    "WHERE program_id='program-a' AND position=2",
                ),
            ),
            (
                "altered-step",
                (
                    "UPDATE cpk_failed_run_compensation_steps SET "
                    "material_source='base-graph' WHERE program_id='program-a' "
                    "AND position=1",
                ),
            ),
            (
                "reordered-steps",
                (
                    "UPDATE cpk_failed_run_compensation_steps SET position="
                    "CASE position WHEN 1 THEN 3 WHEN 2 THEN 1 END "
                    "WHERE program_id='program-a'",
                    "UPDATE cpk_failed_run_compensation_steps SET position=2 "
                    "WHERE program_id='program-a' AND position=3",
                ),
            ),
        )
        for name, statements in mutations:
            with self.subTest(name=name):
                self._seed_admitted_program()
                for statement in statements:
                    self.connection.execute(statement)
                before = self.snapshot_all()
                with self.assertRaises((RunLifecycleConflict, OperationsRecordError)):
                    self.service(Sequence()).execute(self.command())
                self.assertEqual(self.snapshot_all(), before)

    def test_restart_replay_rejects_parent_action_event_and_run_drift(self) -> None:
        mutations = (
            (
                "parent-plan-lineage",
                self._drift_parent_plan,
            ),
            (
                "parent-source-failure",
                lambda: self.connection.execute(
                    "UPDATE cpk_failed_run_compensations SET source_failure="
                    "jsonb_set(source_failure, '{code}', '\"runtime.other-failure\"') "
                    "WHERE program_id='program-a'"
                ),
            ),
            (
                "action-actor",
                lambda: self.connection.execute(
                    "UPDATE cpk_operation_actions SET actor_id='operator-foreign' "
                    "WHERE action_id='action-a'"
                ),
            ),
            (
                "event-program",
                lambda: self.connection.execute(
                    "UPDATE cpk_activity_events SET payload=jsonb_set("
                    "payload, '{evidence,program_id}', '\"program-foreign\"') "
                    "WHERE event_id='compensation-started'"
                ),
            ),
            (
                "run-status",
                lambda: self.connection.execute(
                    "UPDATE cpk_activity_runs SET status='failed' "
                    "WHERE run_id='run-a'"
                ),
            ),
        )
        for name, mutate in mutations:
            with self.subTest(name=name):
                self._seed_admitted_program()
                mutate()
                before = self.snapshot_all()
                with self.assertRaises((RunLifecycleConflict, OperationsRecordError)):
                    self.service(Sequence()).execute(self.command())
                self.assertEqual(self.snapshot_all(), before)

    def test_every_admission_write_rolls_back_on_late_failure(self) -> None:
        write_boundaries = (
            "event",
            "action",
            "program",
            "step-1",
            "step-2",
            "run-fold",
            "commit",
        )
        for fail_at, name in enumerate(write_boundaries, start=1):
            with self.subTest(name=name):
                self.connection.execute("TRUNCATE TABLE cpk_workspaces CASCADE")
                self.seed_truth()
                before = self.snapshot_all()
                raw = RawDependencyFailure(f"late-{name}-canary")
                factory = self.failing_unit_of_work(fail_at, raw)
                with self.assertRaises(RawDependencyFailure) as raised:
                    self.require_contract().FailedRunCompensationCommandService(
                        factory,
                        clock=lambda: "2026-08-25T12:00:00Z",
                        id_factory=Sequence(
                            "program-a",
                            "compensation-started",
                            "action-a",
                        ),
                    ).execute(self.command())
                self.assertIs(raised.exception, raw)
                self.assertEqual(self.snapshot_all(), before)

    def test_two_connection_same_key_replays_one_exact_program(self) -> None:
        self.seed_truth()
        barrier = threading.Barrier(2)

        def execute(suffix: str):
            barrier.wait(timeout=5)
            return self.service(
                Sequence(
                    f"program-{suffix}",
                    f"compensation-started-{suffix}",
                    f"action-{suffix}",
                )
            ).execute(self.command())

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            results = tuple(
                future.result(timeout=15)
                for future in (
                    executor.submit(execute, "b"),
                    executor.submit(execute, "c"),
                )
            )

        self.assertEqual(sum(not result.replayed for result in results), 1)
        self.assertEqual(sum(result.replayed for result in results), 1)
        self.assertEqual(results[0].program, results[1].program)
        self.assertEqual(results[0].record, results[1].record)
        self.assertEqual(
            self.connection.execute(
                "SELECT count(*) FROM cpk_failed_run_compensations"
            ).fetchone(),
            (1,),
        )

    def test_two_connection_competing_keys_admit_exactly_one_program(self) -> None:
        self.seed_truth()
        barrier = threading.Barrier(2)

        def execute(suffix: str):
            barrier.wait(timeout=5)
            try:
                return self.service(
                    Sequence(
                        f"program-{suffix}",
                        f"compensation-started-{suffix}",
                        f"action-{suffix}",
                    )
                ).execute(
                    self.command(
                        idempotency_key=IdempotencyKey(f"compensate-{suffix}")
                    )
                )
            except RunLifecycleConflict as error:
                return error

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            results = tuple(
                future.result(timeout=15)
                for future in (
                    executor.submit(execute, "b"),
                    executor.submit(execute, "c"),
                )
            )

        self.assertEqual(
            sum(not isinstance(result, BaseException) for result in results),
            1,
        )
        self.assertEqual(
            sum(isinstance(result, RunLifecycleConflict) for result in results),
            1,
        )
        self.assertEqual(
            self.connection.execute(
                "SELECT count(*) FROM cpk_failed_run_compensations"
            ).fetchone(),
            (1,),
        )

    def test_rejection_matrix_is_fail_closed_and_write_free(self) -> None:
        module = self.require_contract()
        cases = (
            (
                "wrong-scope",
                lambda: self.command(
                    authority=RecoveryAuthority(
                        "operator-a",
                        "authority-reference-a",
                        (RecoveryScope.OPERATE,),
                    )
                ),
                (ValueError, RunLifecycleDenied),
            ),
            (
                "foreign-workspace",
                lambda: self.command(workspace_id="workspace-foreign"),
                (RunLifecycleConflict,),
            ),
            (
                "foreign-request",
                lambda: self.command(request_id="request-foreign"),
                (RunLifecycleConflict,),
            ),
            (
                "foreign-plan",
                lambda: self.command(plan_id="plan-foreign"),
                (RunLifecycleConflict,),
            ),
            (
                "stale-current-graph",
                lambda: self.command(expected_current_graph_id="graph-stale"),
                (RunLifecycleConflict,),
            ),
            (
                "changed-intent",
                lambda: self.command(execution_intent_fingerprint="f" * 64),
                (RunLifecycleConflict,),
            ),
        )
        for name, command_factory, errors in cases:
            with self.subTest(name=name):
                self.connection.execute("TRUNCATE TABLE cpk_workspaces CASCADE")
                self.seed_truth()
                before = self.snapshot_all(include_compensation=False)
                with self.assertRaises(errors):
                    self.service(Sequence("program-x", "event-x", "action-x")).execute(
                        command_factory()
                    )
                self.assertEqual(
                    self.snapshot_all(include_compensation=False),
                    before,
                )
                self.assertEqual(
                    self.connection.execute(
                        "SELECT count(*) FROM cpk_failed_run_compensations"
                    ).fetchone(),
                    (0,),
                )

    def test_uncertain_fabricated_missing_and_already_started_truth_is_rejected(self) -> None:
        module = self.require_contract()
        mutations = (
            (
                "unresolved-uncertainty",
                "UPDATE cpk_effect_attempts SET status='uncertain', "
                "outcome_fingerprint='f' || repeat('0', 63) "
                "WHERE activity_id='start-node'",
            ),
            (
                "fabricated-success",
                "DELETE FROM cpk_effect_attempt_outcomes "
                "WHERE activity_id='start-node'",
            ),
        )
        for name, statement in mutations:
            with self.subTest(name=name):
                self.connection.execute("TRUNCATE TABLE cpk_workspaces CASCADE")
                self.seed_truth()
                if name == "fabricated-success":
                    self.connection.execute(
                        "DELETE FROM cpk_effect_attempt_outcome_observations "
                        "WHERE activity_id='start-node'"
                    )
                self.connection.execute(statement)
                with self.assertRaises(RunLifecycleConflict):
                    self.service(Sequence("program-x", "event-x", "action-x")).execute(
                        self.command()
                    )

        self.connection.execute("TRUNCATE TABLE cpk_workspaces CASCADE")
        self.seed_truth()
        self.service(
            Sequence("program-a", "compensation-started", "action-a")
        ).execute(self.command())
        with self.assertRaises(RunLifecycleConflict):
            self.service(Sequence("program-b", "event-b", "action-b")).execute(
                self.command(idempotency_key=IdempotencyKey("compensate-b"))
            )

    def test_schema_and_source_have_no_provider_execution_surface(self) -> None:
        module = self.require_contract()
        source = inspect.getsource(module)
        for forbidden in (
            "docker",
            "RuntimeEffectInterpreter",
            ".execute(",
            "prune",
            "cleanup",
            "retry",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, source)
        relations = {
            row[0]
            for row in self.connection.execute(
                "SELECT tablename FROM pg_tables WHERE schemaname=current_schema()"
            ).fetchall()
        }
        self.assertIn("cpk_failed_run_compensations", relations)
        self.assertIn("cpk_failed_run_compensation_steps", relations)

    def snapshot_all(self, *, include_compensation: bool = True):
        values = (
            tuple(
                self.connection.execute(
                    "SELECT run_id, status, started_at, settled_at, metadata "
                    "FROM cpk_activity_runs ORDER BY run_id"
                ).fetchall()
            ),
            tuple(
                self.connection.execute(
                    "SELECT event_id, run_id, ordinal, event_type, payload "
                    "FROM cpk_activity_events ORDER BY run_id, ordinal"
                ).fetchall()
            ),
            tuple(
                self.connection.execute(
                    "SELECT action_id, session_id, ordinal, action_type, payload, "
                    "idempotency_key, intent_fingerprint FROM "
                    "cpk_operation_actions ORDER BY session_id, ordinal"
                ).fetchall()
            ),
            self.original_truth(),
        )
        if not include_compensation:
            return values
        return (
            *values,
            tuple(
                self.connection.execute(
                    "SELECT * FROM cpk_failed_run_compensations ORDER BY program_id"
                ).fetchall()
            ),
            tuple(
                self.connection.execute(
                    "SELECT * FROM cpk_failed_run_compensation_steps "
                    "ORDER BY program_id, position"
                ).fetchall()
            ),
        )

    def _seed_admitted_program(self) -> None:
        self.connection.execute("TRUNCATE TABLE cpk_workspaces CASCADE")
        self.seed_truth()
        self.service(
            Sequence("program-a", "compensation-started", "action-a")
        ).execute(self.command())

    def _copy_plan(self) -> None:
        self.connection.execute(
            "INSERT INTO cpk_activity_plans "
            "(plan_id, session_id, base_graph_id, desired_graph_id, "
            "base_realized_projection_id, desired_realized_projection_id, "
            "desired_graph_revision, status, created_at, payload) "
            "SELECT 'plan-b', session_id, base_graph_id, desired_graph_id, "
            "base_realized_projection_id, desired_realized_projection_id, "
            "desired_graph_revision, status, created_at, payload FROM "
            "cpk_activity_plans WHERE plan_id='plan-a'"
        )

    def _drift_parent_plan(self) -> None:
        self._copy_plan()
        self.connection.execute(
            "UPDATE cpk_failed_run_compensations SET plan_id='plan-b' "
            "WHERE program_id='program-a'"
        )

    def failing_unit_of_work(self, fail_at: int, error: BaseException):
        database_url = self.database_url

        class Connection:
            def __init__(self):
                self._connection = psycopg.connect(database_url)
                self._writes = 0

            def execute(self, query, params=()):
                cursor = self._connection.execute(query, params)
                normalized = " ".join(query.split()).upper()
                if normalized.startswith(("INSERT INTO CPK_ACTIVITY_EVENTS", "INSERT INTO CPK_OPERATION_ACTIONS", "INSERT INTO CPK_FAILED_RUN_COMPENSATIONS", "INSERT INTO CPK_FAILED_RUN_COMPENSATION_STEPS", "UPDATE CPK_ACTIVITY_RUNS")):
                    self._writes += 1
                    if self._writes == fail_at:
                        raise error
                return cursor

            def commit(self):
                if fail_at == 7:
                    raise error
                return self._connection.commit()

            def rollback(self):
                return self._connection.rollback()

            def close(self):
                return self._connection.close()

        return lambda: PostgresUnitOfWork(Connection)


class RawDependencyFailure(RuntimeError):
    pass


if __name__ == "__main__":
    unittest.main()
