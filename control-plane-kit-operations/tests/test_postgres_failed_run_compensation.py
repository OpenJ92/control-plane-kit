from __future__ import annotations

import dataclasses
import importlib
import inspect
import unittest

from control_plane_kit_core.operations import (
    ActivityEventKind,
    ActivityRunStatus,
    RecoveryScope,
)
from control_plane_kit_core.planning import StopNode, StopRuntime
from control_plane_kit_operations.execution_lease_recovery import RecoveryAuthority
from control_plane_kit_operations.lifecycle import RunLifecycleConflict, RunLifecycleDenied
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


if __name__ == "__main__":
    unittest.main()
