from __future__ import annotations

import dataclasses
from dataclasses import replace
import pathlib
import threading
import unittest

import psycopg

from control_plane_kit_core.operations import (
    ActivityEventKind,
    EffectAttemptIdentity,
    EffectAttemptStatus,
    RunId,
)
from control_plane_kit_core.planning import ActivityId
from control_plane_kit_operations.effect_attempt_intent_evidence import (
    EffectAttemptIntentRecord,
)
from control_plane_kit_operations.execution_leases import ExecutionLeaseFence
from control_plane_kit_operations.lifecycle import ExecutionWorkerAuthority
from control_plane_kit_operations.postgres.current_schema_contract import (
    CURRENT_POSTGRES_SCHEMA_CONTRACT,
)
from control_plane_kit_operations.records import OperationsRecordError
from tests.failed_run_compensation_attempt_fixture import (
    FailedRunCompensationAttemptFixture,
    TARGET_MODULE,
    attempt_module,
)


class PostgresFailedRunCompensationAttemptTests(
    FailedRunCompensationAttemptFixture,
    unittest.TestCase,
):
    def test_closed_language_and_direct_current_schema_are_present(self) -> None:
        module = self.require_attempt_contract()
        required = (
            "FailedRunCompensationAttemptBinding",
            "StartFailedRunCompensationAttempt",
            "FailedRunCompensationAttemptStartResult",
            "NewlyBoundCompensationAttempt",
            "ExistingCompensationAttemptBinding",
            "FailedRunCompensationAttemptStartService",
            "FailedRunCompensationAttemptConflict",
            "FailedRunCompensationAttemptDenied",
            "FailedRunCompensationAttemptNotFound",
        )
        self.assertEqual(
            [name for name in required if getattr(module, name, None) is None],
            [],
        )
        self.assertEqual(
            tuple(
                field.name
                for field in dataclasses.fields(
                    module.FailedRunCompensationAttemptBinding
                )
            ),
            (
                "program_id",
                "position",
                "source_attempt",
                "inverse_attempt",
            ),
        )
        schema = pathlib.Path(
            __file__
        ).parents[1] / "src/control_plane_kit_operations/postgres/current_schema.sql"
        source = schema.read_text(encoding="utf-8")
        self.assertIn(
            "CREATE TABLE cpk_failed_run_compensation_attempt_bindings",
            source,
        )
        relation = "cpk_failed_run_compensation_attempt_bindings"
        self.assertEqual(
            tuple(
                column.name
                for column in CURRENT_POSTGRES_SCHEMA_CONTRACT.columns
                if column.relation == relation
            ),
            (
                "program_id",
                "position",
                "source_run_id",
                "source_activity_id",
                "source_attempt",
                "inverse_run_id",
                "inverse_activity_id",
                "inverse_attempt",
            ),
        )
        constraint_names = {
            constraint.name
            for constraint in CURRENT_POSTGRES_SCHEMA_CONTRACT.constraints
            if constraint.relation == relation
        }
        self.assertEqual(
            constraint_names,
            {
                "cpk_failed_run_compensation_attempt_bindings_identity_check",
                "cpk_failed_run_compensation_attempt_bindings_inverse_attempt_fk",
                "cpk_failed_run_compensation_attempt_bindings_inverse_key",
                "cpk_failed_run_compensation_attempt_bindings_pkey",
                "cpk_failed_run_compensation_attempt_bindings_source_step_fk",
            },
        )

    def test_first_incomplete_step_starts_one_exact_linked_inverse_atomically(self) -> None:
        self.seed_admitted_program()
        originals = self.source_truth_snapshot()

        result = self.attempt_service("inverse-start-a").execute(
            self.start_command(position=1)
        )

        module = self.require_attempt_contract()
        self.assertIsInstance(result, module.NewlyBoundCompensationAttempt)
        self.assertFalse(result.replayed)
        binding = result.binding
        self.assertEqual(binding.program_id, "program-a")
        self.assertEqual(binding.position, 1)
        self.assertEqual(
            binding.source_attempt,
            EffectAttemptIdentity(RunId("run-a"), "start-node", 1),
        )
        self.assertEqual(
            binding.inverse_attempt,
            EffectAttemptIdentity(RunId("run-a"), "start-node", 2),
        )
        self.assertEqual(result.attempt.state.status, EffectAttemptStatus.STARTED)
        self.assertEqual(
            result.attempt.state.prior_attempt,
            binding.source_attempt,
        )
        self.assertEqual(
            result.attempt.original_start_event.kind,
            ActivityEventKind.STEP_COMPENSATION_STARTED,
        )
        self.assertEqual(result.intent.identity, binding.inverse_attempt)
        self.assertIsInstance(result.intent, EffectAttemptIntentRecord)
        self.assertEqual(self.source_truth_snapshot(), originals)
        self.assertEqual(
            self.connection.execute(
                "SELECT current_graph_id, desired_graph_id, "
                "desired_graph_revision FROM cpk_workspaces "
                "WHERE workspace_id='workspace-a'"
            ).fetchone(),
            ("graph-current", "graph-desired", 1),
        )

    def test_exact_duplicate_is_write_free_and_incongruent_replay_fails(self) -> None:
        self.seed_admitted_program()
        service = self.attempt_service("inverse-start-a")
        command = self.start_command(position=1)
        module = self.require_attempt_contract()
        with self.assertRaises(module.FailedRunCompensationAttemptConflict):
            self.attempt_service("must-not-allocate").execute(
                self.start_command(position=2)
            )
        first = service.execute(command)
        before = self.binding_snapshot()

        replay = self.attempt_service("must-not-allocate").execute(command)

        self.assertIsInstance(replay, module.ExistingCompensationAttemptBinding)
        self.assertTrue(replay.replayed)
        self.assertEqual(replay.binding, first.binding)
        self.assertEqual(self.binding_snapshot(), before)
        hostile = (
            replace(command, intent=replace(command.intent, activity_id=ActivityId("start-runtime"))),
            replace(command, fence=replace(command.fence, generation=2)),
        )
        for candidate in hostile:
            with self.subTest(candidate=candidate.position):
                with self.assertRaises(module.FailedRunCompensationAttemptError):
                    self.attempt_service("must-not-allocate").execute(candidate)
                self.assertEqual(self.binding_snapshot(), before)

    def test_two_connections_racing_same_step_commit_one_binding(self) -> None:
        self.seed_admitted_program()
        barrier = threading.Barrier(2)
        results = []
        errors = []

        def run(identifier: str) -> None:
            try:
                barrier.wait(timeout=10)
                results.append(
                    self.attempt_service(identifier).execute(
                        self.start_command(position=1)
                    )
                )
            except BaseException as error:
                errors.append(error)

        threads = tuple(
            threading.Thread(target=run, args=(f"inverse-start-{index}",))
            for index in (1, 2)
        )
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=20)
        self.assertEqual(errors, [])
        self.assertEqual(len(results), 2)
        self.assertEqual(sum(not result.replayed for result in results), 1)
        self.assertEqual(sum(result.replayed for result in results), 1)
        snapshot = self.binding_snapshot()
        self.assertEqual(
            tuple(len(section) for section in snapshot),
            (1, 1, 1, 1, 0),
        )

    def test_next_step_requires_every_prior_binding_succeeded_with_outcome(
        self,
    ) -> None:
        module = self.require_attempt_contract()
        blocking = (
            EffectAttemptStatus.STARTED,
            EffectAttemptStatus.FAILED,
            EffectAttemptStatus.UNSUPPORTED,
            EffectAttemptStatus.UNCERTAIN,
            EffectAttemptStatus.ABANDONED,
        )
        for status in blocking:
            with self.subTest(prior_status=status.value):
                self.connection.execute("TRUNCATE TABLE cpk_workspaces CASCADE")
                self.seed_admitted_program()
                self.attempt_service("inverse-start-a").execute(
                    self.start_command(position=1)
                )
                if status is not EffectAttemptStatus.STARTED:
                    self.fold_bound_attempt(status)
                before = self.binding_snapshot()
                with self.assertRaises(
                    module.FailedRunCompensationAttemptConflict
                ):
                    self.attempt_service("must-not-allocate").execute(
                        self.start_command(position=2)
                    )
                self.assertEqual(self.binding_snapshot(), before)

        hostile_prior_truth = (
            (
                "missing-outcome",
                "DELETE FROM cpk_effect_attempt_outcomes WHERE attempt=2",
            ),
            (
                "incongruent-intent",
                "UPDATE cpk_effect_attempt_intents SET preimage='{}'::bytea "
                "WHERE attempt=2",
            ),
        )
        for label, mutation in hostile_prior_truth:
            with self.subTest(prior_truth=label):
                self.connection.execute("TRUNCATE TABLE cpk_workspaces CASCADE")
                self.seed_admitted_program()
                self.attempt_service("inverse-start-a").execute(
                    self.start_command(position=1)
                )
                self.fold_bound_attempt(EffectAttemptStatus.SUCCEEDED)
                self.connection.execute(mutation)
                before = self.binding_snapshot()
                with self.assertRaises(
                    module.FailedRunCompensationAttemptConflict
                ):
                    self.attempt_service("must-not-allocate").execute(
                        self.start_command(position=2)
                    )
                self.assertEqual(self.binding_snapshot(), before)

        self.connection.execute("TRUNCATE TABLE cpk_workspaces CASCADE")
        self.seed_admitted_program()
        self.attempt_service("inverse-start-a").execute(
            self.start_command(position=1)
        )
        self.fold_bound_attempt(EffectAttemptStatus.SUCCEEDED)
        admitted = self.attempt_service("inverse-start-b").execute(
            self.start_command(position=2)
        )
        self.assertIsInstance(admitted, module.NewlyBoundCompensationAttempt)
        self.assertEqual(admitted.binding.position, 2)

    def test_exact_replay_accepts_every_terminal_bound_attempt_write_free(
        self,
    ) -> None:
        module = self.require_attempt_contract()
        terminal = (
            EffectAttemptStatus.SUCCEEDED,
            EffectAttemptStatus.FAILED,
            EffectAttemptStatus.UNSUPPORTED,
            EffectAttemptStatus.UNCERTAIN,
            EffectAttemptStatus.ABANDONED,
        )
        for status in terminal:
            with self.subTest(status=status.value):
                self.connection.execute("TRUNCATE TABLE cpk_workspaces CASCADE")
                self.seed_admitted_program()
                first = self.attempt_service("inverse-start-a").execute(
                    self.start_command(position=1)
                )
                self.fold_bound_attempt(status)
                before = self.binding_snapshot()
                try:
                    replay = self.attempt_service("must-not-allocate").execute(
                        self.start_command(position=1)
                    )
                except module.FailedRunCompensationAttemptError as error:
                    self.fail(
                        f"exact {status.value} replay was rejected: {error}"
                    )
                self.assertIsInstance(
                    replay,
                    module.ExistingCompensationAttemptBinding,
                )
                self.assertTrue(replay.replayed)
                self.assertEqual(replay.binding, first.binding)
                self.assertEqual(replay.attempt.state.status, status)
                self.assertEqual(self.binding_snapshot(), before)

    def test_changed_program_source_graph_approval_authority_and_fence_fail_closed(
        self,
    ) -> None:
        module = self.require_attempt_contract()
        cases = (
            (
                "program",
                lambda command: replace(command, program_id="program-missing"),
                None,
            ),
            (
                "source-outcome",
                lambda command: command,
                "UPDATE cpk_failed_run_compensation_steps SET "
                "source_outcome_fingerprint='ffffffffffffffffffffffffffffffff"
                "ffffffffffffffffffffffffffffffff' WHERE program_id='program-a' "
                "AND position=1",
            ),
            (
                "graph",
                lambda command: replace(
                    command,
                    intent=replace(
                        command.intent,
                        source=replace(
                            command.intent.source,
                            base_graph_id="graph-foreign",
                        ),
                    ),
                ),
                None,
            ),
            (
                "approval",
                lambda command: command,
                "UPDATE cpk_approval_decisions SET decision='rejected' "
                "WHERE decision_id='approval-decision-a'",
            ),
            (
                "authority",
                lambda command: replace(
                    command,
                    authority=ExecutionWorkerAuthority(
                        "worker-b",
                        command.authority.scopes,
                    ),
                    fence=ExecutionLeaseFence("worker-b", 1),
                ),
                None,
            ),
            (
                "fence",
                lambda command: replace(
                    command,
                    fence=ExecutionLeaseFence("worker-a", 2),
                ),
                None,
            ),
        )
        for label, mutate, sql in cases:
            with self.subTest(case=label):
                self.connection.execute("TRUNCATE TABLE cpk_workspaces CASCADE")
                self.seed_admitted_program()
                before = self.binding_snapshot()
                command = mutate(self.start_command(position=1))
                if sql is not None:
                    self.connection.execute(sql)
                with self.assertRaises(module.FailedRunCompensationAttemptError):
                    self.attempt_service("must-not-allocate").execute(command)
                self.assertEqual(self.binding_snapshot(), before)

    def test_failure_after_each_write_boundary_rolls_back_every_owned_row(self) -> None:
        module = self.require_attempt_contract()
        for fail_at in range(1, 6):
            with self.subTest(fail_at=fail_at):
                self.connection.execute("TRUNCATE TABLE cpk_workspaces CASCADE")
                self.seed_admitted_program()
                before = self.binding_snapshot()
                error = RawWriteFailure(f"write-{fail_at}")
                with self.assertRaises(RawWriteFailure) as caught:
                    self.attempt_service(
                        "inverse-start-a",
                        unit_of_work=self.failing_unit_of_work(fail_at, error),
                    ).execute(self.start_command(position=1))
                self.assertIs(caught.exception, error)
                self.assertEqual(self.binding_snapshot(), before)
                self.assertEqual(
                    self.connection.execute(
                        "SELECT count(*) FROM cpk_failed_run_compensation_attempt_bindings"
                    ).fetchone()[0],
                    0,
                )

    def test_restart_reconstructs_bidirectional_binding_and_rejects_lineage_drift(self) -> None:
        self.seed_admitted_program()
        result = self.attempt_service("inverse-start-a").execute(
            self.start_command(position=1)
        )
        module = self.require_attempt_contract()
        with self.unit_of_work() as unit_of_work:
            store = unit_of_work.stores.failed_run_compensation_attempts
            self.assertEqual(store.get("program-a", 1), result.binding)
            self.assertEqual(
                store.get_for_attempt(result.binding.inverse_attempt),
                result.binding,
            )
        before = self.binding_snapshot()
        with self.assertRaises(psycopg.IntegrityError):
            self.connection.execute(
                "UPDATE cpk_failed_run_compensation_attempt_bindings "
                "SET inverse_attempt=3 WHERE program_id='program-a' AND position=1"
            )
        self.assertEqual(self.binding_snapshot(), before)
        drift_rows = (
            "UPDATE cpk_failed_run_compensation_steps SET "
            "source_outcome_fingerprint=%s WHERE program_id='program-a' AND position=1",
            "UPDATE cpk_effect_attempt_intents SET preimage=%s "
            "WHERE run_id='run-a' AND activity_id='start-node' AND attempt=2",
        )
        for position, query in enumerate(drift_rows, start=1):
            with self.subTest(drift=position):
                params = (("f" * 64) if position == 1 else b"{}",)
                self.connection.execute(query, params)
                with self.assertRaises((OperationsRecordError, module.FailedRunCompensationAttemptConflict)):
                    self.attempt_service("must-not-allocate").execute(
                        self.start_command(position=1)
                    )
                self.connection.execute("TRUNCATE TABLE cpk_workspaces CASCADE")
                self.seed_admitted_program()
                result = self.attempt_service("inverse-start-a").execute(
                    self.start_command(position=1)
                )
                before = self.binding_snapshot()

    def test_public_evidence_is_bounded_redacted_and_has_no_execution_surface(self) -> None:
        module = self.require_attempt_contract()
        source = pathlib.Path(module.__file__).read_text(encoding="utf-8")
        for forbidden in (
            "RuntimeInterpreterDispatcher",
            "ActivityExecutionDispatcher",
            "COMPENSATED",
            "PARTIALLY_FAILED",
            "docker",
            "prune",
            "provider",
            "http",
            "mcp",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, source)

    def failing_unit_of_work(self, fail_at: int, error: BaseException):
        database_url = self.database_url

        class Connection:
            def __init__(self):
                self.connection = psycopg.connect(database_url)
                self.writes = 0

            def execute(self, query, params=()):
                cursor = self.connection.execute(query, params)
                normalized = " ".join(str(query).split()).upper()
                if normalized.startswith((
                    "INSERT INTO CPK_ACTIVITY_EVENTS",
                    "INSERT INTO CPK_EFFECT_ATTEMPT_INTENTS",
                    "INSERT INTO CPK_EFFECT_ATTEMPTS",
                    "INSERT INTO CPK_FAILED_RUN_COMPENSATION_ATTEMPT_BINDINGS",
                )):
                    self.writes += 1
                    if self.writes == fail_at:
                        raise error
                return cursor

            def commit(self):
                if fail_at == 5:
                    raise error
                return self.connection.commit()

            def rollback(self):
                return self.connection.rollback()

            def close(self):
                return self.connection.close()

        from control_plane_kit_operations.postgres import PostgresUnitOfWork

        return lambda: PostgresUnitOfWork(Connection)


class RawWriteFailure(RuntimeError):
    pass


if __name__ == "__main__":
    unittest.main()
