from __future__ import annotations

import os
import unittest
import uuid

import psycopg
from psycopg.errors import CheckViolation, NotNullViolation, UndefinedColumn

from control_plane_kit.stores import install_schema


class ExecutionSchemaTests(unittest.TestCase):
    """Current-schema proofs; unreleased development formats are unsupported."""

    def setUp(self) -> None:
        self.schema = f"execution_current_{uuid.uuid4().hex}"
        self.connection = psycopg.connect(
            os.environ["CPK_TEST_DATABASE_URL"],
            autocommit=True,
        )
        self.connection.execute(f'CREATE SCHEMA "{self.schema}"')
        self.connection.execute(f'SET search_path TO "{self.schema}"')

    def tearDown(self) -> None:
        self.connection.execute("SET search_path TO public")
        self.connection.execute(f'DROP SCHEMA "{self.schema}" CASCADE')
        self.connection.close()

    def test_repeated_install_preserves_rows_and_constraint_identity(self) -> None:
        install_schema(self.connection)
        self._seed_execution_truth()
        before = self._constraint_identities()

        install_schema(self.connection)

        self.assertEqual(self._constraint_identities(), before)
        self.assertEqual(
            self.connection.execute(
                """
                SELECT event_id, event_type, payload->>'activity_id'
                FROM cpk_activity_events ORDER BY ordinal
                """
            ).fetchall(),
            [
                ("event-forward-failed", "step_failed", "start-api"),
                ("event-recovery", "recovery_decision_recorded", None),
                (
                    "event-compensation-failed",
                    "step_compensation_failed",
                    "start-api",
                ),
            ],
        )
        self.assertEqual(
            self.connection.execute(
                "SELECT status, request_id FROM cpk_activity_runs"
            ).fetchone(),
            ("partially_failed", "request-a"),
        )

    def test_current_schema_rejects_unknown_retired_and_malformed_events(self) -> None:
        install_schema(self.connection)
        self._seed_execution_truth(include_events=False)

        invalid_events = (
            ("unknown", "invented", {"activity_id": None, "recovery": None}),
            (
                "retired",
                "compensation_started",
                {"activity_id": None, "recovery": None},
            ),
            ("step-without-id", "step_started", {"recovery": None}),
            (
                "run-with-id",
                "run_started",
                {"activity_id": "start-api", "recovery": None},
            ),
            (
                "recovery-without-record",
                "recovery_decision_recorded",
                {"activity_id": None, "recovery": None},
            ),
            (
                "recovery-without-key",
                "recovery_decision_recorded",
                {"activity_id": None},
            ),
            (
                "ordinary-with-recovery",
                "run_failed",
                {"activity_id": None, "recovery": {"decision_id": "decision-a"}},
            ),
        )
        for event_id, event_type, payload in invalid_events:
            with self.subTest(event_type=event_type, event_id=event_id):
                with self.assertRaises(CheckViolation):
                    self.connection.execute(
                        """
                        INSERT INTO cpk_activity_events
                          (event_id, run_id, ordinal, event_type, occurred_at, payload)
                        VALUES (%s, 'run-a', 20, %s, 'invalid-at', %s::jsonb)
                        """,
                        (event_id, event_type, psycopg.types.json.Jsonb(payload)),
                    )

        with self.assertRaises(CheckViolation):
            self.connection.execute(
                "UPDATE cpk_activity_runs SET status = 'invented' WHERE run_id = 'run-a'"
            )
        with self.assertRaises(NotNullViolation):
            self.connection.execute(
                "UPDATE cpk_activity_runs SET request_id = NULL WHERE run_id = 'run-a'"
            )

    def test_incompatible_schema_install_rolls_back_partial_ddl(self) -> None:
        self.connection.execute(
            "CREATE TABLE cpk_activity_runs (run_id text PRIMARY KEY)"
        )
        self.connection.autocommit = False
        try:
            with self.assertRaises(UndefinedColumn):
                install_schema(self.connection)
            self.connection.rollback()
        finally:
            self.connection.autocommit = True

        tables = {
            row[0]
            for row in self.connection.execute(
                """
                SELECT table_name FROM information_schema.tables
                WHERE table_schema = current_schema()
                """
            ).fetchall()
        }
        self.assertEqual(tables, {"cpk_activity_runs"})

    def _constraint_identities(self) -> tuple[tuple[str, int], ...]:
        return tuple(
            self.connection.execute(
                """
                SELECT conname, oid
                FROM pg_constraint
                WHERE connamespace = current_schema()::regnamespace
                ORDER BY conname
                """
            ).fetchall()
        )

    def _seed_execution_truth(self, *, include_events: bool = True) -> None:
        self.connection.execute(
            """
            INSERT INTO cpk_workspaces (workspace_id, name, lifecycle)
            VALUES ('workspace-a', 'Current workspace', 'active');
            INSERT INTO cpk_operation_sessions
              (session_id, workspace_id, actor_id, title, status, created_at)
            VALUES ('session-a', 'workspace-a', 'operator', 'Recovery', 'open',
                    'created-at');
            INSERT INTO cpk_activity_plans
              (plan_id, session_id, base_graph_id, desired_graph_id, status,
               created_at, payload)
            VALUES ('plan-a', 'session-a', 'graph-a', 'graph-b', 'planned',
                    'planned-at', '{}'::jsonb);
            INSERT INTO cpk_approval_requests
              (request_id, session_id, plan_id, requested_by, requested_at,
               required_scope, max_risk, destructive)
            VALUES ('approval-request-a', 'session-a', 'plan-a', 'operator',
                    'requested-at', 'plan:approve', 'low', false);
            INSERT INTO cpk_approval_decisions
              (decision_id, request_id, actor_id, decision, scope, decided_at)
            VALUES ('approval-decision-a', 'approval-request-a', 'manager',
                    'approved', 'plan:approve', 'approved-at');
            INSERT INTO cpk_execution_requests
              (request_id, workspace_id, session_id, plan_id, status,
               requested_by, requested_at, approval_request_id,
               approval_decision_id, idempotency_key, intent_fingerprint,
               claim_worker_id, claimed_at, lease_expires_at)
            VALUES ('request-a', 'workspace-a', 'session-a', 'plan-a', 'claimed',
                    'operator', 'execution-requested-at', 'approval-request-a',
                    'approval-decision-a', 'execute-a', 'fingerprint-a',
                    'worker-a', 'claimed-at', 'lease-at');
            INSERT INTO cpk_activity_runs
              (run_id, plan_id, request_id, attempt, status, created_at,
               started_at, settled_at, metadata)
            VALUES ('run-a', 'plan-a', 'request-a', 1, 'partially_failed',
                    'run-created-at', 'run-started-at', 'run-settled-at',
                    '{}'::jsonb);
            """
        )
        if include_events:
            self.connection.execute(
                """
                INSERT INTO cpk_activity_events
                  (event_id, run_id, ordinal, event_type, occurred_at, payload)
                VALUES
                  ('event-forward-failed', 'run-a', 1, 'step_failed',
                   'forward-failed-at',
                   '{"activity_id":"start-api","failure":{"code":"forward"},"recovery":null}'),
                  ('event-recovery', 'run-a', 2, 'recovery_decision_recorded',
                   'recovery-at',
                   '{"activity_id":null,"failure":null,"recovery":{"decision_id":"decision-a"}}'),
                  ('event-compensation-failed', 'run-a', 3,
                   'step_compensation_failed', 'compensation-failed-at',
                   '{"activity_id":"start-api","failure":{"code":"compensation"},"recovery":null}');
                """
            )


if __name__ == "__main__":
    unittest.main()
