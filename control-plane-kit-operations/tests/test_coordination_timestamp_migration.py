from __future__ import annotations

from datetime import datetime, timedelta, timezone, tzinfo
import importlib
import os
import unittest
import uuid

import psycopg

import control_plane_kit_operations.postgres as postgres
from control_plane_kit_operations.postgres.activity_history import (
    PostgresActivityHistoryStore,
)
from control_plane_kit_operations.postgres.execution import PostgresExecutionStore
from control_plane_kit_operations.postgres.observed_state import (
    PostgresObservedStateStore,
)
from control_plane_kit_operations.records import (
    ExecutionIdempotency,
    ExecutionRequestIdentity,
    ExecutionRequestRecord,
    ObservationFreshness,
    ObservationRecord,
    ObservationStatus,
    OperationSessionRecord,
    OperationSessionStatus,
)
from control_plane_kit_core.operations.lifecycle import (
    ExecutionRequestStatus,
)


_TEMPORAL_COLUMNS = (
    ("cpk_activity_events", "occurred_at", "NO", 6),
    ("cpk_activity_plans", "created_at", "NO", 6),
    ("cpk_activity_runs", "created_at", "NO", 6),
    ("cpk_activity_runs", "settled_at", "YES", 6),
    ("cpk_activity_runs", "started_at", "YES", 6),
    ("cpk_approval_decisions", "decided_at", "NO", 6),
    ("cpk_approval_requests", "requested_at", "NO", 6),
    ("cpk_execution_requests", "claimed_at", "YES", 6),
    ("cpk_execution_requests", "lease_expires_at", "YES", 6),
    ("cpk_execution_requests", "requested_at", "NO", 6),
    ("cpk_observations", "observed_at", "NO", 6),
    ("cpk_operation_actions", "created_at", "NO", 6),
    ("cpk_operation_sessions", "closed_at", "YES", 6),
    ("cpk_operation_sessions", "created_at", "NO", 6),
)


class PostgresTimestampCodecTests(unittest.TestCase):
    def test_encode_accepts_only_canonical_utc_seconds_and_microseconds(self) -> None:
        temporal = self._temporal()

        seconds = temporal.encode_postgres_timestamp("2026-08-07T06:00:00Z")
        micros = temporal.encode_postgres_timestamp(
            "2026-08-07T06:00:00.000001Z"
        )

        self.assertEqual(seconds, datetime(2026, 8, 7, 6, tzinfo=timezone.utc))
        self.assertEqual(
            micros,
            datetime(2026, 8, 7, 6, 0, 0, 1, tzinfo=timezone.utc),
        )

    def test_encode_rejects_noncanonical_material_without_echo_or_cause(self) -> None:
        temporal = self._temporal()
        marker = "private-timestamp-material"
        invalid = (
            None,
            datetime(2026, 8, 7, 6),
            "2026-08-07T06:00:00+00:00",
            "2026-08-07T01:00:00-05:00",
            "2026-08-07T06:00:00.000000Z",
            "2026-02-30T06:00:00Z",
            marker,
            marker + ("x" * 4096),
            "\ud800",
        )

        for value in invalid:
            with self.subTest(value_type=type(value).__name__):
                with self.assertRaises(ValueError) as raised:
                    temporal.encode_postgres_timestamp(value)
                self.assertNotIn(marker, str(raised.exception))
                self.assertIsNone(raised.exception.__context__)

    def test_decode_normalizes_aware_database_values_to_canonical_utc(self) -> None:
        temporal = self._temporal()
        eastern = timezone(timedelta(hours=-5))

        class BrokenTimezone(tzinfo):
            def utcoffset(self, _value):
                raise RuntimeError("private-timestamp-material")

        self.assertEqual(
            temporal.decode_postgres_timestamp(
                datetime(2026, 8, 7, 1, tzinfo=eastern)
            ),
            "2026-08-07T06:00:00Z",
        )
        self.assertEqual(
            temporal.decode_postgres_timestamp(
                datetime(2026, 8, 7, 1, 0, 0, 1, tzinfo=eastern)
            ),
            "2026-08-07T06:00:00.000001Z",
        )
        invalid = (
            None,
            "2026-08-07T06:00:00Z",
            datetime(2026, 8, 7, 6),
            datetime(2026, 8, 7, 6, tzinfo=BrokenTimezone()),
        )
        for value in invalid:
            with self.subTest(value_type=type(value).__name__):
                with self.assertRaises(ValueError) as raised:
                    temporal.decode_postgres_timestamp(value)
                self.assertNotIn("private-timestamp-material", str(raised.exception))
                self.assertIsNone(raised.exception.__context__)

    def _temporal(self):
        try:
            return importlib.import_module(
                "control_plane_kit_operations.postgres.temporal"
            )
        except ModuleNotFoundError:
            self.fail("postgres temporal codec module is not implemented")


class CoordinationTimestampMigrationTests(unittest.TestCase):
    def setUp(self) -> None:
        database_url = os.environ.get("CPK_OPERATIONS_TEST_DATABASE_URL")
        if not database_url:
            raise RuntimeError(
                "CPK_OPERATIONS_TEST_DATABASE_URL is required; use Docker-first tests"
            )
        self.schema = f"coordination_time_{uuid.uuid4().hex}"
        self.connection = psycopg.connect(database_url, autocommit=True)
        self.connection.execute(f'CREATE SCHEMA "{self.schema}"')
        self.connection.execute(f'SET search_path TO "{self.schema}"')

    def tearDown(self) -> None:
        self.connection.execute("SET search_path TO public")
        self.connection.execute(f'DROP SCHEMA IF EXISTS "{self.schema}" CASCADE')
        self.connection.close()

    def test_registry_preserves_exact_coordination_timestamp_v2_prefix(self) -> None:
        registry = postgres.POSTGRES_SCHEMA_MIGRATIONS

        self.assertEqual(
            tuple(
                (migration.version, migration.name)
                for migration in registry.migrations[:2]
            ),
            ((1, "operations-baseline"), (2, "coordination-timestamps")),
        )
        self.assertEqual(postgres.POSTGRES_SCHEMA_V1_SHA256, registry.migrations[0].checksum_sha256)

    def test_fresh_install_preserves_v2_temporal_contract(self) -> None:
        postgres.install_postgres_schema(self.connection)

        self.assertEqual(
            self._ledger(),
            [
                (1, "operations-baseline"),
                (2, "coordination-timestamps"),
                (3, "graph-product-authority-timestamps"),
                (4, "secret-registration-timestamps"),
            ],
        )
        self.assertEqual(self._temporal_contract(), _TEMPORAL_COLUMNS)
        self.assertIs(
            postgres.verify_postgres_schema(self.connection).kind,
            postgres.ObservedSchemaKind.VERSIONED,
        )

    def test_retained_v1_canonical_values_migrate_without_identity_loss(self) -> None:
        self.connection.execute(postgres.POSTGRES_SCHEMA)
        self.connection.execute("SET TIME ZONE 'America/New_York'")
        self.connection.execute(
            """
            INSERT INTO cpk_workspaces (workspace_id, name, lifecycle)
            VALUES ('workspace-a', 'Workspace A', 'created');
            INSERT INTO cpk_operation_sessions
              (session_id, workspace_id, actor_id, title, status, created_at,
               closed_at)
            VALUES ('session-a', 'workspace-a', 'operator-a', 'Done', 'closed',
                    '2026-08-07T06:00:00.000001Z',
                    '2026-08-07T06:01:00Z');
            """
        )

        postgres.install_postgres_schema(self.connection)

        self.assertEqual(
            self._ledger(),
            [
                (1, "operations-baseline"),
                (2, "coordination-timestamps"),
                (3, "graph-product-authority-timestamps"),
                (4, "secret-registration-timestamps"),
            ],
        )
        self.assertEqual(
            self.connection.execute(
                """
                SELECT session_id, created_at, closed_at
                FROM cpk_operation_sessions
                """
            ).fetchone(),
            (
                "session-a",
                datetime(2026, 8, 7, 6, 0, 0, 1, tzinfo=timezone.utc),
                datetime(2026, 8, 7, 6, 1, tzinfo=timezone.utc),
            ),
        )

    def test_retained_noncanonical_value_rolls_back_ledger_and_ddl(self) -> None:
        self.connection.execute(postgres.POSTGRES_SCHEMA)
        self.connection.execute(
            """
            INSERT INTO cpk_workspaces (workspace_id, name, lifecycle)
            VALUES ('workspace-a', 'Workspace A', 'created');
            INSERT INTO cpk_operation_sessions
              (session_id, workspace_id, actor_id, title, status, created_at)
            VALUES ('session-a', 'workspace-a', 'operator-a', 'Open', 'open',
                    'not-a-timestamp');
            """
        )

        with self.assertRaises(postgres.SchemaMigrationError) as raised:
            postgres.install_postgres_schema(self.connection)

        self.assertLessEqual(len(str(raised.exception)), 256)
        self.assertNotIn("not-a-timestamp", str(raised.exception))
        self.assertNotIn("cpk://", str(raised.exception))
        self.assertNotIn("cpk_schema_migrations", self._table_names())
        self.assertEqual(
            self.connection.execute(
                "SELECT created_at FROM cpk_operation_sessions"
            ).fetchone(),
            ("not-a-timestamp",),
        )
        self.assertEqual(
            self.connection.execute(
                """
                SELECT data_type
                FROM information_schema.columns
                WHERE table_schema = current_schema()
                  AND table_name = 'cpk_operation_sessions'
                  AND column_name = 'created_at'
                """
            ).fetchone(),
            ("text",),
        )

    def test_retained_calendar_invalid_value_has_bounded_category(self) -> None:
        self.connection.execute(postgres.POSTGRES_SCHEMA)
        self.connection.execute(
            """
            INSERT INTO cpk_workspaces (workspace_id, name, lifecycle)
            VALUES ('workspace-a', 'Workspace A', 'created');
            INSERT INTO cpk_operation_sessions
              (session_id, workspace_id, actor_id, title, status, created_at)
            VALUES ('session-a', 'workspace-a', 'operator-a', 'Open', 'open',
                    '2026-02-30T06:00:00Z');
            """
        )

        with self.assertRaises(postgres.SchemaMigrationError) as raised:
            postgres.install_postgres_schema(self.connection)

        self.assertEqual(
            str(raised.exception),
            "coordination timestamps are not canonical UTC",
        )
        self.assertIsNone(raised.exception.__context__)
        self.assertNotIn("cpk_schema_migrations", self._table_names())
        self.assertEqual(
            self.connection.execute(
                "SELECT created_at FROM cpk_operation_sessions"
            ).fetchone(),
            ("2026-02-30T06:00:00Z",),
        )

    def test_current_verifier_rejects_owned_temporal_type_drift(self) -> None:
        postgres.install_postgres_schema(self.connection)
        self.connection.execute(
            """
            ALTER TABLE cpk_observations
              ALTER COLUMN observed_at TYPE text USING observed_at::text
            """
        )

        with self.assertRaises(postgres.SchemaMigrationError):
            postgres.verify_postgres_schema(self.connection)

    def test_current_verifier_rejects_owned_temporal_precision_drift(self) -> None:
        postgres.install_postgres_schema(self.connection)
        self.connection.execute(
            """
            ALTER TABLE cpk_observations
              ALTER COLUMN observed_at TYPE timestamptz(5)
                USING observed_at::timestamptz(5)
            """
        )

        with self.assertRaises(postgres.SchemaMigrationError):
            postgres.verify_postgres_schema(self.connection)

    def test_owned_stores_round_trip_strings_microseconds_and_nulls(self) -> None:
        postgres.install_postgres_schema(self.connection)
        self.connection.execute("SET TIME ZONE 'Asia/Tokyo'")
        self.connection.execute(
            """
            INSERT INTO cpk_workspaces (workspace_id, name, lifecycle)
            VALUES ('workspace-a', 'Workspace A', 'created')
            """
        )
        history = PostgresActivityHistoryStore(self.connection)
        session = OperationSessionRecord(
            session_id="session-a",
            workspace_id="workspace-a",
            actor_id="operator-a",
            title="Deploy",
            status=OperationSessionStatus.OPEN,
            created_at="2026-08-07T06:00:00.000001Z",
        )
        history.add_session(session)
        self.assertEqual(history.get_session("session-a"), session)

        observation = ObservationRecord(
            observation_id="observation-a",
            workspace_id="workspace-a",
            subject_id="runtime-a",
            status=ObservationStatus.HEALTHY,
            observed_at="2026-08-07T06:00:01Z",
            freshness=ObservationFreshness.FRESH,
        )
        observed = PostgresObservedStateStore(self.connection)
        observed.put(observation)
        self.assertEqual(observed.latest("workspace-a", "runtime-a"), observation)

    def test_execution_request_claim_round_trips_database_native_lease(self) -> None:
        postgres.install_postgres_schema(self.connection)
        self._seed_execution_prerequisites()
        store = PostgresExecutionStore(self.connection)
        request = ExecutionRequestRecord(
            identity=ExecutionRequestIdentity(
                "request-a", "workspace-a", "session-a", "plan-a"
            ),
            status=ExecutionRequestStatus.QUEUED,
            requested_by="operator-a",
            requested_at="2026-08-07T06:00:03Z",
            approval_request_id="approval-request-a",
            approval_decision_id="approval-decision-a",
            idempotency=ExecutionIdempotency("execute-a", "fingerprint-a"),
        )

        store.add_request(request)
        claimed = store.claim_request(
            "request-a",
            "worker-a",
            "2026-08-07T06:00:04.000001Z",
            "2026-08-07T06:01:04Z",
        )

        self.assertIsNotNone(claimed)
        self.assertEqual(claimed.requested_at, "2026-08-07T06:00:03Z")
        self.assertEqual(claimed.claim.claimed_at, "2026-08-07T06:00:04.000001Z")
        self.assertEqual(claimed.claim.lease_expires_at, "2026-08-07T06:01:04Z")

    def _seed_execution_prerequisites(self) -> None:
        self.connection.execute(
            """
            INSERT INTO cpk_workspaces (workspace_id, name, lifecycle)
            VALUES ('workspace-a', 'Workspace A', 'created');
            INSERT INTO cpk_operation_sessions
              (session_id, workspace_id, actor_id, title, status, created_at)
            VALUES ('session-a', 'workspace-a', 'operator-a', 'Deploy', 'open',
                    '2026-08-07T06:00:00Z');
            INSERT INTO cpk_activity_plans
              (plan_id, session_id, base_graph_id, desired_graph_id, status,
               created_at, payload)
            VALUES ('plan-a', 'session-a', 'graph-a', 'graph-b', 'planned',
                    '2026-08-07T06:00:01Z', '{}'::jsonb);
            INSERT INTO cpk_approval_requests
              (request_id, session_id, plan_id, subject_kind, subject_payload,
               review_digest, requested_by, requested_at, required_scope,
               max_risk, destructive)
            VALUES ('approval-request-a', 'session-a', 'plan-a', 'activity-plan',
                    '{"kind":"activity-plan","plan_id":"plan-a"}'::jsonb,
                    encode(sha256(convert_to('activity-plan:plan-a', 'UTF8')), 'hex'),
                    'operator-a', '2026-08-07T06:00:02Z', 'plan:approve',
                    'low', false);
            INSERT INTO cpk_approval_decisions
              (decision_id, request_id, actor_id, decision, scope, decided_at)
            VALUES ('approval-decision-a', 'approval-request-a', 'manager-a',
                    'approved', 'plan:approve', '2026-08-07T06:00:02Z');
            """
        )

    def _ledger(self) -> list[tuple[int, str]]:
        return self.connection.execute(
            "SELECT version, name FROM cpk_schema_migrations ORDER BY version"
        ).fetchall()

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

    def _temporal_contract(self) -> tuple[tuple[str, str, str, int], ...]:
        return tuple(
            self.connection.execute(
                """
                SELECT table_name, column_name, is_nullable, datetime_precision
                FROM information_schema.columns
                WHERE table_schema = current_schema()
                  AND data_type = 'timestamp with time zone'
                  AND (table_name, column_name) IN (
                    ('cpk_activity_events', 'occurred_at'),
                    ('cpk_activity_plans', 'created_at'),
                    ('cpk_activity_runs', 'created_at'),
                    ('cpk_activity_runs', 'settled_at'),
                    ('cpk_activity_runs', 'started_at'),
                    ('cpk_approval_decisions', 'decided_at'),
                    ('cpk_approval_requests', 'requested_at'),
                    ('cpk_execution_requests', 'claimed_at'),
                    ('cpk_execution_requests', 'lease_expires_at'),
                    ('cpk_execution_requests', 'requested_at'),
                    ('cpk_observations', 'observed_at'),
                    ('cpk_operation_actions', 'created_at'),
                    ('cpk_operation_sessions', 'closed_at'),
                    ('cpk_operation_sessions', 'created_at')
                  )
                ORDER BY table_name, column_name
                """
            ).fetchall()
        )


if __name__ == "__main__":
    unittest.main()
