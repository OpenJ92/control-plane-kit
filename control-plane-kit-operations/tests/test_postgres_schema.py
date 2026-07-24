from __future__ import annotations

import os
import unittest
import uuid

import psycopg
from psycopg.errors import CheckViolation
from psycopg.types.json import Jsonb

from control_plane_kit_operations.postgres import POSTGRES_SCHEMA, install_schema


class PostgresSchemaFoundationTests(unittest.TestCase):
    def setUp(self) -> None:
        database_url = os.environ.get("CPK_OPERATIONS_TEST_DATABASE_URL")
        if not database_url:
            raise RuntimeError(
                "CPK_OPERATIONS_TEST_DATABASE_URL is required. Run "
                "./control-plane-kit-operations/test.sh so Docker starts Postgres."
            )
        self.schema = f"operations_schema_{uuid.uuid4().hex}"
        self.connection = psycopg.connect(database_url, autocommit=True)
        self.connection.execute(f'CREATE SCHEMA "{self.schema}"')
        self.connection.execute(f'SET search_path TO "{self.schema}"')

    def tearDown(self) -> None:
        self.connection.execute("SET search_path TO public")
        self.connection.execute(f'DROP SCHEMA "{self.schema}" CASCADE')
        self.connection.close()

    def test_install_is_caller_transactional(self) -> None:
        self.connection.autocommit = False
        try:
            install_schema(self.connection)
            self.connection.rollback()
        finally:
            self.connection.autocommit = True

        self.assertEqual(self._table_names(), set())

    def test_repeated_install_preserves_rows_and_constraint_identities(self) -> None:
        install_schema(self.connection)
        self._seed_minimal_execution_truth()
        before = self._constraint_identities()

        install_schema(self.connection)

        self.assertEqual(self._constraint_identities(), before)
        self.assertEqual(
            self.connection.execute(
                """
                SELECT workspace_id, lifecycle
                FROM cpk_workspaces
                """
            ).fetchone(),
            ("workspace-a", "created"),
        )
        self.assertEqual(
            self.connection.execute(
                """
                SELECT event_type, payload->>'activity_id'
                FROM cpk_activity_events
                ORDER BY ordinal
                """
            ).fetchall(),
            [
                ("run_opened", None),
                ("step_started", "start-api"),
                ("recovery_decision_recorded", None),
            ],
        )

    def test_closed_values_and_event_shapes_fail_closed(self) -> None:
        install_schema(self.connection)
        self._seed_minimal_execution_truth(include_events=False)

        invalid_events = (
            ("unknown-event", "invented", {"activity_id": None, "recovery": None}),
            ("step-without-id", "step_started", {"recovery": None}),
            (
                "run-with-id",
                "run_started",
                {"activity_id": "start-api", "recovery": None},
            ),
            (
                "recovery-without-object",
                "recovery_decision_recorded",
                {"activity_id": None, "recovery": None},
            ),
            (
                "ordinary-with-recovery",
                "run_failed",
                {"activity_id": None, "recovery": {"decision_id": "decision-a"}},
            ),
        )
        for event_id, event_type, payload in invalid_events:
            with self.subTest(event_id=event_id, event_type=event_type):
                with self.assertRaises(CheckViolation):
                    self.connection.execute(
                        """
                        INSERT INTO cpk_activity_events
                          (event_id, run_id, ordinal, event_type, occurred_at, payload)
                        VALUES (%s, 'run-a', 20, %s, 'invalid-at', %s)
                        """,
                        (event_id, event_type, Jsonb(payload)),
                    )

        with self.assertRaises(CheckViolation):
            self.connection.execute(
                """
                INSERT INTO cpk_workspaces (workspace_id, name, lifecycle)
                VALUES ('workspace-b', 'Broken', 'invented')
                """
            )
        with self.assertRaises(CheckViolation):
            self.connection.execute(
                """
                UPDATE cpk_activity_runs
                SET status = 'invented'
                WHERE run_id = 'run-a'
                """
            )
        with self.assertRaises(CheckViolation):
            self.connection.execute(
                """
                INSERT INTO cpk_approval_requests
                  (request_id, session_id, plan_id, requested_by, requested_at,
                   required_scope, max_risk, destructive)
                VALUES ('bad-approval-scope', 'session-a', 'plan-a', 'operator',
                        'approval-request-at', 'plan:invented', 'low', false)
                """
            )
        with self.assertRaises(CheckViolation):
            self.connection.execute(
                """
                INSERT INTO cpk_approval_requests
                  (request_id, session_id, plan_id, requested_by, requested_at,
                   required_scope, max_risk, destructive)
                VALUES ('bad-approval-risk', 'session-a', 'plan-a', 'operator',
                        'approval-request-at', 'plan:approve', 'invented', false)
                """
            )
        with self.assertRaises(CheckViolation):
            self.connection.execute(
                """
                INSERT INTO cpk_approval_decisions
                  (decision_id, request_id, actor_id, decision, scope, decided_at)
                VALUES ('bad-decision-scope', 'approval-request-a', 'manager',
                        'approved', 'plan:invented', 'approval-at')
                """
            )
        with self.assertRaises(CheckViolation):
            self.connection.execute(
                """
                INSERT INTO cpk_operation_actions
                  (action_id, session_id, ordinal, action_type, actor_id,
                   created_at)
                VALUES ('bad-action-type', 'session-a', 1, 'invented',
                        'operator', 'action-at')
                """
            )
        self.connection.execute(
            """
            INSERT INTO cpk_operation_actions
              (action_id, session_id, ordinal, action_type, actor_id,
               created_at)
            VALUES ('admit-action', 'session-a', 1, 'admit-execution',
                    'operator', 'action-at')
            """
        )

    def test_schema_text_contains_no_unconditional_destructive_constraint_ddl(self) -> None:
        normalized = " ".join(POSTGRES_SCHEMA.lower().split())

        self.assertNotIn("drop table", normalized)
        self.assertNotIn("drop constraint", normalized)
        self.assertNotIn("truncate table", normalized)

    def _seed_minimal_execution_truth(self, *, include_events: bool = True) -> None:
        self.connection.execute(
            """
            INSERT INTO cpk_workspaces (workspace_id, name, lifecycle)
            VALUES ('workspace-a', 'Current workspace', 'created');
            INSERT INTO cpk_graph_versions
              (graph_id, workspace_id, version, graph_descriptor, created_by,
               created_at)
            VALUES ('graph-a', 'workspace-a', 1, '{}'::jsonb, 'operator',
                    'graph-at');
            INSERT INTO cpk_operation_sessions
              (session_id, workspace_id, actor_id, title, status, created_at)
            VALUES ('session-a', 'workspace-a', 'operator', 'Deploy', 'open',
                    'session-at');
            INSERT INTO cpk_activity_plans
              (plan_id, session_id, base_graph_id, desired_graph_id, status,
               created_at, payload)
            VALUES ('plan-a', 'session-a', 'graph-a', 'graph-a', 'planned',
                    'plan-at', '{}'::jsonb);
            INSERT INTO cpk_approval_requests
              (request_id, session_id, plan_id, requested_by, requested_at,
               required_scope, max_risk, destructive)
            VALUES ('approval-request-a', 'session-a', 'plan-a', 'operator',
                    'approval-request-at', 'plan:approve', 'low', false);
            INSERT INTO cpk_approval_decisions
              (decision_id, request_id, actor_id, decision, scope, decided_at)
            VALUES ('approval-decision-a', 'approval-request-a', 'manager',
                    'approved', 'plan:approve', 'approval-at');
            INSERT INTO cpk_execution_requests
              (request_id, workspace_id, session_id, plan_id, status,
               requested_by, requested_at, approval_request_id,
               approval_decision_id, idempotency_key, intent_fingerprint)
            VALUES ('request-a', 'workspace-a', 'session-a', 'plan-a', 'queued',
                    'operator', 'execution-at', 'approval-request-a',
                    'approval-decision-a', 'execute-a', 'fingerprint-a');
            INSERT INTO cpk_activity_runs
              (run_id, plan_id, request_id, attempt, status, created_at,
               metadata)
            VALUES ('run-a', 'plan-a', 'request-a', 1, 'claimed', 'run-at',
                    '{}'::jsonb);
            """
        )
        if include_events:
            self.connection.execute(
                """
                INSERT INTO cpk_activity_events
                  (event_id, run_id, ordinal, event_type, occurred_at, payload)
                VALUES
                  ('event-opened', 'run-a', 1, 'run_opened', 'opened-at',
                   '{"activity_id": null, "recovery": null}'::jsonb),
                  ('event-step-started', 'run-a', 2, 'step_started',
                   'step-at',
                   '{"activity_id": "start-api", "recovery": null}'::jsonb),
                  ('event-recovery', 'run-a', 3, 'recovery_decision_recorded',
                   'recovery-at',
                   '{"activity_id": null, "recovery": {"decision_id": "decision-a"}}'::jsonb);
                """
            )

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

    def _table_names(self) -> set[str]:
        return {
            row[0]
            for row in self.connection.execute(
                """
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema = current_schema()
                """
            ).fetchall()
        }


if __name__ == "__main__":
    unittest.main()
