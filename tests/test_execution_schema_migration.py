from __future__ import annotations

import os
import unittest
import uuid

import psycopg
from psycopg.errors import CheckViolation

from control_plane_kit.stores import install_schema


LEGACY_EXECUTION_SCHEMA = """
CREATE TABLE cpk_workspaces (
  workspace_id text PRIMARY KEY,
  name text NOT NULL,
  lifecycle text NOT NULL,
  current_graph_id text,
  desired_graph_id text,
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb
);
CREATE TABLE cpk_operation_sessions (
  session_id text PRIMARY KEY,
  workspace_id text NOT NULL,
  actor_id text NOT NULL,
  title text NOT NULL,
  status text NOT NULL,
  created_at text NOT NULL,
  closed_at text,
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb
);
CREATE TABLE cpk_activity_plans (
  plan_id text PRIMARY KEY,
  session_id text NOT NULL REFERENCES cpk_operation_sessions(session_id),
  base_graph_id text NOT NULL,
  desired_graph_id text NOT NULL,
  status text NOT NULL,
  created_at text NOT NULL,
  payload jsonb NOT NULL DEFAULT '{}'::jsonb
);
CREATE TABLE cpk_activity_runs (
  run_id text PRIMARY KEY,
  plan_id text NOT NULL REFERENCES cpk_activity_plans(plan_id),
  status text NOT NULL,
  started_at text NOT NULL,
  finished_at text,
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb
);
CREATE TABLE cpk_activity_events (
  event_id text PRIMARY KEY,
  run_id text NOT NULL REFERENCES cpk_activity_runs(run_id),
  ordinal integer NOT NULL,
  event_type text NOT NULL,
  occurred_at text NOT NULL,
  payload jsonb NOT NULL DEFAULT '{}'::jsonb,
  UNIQUE (run_id, ordinal)
);
CREATE TABLE cpk_observations (
  observation_id text PRIMARY KEY,
  workspace_id text NOT NULL,
  subject_id text NOT NULL,
  status text NOT NULL,
  observed_at text NOT NULL,
  payload jsonb NOT NULL DEFAULT '{}'::jsonb,
  stale boolean NOT NULL DEFAULT false
);
INSERT INTO cpk_workspaces
  (workspace_id, name, lifecycle)
VALUES ('workspace-a', 'Legacy workspace', 'active');
INSERT INTO cpk_operation_sessions
  (session_id, workspace_id, actor_id, title, status, created_at)
VALUES ('session-a', 'workspace-a', 'operator', 'Legacy session', 'closed',
        '2026-07-15T00:00:00Z');
INSERT INTO cpk_activity_plans
  (plan_id, session_id, base_graph_id, desired_graph_id, status, created_at)
VALUES ('plan-a', 'session-a', 'graph-a', 'graph-b', 'planned',
        '2026-07-15T00:01:00Z');
INSERT INTO cpk_activity_runs
  (run_id, plan_id, status, started_at, finished_at)
VALUES ('run-a', 'plan-a', 'running', '2026-07-15T00:02:00Z',
        '2026-07-15T00:02:30Z');
INSERT INTO cpk_activity_events
  (event_id, run_id, ordinal, event_type, occurred_at)
VALUES ('event-a', 'run-a', 1, 'legacy-step', '2026-07-15T00:03:00Z');
ALTER TABLE cpk_activity_events
  ADD CONSTRAINT cpk_activity_events_kind_check
  CHECK (event_type IN ('request_claimed', 'run_started')) NOT VALID;
INSERT INTO cpk_observations
  (observation_id, workspace_id, subject_id, status, observed_at)
VALUES ('observation-a', 'workspace-a', 'api', 'legacy-health',
        '2026-07-15T00:04:00Z');
"""


class ExecutionSchemaMigrationTests(unittest.TestCase):
    def test_install_forward_migrates_legacy_history_without_fabricating_admission(self):
        schema = f"execution_migration_{uuid.uuid4().hex}"
        database_url = os.environ["CPK_TEST_DATABASE_URL"]
        with psycopg.connect(database_url, autocommit=True) as connection:
            connection.execute(f'CREATE SCHEMA "{schema}"')
            try:
                connection.execute(f'SET search_path TO "{schema}"')
                connection.execute(LEGACY_EXECUTION_SCHEMA)

                install_schema(connection)
                install_schema(connection)

                migrated = connection.execute(
                    """
                    SELECT request_id, attempt, prior_run_id, created_at,
                           legacy_imported, settled_at, metadata
                    FROM cpk_activity_runs WHERE run_id = 'run-a'
                    """
                ).fetchone()
                self.assertEqual(
                    migrated[:6],
                    (None, 1, None, "2026-07-15T00:02:00Z", True, None),
                )
                self.assertEqual(
                    migrated[6]["migration"]["legacy_finished_at"],
                    "2026-07-15T00:02:30Z",
                )
                self.assertFalse(
                    connection.execute(
                        """
                        SELECT EXISTS (
                          SELECT 1 FROM information_schema.columns
                          WHERE table_schema = current_schema()
                            AND table_name = 'cpk_activity_runs'
                            AND column_name = 'finished_at'
                        )
                        """
                    ).fetchone()[0]
                )
                self.assertEqual(
                    connection.execute(
                        "SELECT count(*) FROM cpk_execution_requests"
                    ).fetchone()[0],
                    0,
                )
                self.assertEqual(
                    connection.execute(
                        "SELECT event_type FROM cpk_activity_events"
                    ).fetchone()[0],
                    "legacy-step",
                )
                self.assertEqual(
                    connection.execute(
                        """
                        SELECT status, graph_id, probe_kind, probe_outcome,
                               endpoint_context
                        FROM cpk_observations
                        """
                    ).fetchone(),
                    ("legacy-health", None, None, None, None),
                )
                constraint_names = {
                    row[0]
                    for row in connection.execute(
                        """
                        SELECT conname FROM pg_constraint
                        WHERE conname IN (
                          'cpk_execution_requests_workspace_session_fk',
                          'cpk_execution_requests_plan_session_fk',
                          'cpk_execution_requests_approval_identity_fk',
                          'cpk_activity_runs_request_plan_fk',
                          'cpk_observations_workspace_fk'
                        )
                        """
                    ).fetchall()
                }
                self.assertEqual(
                    constraint_names,
                    {
                        "cpk_execution_requests_workspace_session_fk",
                        "cpk_execution_requests_plan_session_fk",
                        "cpk_execution_requests_approval_identity_fk",
                        "cpk_activity_runs_request_plan_fk",
                        "cpk_observations_workspace_fk",
                    },
                )
            finally:
                connection.execute("SET search_path TO public")
                connection.execute(f'DROP SCHEMA "{schema}" CASCADE')

    def test_gate_a_timestamps_migrate_to_event_authoritative_settlement(self):
        schema = f"execution_settlement_{uuid.uuid4().hex}"
        database_url = os.environ["CPK_TEST_DATABASE_URL"]
        with psycopg.connect(database_url, autocommit=True) as connection:
            connection.execute(f'CREATE SCHEMA "{schema}"')
            try:
                connection.execute(f'SET search_path TO "{schema}"')
                self._seed_gate_a_schema(connection, include_failure_events=True)

                install_schema(connection)
                install_schema(connection)

                rows = connection.execute(
                    """
                    SELECT run_id, status, settled_at, metadata
                    FROM cpk_activity_runs ORDER BY attempt
                    """
                ).fetchall()
                self.assertEqual(
                    [(row[0], row[1], row[2]) for row in rows],
                    [
                        ("run-succeeded", "succeeded", "settled-success"),
                        ("run-failed", "failed", None),
                        ("run-compensating", "compensating", None),
                        ("run-compensated", "compensated", "settled-compensated"),
                    ],
                )
                self.assertEqual(
                    connection.execute(
                        """
                        SELECT run_id, event_type, occurred_at
                        FROM cpk_activity_events ORDER BY run_id
                        """
                    ).fetchall(),
                    [
                        ("run-compensating", "run_failed", "compensating-failed-at"),
                        ("run-failed", "run_failed", "failed-at"),
                    ],
                )
                with self.assertRaises(CheckViolation):
                    connection.execute(
                        """
                        INSERT INTO cpk_activity_runs
                          (run_id, plan_id, request_id, attempt, prior_run_id,
                           status, created_at, started_at, settled_at,
                           legacy_imported)
                        VALUES ('run-illegal-failed', 'plan-modern',
                                'request-modern', 5, 'run-compensated',
                                'failed', 'created-5', 'started-5',
                                'not-settled', false)
                        """
                    )
                with self.assertRaises(CheckViolation):
                    connection.execute(
                        """
                        INSERT INTO cpk_activity_runs
                          (run_id, plan_id, request_id, attempt, prior_run_id,
                           status, created_at, started_at, settled_at,
                           legacy_imported)
                        VALUES ('run-illegal-success', 'plan-modern',
                                'request-modern', 5, 'run-compensated',
                                'succeeded', 'created-5', 'started-5', NULL,
                                false)
                        """
                    )
            finally:
                connection.execute("SET search_path TO public")
                connection.execute(f'DROP SCHEMA "{schema}" CASCADE')

    def test_gate_a_migration_rejects_unproven_modern_failure_timestamp(self):
        schema = f"execution_settlement_reject_{uuid.uuid4().hex}"
        database_url = os.environ["CPK_TEST_DATABASE_URL"]
        with psycopg.connect(database_url, autocommit=True) as connection:
            connection.execute(f'CREATE SCHEMA "{schema}"')
            try:
                connection.execute(f'SET search_path TO "{schema}"')
                self._seed_gate_a_schema(connection, include_failure_events=False)

                with self.assertRaisesRegex(
                    psycopg.errors.RaiseException,
                    "without matching run_failed event",
                ):
                    install_schema(connection)
            finally:
                connection.execute("SET search_path TO public")
                connection.execute(f'DROP SCHEMA "{schema}" CASCADE')

    @staticmethod
    def _seed_gate_a_schema(
        connection: psycopg.Connection,
        *,
        include_failure_events: bool,
    ) -> None:
        install_schema(connection)
        connection.execute(
            """
            ALTER TABLE cpk_activity_runs
              DROP CONSTRAINT cpk_activity_runs_settlement_check,
              DROP CONSTRAINT cpk_activity_runs_started_check,
              DROP COLUMN settled_at,
              ADD COLUMN finished_at text;

            INSERT INTO cpk_workspaces (workspace_id, name, lifecycle)
            VALUES ('workspace-modern', 'Modern', 'active');
            INSERT INTO cpk_operation_sessions
              (session_id, workspace_id, actor_id, title, status, created_at)
            VALUES ('session-modern', 'workspace-modern', 'operator', 'Modern',
                    'open', 'created');
            INSERT INTO cpk_activity_plans
              (plan_id, session_id, base_graph_id, desired_graph_id, status,
               created_at, payload)
            VALUES ('plan-modern', 'session-modern', 'graph-a', 'graph-b',
                    'planned', 'created', '{}'::jsonb);
            INSERT INTO cpk_approval_requests
              (request_id, session_id, plan_id, requested_by, requested_at,
               required_scope, max_risk, destructive)
            VALUES ('approval-request-modern', 'session-modern', 'plan-modern',
                    'operator', 'requested', 'plan:approve', 'low', false);
            INSERT INTO cpk_approval_decisions
              (decision_id, request_id, actor_id, decision, scope, decided_at)
            VALUES ('approval-decision-modern', 'approval-request-modern',
                    'manager', 'approved', 'plan:approve', 'approved');
            INSERT INTO cpk_execution_requests
              (request_id, workspace_id, session_id, plan_id, status,
               requested_by, requested_at, approval_request_id,
               approval_decision_id, idempotency_key, intent_fingerprint,
               claim_worker_id, claimed_at, lease_expires_at)
            VALUES ('request-modern', 'workspace-modern', 'session-modern',
                    'plan-modern', 'claimed', 'operator', 'requested',
                    'approval-request-modern', 'approval-decision-modern',
                    'execute-modern', 'fingerprint-modern', 'worker', 'claimed',
                    '2099-01-01T00:00:00Z');

            INSERT INTO cpk_activity_runs
              (run_id, plan_id, request_id, attempt, prior_run_id, status,
               created_at, started_at, finished_at, metadata, legacy_imported)
            VALUES
              ('run-succeeded', 'plan-modern', 'request-modern', 1, NULL,
               'succeeded', 'created-1', 'started-1', 'settled-success', '{}', false),
              ('run-failed', 'plan-modern', 'request-modern', 2, 'run-succeeded',
               'failed', 'created-2', 'started-2', 'failed-at', '{}', false),
              ('run-compensating', 'plan-modern', 'request-modern', 3, 'run-failed',
               'compensating', 'created-3', 'started-3',
               'compensating-failed-at', '{}', false),
              ('run-compensated', 'plan-modern', 'request-modern', 4,
               'run-compensating', 'compensated', 'created-4', 'started-4',
               'settled-compensated', '{}', false);
            """
        )
        if include_failure_events:
            connection.execute(
                """
                INSERT INTO cpk_activity_events
                  (event_id, run_id, ordinal, event_type, occurred_at, payload)
                VALUES
                  ('event-failed', 'run-failed', 1, 'run_failed', 'failed-at', '{}'),
                  ('event-compensating-failed', 'run-compensating', 1,
                   'run_failed', 'compensating-failed-at', '{}');
                """
            )

    def test_new_execution_rows_are_constrained_after_forward_migration(self):
        schema = f"execution_constraints_{uuid.uuid4().hex}"
        database_url = os.environ["CPK_TEST_DATABASE_URL"]
        with psycopg.connect(database_url, autocommit=True) as connection:
            connection.execute(f'CREATE SCHEMA "{schema}"')
            try:
                connection.execute(f'SET search_path TO "{schema}"')
                connection.execute(LEGACY_EXECUTION_SCHEMA)
                install_schema(connection)

                with self.assertRaises(CheckViolation):
                    connection.execute(
                        """
                        INSERT INTO cpk_activity_runs
                          (run_id, plan_id, status, created_at, legacy_imported)
                        VALUES ('run-invalid', 'plan-a', 'invented',
                                '2026-07-16T00:00:00Z', true)
                        """
                    )
                connection.execute(
                    """
                    INSERT INTO cpk_activity_events
                      (event_id, run_id, ordinal, event_type, occurred_at)
                    VALUES ('event-opened', 'run-a', 2, 'run_opened',
                            '2026-07-16T00:00:00Z')
                    """
                )
                with self.assertRaises(CheckViolation):
                    connection.execute(
                        """
                        INSERT INTO cpk_activity_runs
                          (run_id, plan_id, status, created_at, attempt,
                           legacy_imported)
                        VALUES ('run-invalid-attempt', 'plan-a', 'failed',
                                '2026-07-16T00:00:00Z', 2, true)
                        """
                    )
                with self.assertRaises(CheckViolation):
                    connection.execute(
                        """
                        INSERT INTO cpk_activity_events
                          (event_id, run_id, ordinal, event_type, occurred_at)
                        VALUES ('event-invalid', 'run-a', 3, 'invented',
                                '2026-07-16T00:00:00Z')
                        """
                    )
                with self.assertRaises(CheckViolation):
                    connection.execute(
                        """
                        INSERT INTO cpk_observations
                          (observation_id, workspace_id, subject_id, status,
                           observed_at)
                        VALUES ('observation-invalid', 'workspace-a', 'api',
                                'invented', '2026-07-16T00:00:00Z')
                        """
                    )
            finally:
                connection.execute("SET search_path TO public")
                connection.execute(f'DROP SCHEMA "{schema}" CASCADE')


if __name__ == "__main__":
    unittest.main()
