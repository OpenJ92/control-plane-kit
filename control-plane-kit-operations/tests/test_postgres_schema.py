from __future__ import annotations

import os
import unittest
import uuid

import psycopg
from psycopg.errors import CheckViolation
from psycopg.types.json import Jsonb

from control_plane_kit_core.delegation_keys import DelegationKeyPurpose
from control_plane_kit_core.topology import DEFAULT_GRAPH_CODEC, DeploymentGraph
from control_plane_kit_operations.gateway_key_rotations import (
    GatewayKeyRotationStatus,
)
from control_plane_kit_operations.postgres import (
    SchemaInstallationError,
    install_schema,
)
from control_plane_kit_operations.records import (
    GraphVersionRecord,
    RealizedGraphProjectionRecord,
)


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
            self.connection.execute("SELECT 1")
            install_schema(self.connection)
            self.connection.rollback()
        finally:
            self.connection.autocommit = True

        self.assertEqual(self._table_names(), set())

    def test_incompatible_schema_install_rolls_back_partial_ddl(self) -> None:
        self.connection.execute(
            "CREATE TABLE cpk_activity_runs (run_id text PRIMARY KEY)"
        )
        self.connection.autocommit = False
        try:
            with self.assertRaises(SchemaInstallationError):
                install_schema(self.connection)
            self.connection.rollback()
        finally:
            if self.connection.info.transaction_status != psycopg.pq.TransactionStatus.IDLE:
                self.connection.rollback()
            self.connection.autocommit = True

        self.assertEqual(self._table_names(), {"cpk_activity_runs"})

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

    def test_install_rejects_approval_subject_drift(self) -> None:
        install_schema(self.connection)
        self._seed_minimal_execution_truth()
        self.connection.execute(
            "ALTER TABLE cpk_approval_requests DROP COLUMN rotation_id CASCADE"
        )
        self.connection.execute(
            "ALTER TABLE cpk_approval_requests DROP COLUMN subject_kind CASCADE"
        )
        self.connection.execute(
            "ALTER TABLE cpk_approval_requests DROP COLUMN subject_payload CASCADE"
        )
        self.connection.execute(
            "ALTER TABLE cpk_approval_requests DROP COLUMN review_digest CASCADE"
        )
        self.connection.execute(
            "ALTER TABLE cpk_approval_requests ALTER COLUMN plan_id SET NOT NULL"
        )

        with self.assertRaises(SchemaInstallationError):
            install_schema(self.connection)

        self.assertEqual(
            self.connection.execute(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema = current_schema()
                  AND table_name = 'cpk_approval_requests'
                  AND column_name IN (
                    'rotation_id', 'subject_kind', 'subject_payload',
                    'review_digest'
                  )
                """
            ).fetchall(),
            [],
        )

    def test_install_rejects_rotation_status_drift_without_row_loss_or_repair(
        self,
    ) -> None:
        install_schema(self.connection)
        self.connection.execute(
            """
            INSERT INTO cpk_workspaces (workspace_id, name, lifecycle)
            VALUES ('workspace-a', 'Workspace A', 'created')
            """
        )
        self.connection.execute(
            """
            INSERT INTO cpk_gateway_key_rotations (
              rotation_id, workspace_id, gateway_node_id, purpose, issuer,
              old_key_id, new_secret_reference, key_generation_correlation,
              maximum_grant_lifetime_seconds, clock_skew_seconds,
              correlation_id, requested_by, requested_at, intent_fingerprint,
              status, version
            ) VALUES (
              'rotation-a', 'workspace-a', 'gateway-a', %s, 'cpk-server',
              'gateway-key-a',
              'secret://workspace-secrets/keys/gateway-key-b',
              'generate-gateway-key-b', 120, 10, 'rotation-a', 'operator-a',
              '2026-08-02T00:00:00Z', %s, 'approved', 1
            )
            """,
            (DelegationKeyPurpose.GATEWAY_PROBE.value, "a" * 64),
        )
        old_values = ", ".join(
            f"'{status.value}'"
            for status in GatewayKeyRotationStatus
            if status is not GatewayKeyRotationStatus.GENERATION_PREPARED
        )
        for table, column, constraint in (
            (
                "cpk_gateway_key_rotations",
                "status",
                "cpk_gateway_key_rotations_status_check",
            ),
            (
                "cpk_gateway_key_rotation_transitions",
                "from_status",
                "cpk_gateway_key_rotation_transitions_from_status_check",
            ),
            (
                "cpk_gateway_key_rotation_transitions",
                "to_status",
                "cpk_gateway_key_rotation_transitions_to_status_check",
            ),
        ):
            self.connection.execute(
                f"ALTER TABLE {table} DROP CONSTRAINT {constraint}"
            )
            self.connection.execute(
                f"ALTER TABLE {table} ADD CONSTRAINT {constraint} "
                f"CHECK ({column} IN ({old_values}))"
            )

        before_rejection = self._constraint_identities()
        for _ in range(2):
            with self.assertRaises(SchemaInstallationError) as raised:
                install_schema(self.connection)

            self.assertEqual(
                str(raised.exception),
                "operations schema reset is required",
            )
            self.assertIsNone(raised.exception.__context__)
            self.assertIsNone(raised.exception.__cause__)
            self.assertEqual(self._constraint_identities(), before_rejection)
            self.assertEqual(
                self.connection.execute(
                    "SELECT rotation_id, status, version "
                    "FROM cpk_gateway_key_rotations"
                ).fetchall(),
                [("rotation-a", GatewayKeyRotationStatus.APPROVED.value, 1)],
            )
            self.assertEqual(
                self.connection.execute(
                    "SELECT count(*) FROM cpk_gateway_key_rotation_transitions"
                ).fetchone(),
                (0,),
            )

    def test_cloudflare_owned_ingress_resources_are_epoch_history_records(
        self,
    ) -> None:
        install_schema(self.connection)

        columns = {
            row[0]: (row[1], row[2])
            for row in self.connection.execute(
                """
                SELECT column_name, data_type, is_nullable
                FROM information_schema.columns
                WHERE table_schema = %s
                  AND table_name = 'cpk_cloudflare_ingress_resources'
                """,
                (self.schema,),
            ).fetchall()
        }
        self.assertEqual(columns["epoch"], ("integer", "NO"))
        self.assertEqual(columns["status"], ("text", "NO"))
        self.assertEqual(
            columns["removed_at"],
            ("timestamp with time zone", "YES"),
        )
        self.assertEqual(columns["removed_by_run_id"], ("text", "YES"))

        primary_key_columns = self.connection.execute(
            """
            SELECT a.attname
            FROM pg_index i
            JOIN pg_attribute a
              ON a.attrelid = i.indrelid
             AND a.attnum = ANY(i.indkey)
            WHERE i.indrelid = 'cpk_cloudflare_ingress_resources'::regclass
              AND i.indisprimary
            ORDER BY array_position(i.indkey, a.attnum)
            """
        ).fetchall()
        self.assertEqual(
            [column[0] for column in primary_key_columns],
            ["workspace_id", "ingress_id", "epoch"],
        )

        active_index = self.connection.execute(
            """
            SELECT pg_get_expr(indpred, indrelid)
            FROM pg_index
            WHERE indexrelid = 'cpk_cloudflare_ingress_resources_active_key'::regclass
            """
        ).fetchone()
        self.assertIn("'active'::text", active_index[0])
        self.assertIn("'allocating'::text", active_index[0])
        self.assertIn("'removing'::text", active_index[0])

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
                        VALUES (%s, 'run-a', 20, %s,
                                '2026-08-07T06:30:00Z', %s)
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
                  (request_id, session_id, plan_id, subject_kind, subject_payload,
                   review_digest, requested_by, requested_at,
                   required_scope, max_risk, destructive)
                VALUES ('bad-approval-scope', 'session-a', 'plan-a', 'activity-plan',
                        '{"kind":"activity-plan","plan_id":"plan-a"}'::jsonb,
                        encode(sha256(convert_to('activity-plan:plan-a', 'UTF8')), 'hex'),
                        'operator',
                        '2026-08-07T06:31:00Z', 'plan:invented', 'low', false)
                """
            )
        with self.assertRaises(CheckViolation):
            self.connection.execute(
                """
                INSERT INTO cpk_approval_requests
                  (request_id, session_id, plan_id, subject_kind, subject_payload,
                   review_digest, requested_by, requested_at,
                   required_scope, max_risk, destructive)
                VALUES ('bad-approval-risk', 'session-a', 'plan-a', 'activity-plan',
                        '{"kind":"activity-plan","plan_id":"plan-a"}'::jsonb,
                        encode(sha256(convert_to('activity-plan:plan-a', 'UTF8')), 'hex'),
                        'operator',
                        '2026-08-07T06:31:00Z', 'plan:approve', 'invented', false)
                """
            )
        with self.assertRaises(CheckViolation):
            self.connection.execute(
                """
                INSERT INTO cpk_approval_decisions
                  (decision_id, request_id, actor_id, decision, scope, decided_at)
                VALUES ('bad-decision-scope', 'approval-request-a', 'manager',
                        'approved', 'plan:invented', '2026-08-07T06:32:00Z')
                """
            )
        with self.assertRaises(CheckViolation):
            self.connection.execute(
                """
                INSERT INTO cpk_operation_actions
                  (action_id, session_id, ordinal, action_type, actor_id,
                   created_at)
                VALUES ('bad-action-type', 'session-a', 1, 'invented',
                        'operator', '2026-08-07T06:33:00Z')
                """
            )
        self.connection.execute(
            """
            INSERT INTO cpk_operation_actions
              (action_id, session_id, ordinal, action_type, actor_id,
               created_at)
            VALUES ('admit-action', 'session-a', 1, 'admit-execution',
                    'operator', '2026-08-07T06:33:00Z')
            """
        )

    def _seed_minimal_execution_truth(self, *, include_events: bool = True) -> None:
        self.connection.execute(
            """
            INSERT INTO cpk_workspaces (workspace_id, name, lifecycle)
            VALUES ('workspace-a', 'Current workspace', 'created');
            """
        )
        self.connection.execute(
            """
            INSERT INTO cpk_graph_versions
              (graph_id, workspace_id, version, graph_descriptor, created_by,
               created_at)
            VALUES ('graph-a', 'workspace-a', 1, %s, 'operator',
                    '2026-08-07T05:59:00Z');
            """,
            (Jsonb(DEFAULT_GRAPH_CODEC.encode(DeploymentGraph("current"))),),
        )
        projection = RealizedGraphProjectionRecord.identity_for_authored(
            authored_record=GraphVersionRecord(
                graph_id="graph-a",
                workspace_id="workspace-a",
                version=1,
                graph_descriptor=DEFAULT_GRAPH_CODEC.encode(
                    DeploymentGraph("current")
                ),
                created_by="operator",
                created_at="2026-08-07T05:59:00Z",
            )
        )
        self.connection.execute(
            """
            INSERT INTO cpk_realized_graph_projections
              (projection_id, workspace_id, source_authored_graph_id,
               projection_kind, projection_key, projection_digest,
               graph_descriptor, created_by, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                projection.projection_id,
                projection.workspace_id,
                projection.source_authored_graph_id,
                projection.projection_kind.value,
                projection.projection_key,
                projection.projection_digest,
                Jsonb(projection.graph_descriptor),
                projection.created_by,
                projection.created_at,
            ),
        )
        self.connection.execute(
            "UPDATE cpk_workspaces SET current_graph_id='graph-a', "
            "desired_graph_id='graph-a', current_realized_projection_id=%s, "
            "desired_realized_projection_id=%s, desired_graph_revision=1 "
            "WHERE workspace_id='workspace-a'",
            (projection.projection_id, projection.projection_id),
        )
        self.connection.execute(
            """
            INSERT INTO cpk_operation_sessions
              (session_id, workspace_id, actor_id, title, status, created_at)
            VALUES ('session-a', 'workspace-a', 'operator', 'Deploy', 'open',
                    '2026-08-07T06:00:00Z');
            """
        )
        self.connection.execute(
            """
            INSERT INTO cpk_activity_plans
              (plan_id, session_id, base_graph_id, desired_graph_id,
               base_realized_projection_id, desired_realized_projection_id,
               desired_graph_revision, status, created_at, payload)
            VALUES ('plan-a', 'session-a', 'graph-a', 'graph-a', %s, %s, 1,
                    'planned', '2026-08-07T06:01:00Z', '{}'::jsonb)
            """,
            (projection.projection_id, projection.projection_id),
        )
        self.connection.execute(
            """
            INSERT INTO cpk_approval_requests
              (request_id, session_id, plan_id, subject_kind, subject_payload,
               review_digest, requested_by, requested_at,
               required_scope, max_risk, destructive)
            VALUES ('approval-request-a', 'session-a', 'plan-a', 'activity-plan',
                    '{"kind":"activity-plan","plan_id":"plan-a"}'::jsonb,
                    encode(sha256(convert_to('activity-plan:plan-a', 'UTF8')), 'hex'),
                    'operator',
                    '2026-08-07T06:02:00Z', 'plan:approve', 'low', false);
            INSERT INTO cpk_approval_decisions
              (decision_id, request_id, actor_id, decision, scope, decided_at)
            VALUES ('approval-decision-a', 'approval-request-a', 'manager',
                    'approved', 'plan:approve', '2026-08-07T06:03:00Z');
            INSERT INTO cpk_execution_requests
              (request_id, workspace_id, session_id, plan_id, status,
               requested_by, requested_at, approval_request_id,
               approval_decision_id, idempotency_key, intent_fingerprint)
            VALUES ('request-a', 'workspace-a', 'session-a', 'plan-a', 'queued',
                    'operator', '2026-08-07T06:04:00Z', 'approval-request-a',
                    'approval-decision-a', 'execute-a', 'fingerprint-a');
            INSERT INTO cpk_activity_runs
              (run_id, plan_id, request_id, attempt, status, created_at,
               metadata)
            VALUES ('run-a', 'plan-a', 'request-a', 1, 'claimed',
                    '2026-08-07T06:05:00Z',
                    '{}'::jsonb);
            """,
        )
        if include_events:
            self.connection.execute(
                """
                INSERT INTO cpk_activity_events
                  (event_id, run_id, ordinal, event_type, occurred_at, payload)
                VALUES
                  ('event-opened', 'run-a', 1, 'run_opened',
                   '2026-08-07T06:06:00Z',
                   '{"activity_id": null, "recovery": null}'::jsonb),
                  ('event-step-started', 'run-a', 2, 'step_started',
                   '2026-08-07T06:07:00Z',
                   '{"activity_id": "start-api", "recovery": null}'::jsonb),
                  ('event-recovery', 'run-a', 3, 'recovery_decision_recorded',
                   '2026-08-07T06:08:00Z',
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
