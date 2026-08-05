from __future__ import annotations

import os
import unittest
import uuid

import psycopg
from psycopg.errors import CheckViolation, ForeignKeyViolation, UndefinedColumn
from psycopg.types.json import Jsonb

from control_plane_kit_core.delegation_keys import DelegationKeyPurpose
from control_plane_kit_core.operations import LifecycleOperationKind, OperatorCommandKind
from control_plane_kit_core.topology import DEFAULT_GRAPH_CODEC, DeploymentGraph
from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_operations.gateway_key_rotations import (
    GatewayKeyRotationStatus,
)
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

    def test_install_backfills_legacy_plan_approval_as_closed_subject(self) -> None:
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

        install_schema(self.connection)

        row = self.connection.execute(
            """
            SELECT plan_id, rotation_id, subject_kind, subject_payload,
                   review_digest
            FROM cpk_approval_requests
            WHERE request_id = 'approval-request-a'
            """
        ).fetchone()
        self.assertEqual(row[0:3], ("plan-a", None, "activity-plan"))
        self.assertEqual(
            row[3],
            {"kind": "activity-plan", "plan_id": "plan-a"},
        )
        self.assertEqual(len(row[4]), 64)

    def test_install_expands_stale_approval_scope_checks_once(self) -> None:
        install_schema(self.connection)
        self._seed_minimal_execution_truth()
        old_values = ", ".join(
            f"'{scope.value}'"
            for scope in PolicyScope
            if scope is not PolicyScope.DELEGATION_KEY_ROTATE_APPROVE
        )
        for table, column, constraint in (
            (
                "cpk_approval_requests",
                "required_scope",
                "cpk_approval_requests_scope_check",
            ),
            (
                "cpk_approval_decisions",
                "scope",
                "cpk_approval_decisions_scope_check",
            ),
        ):
            self.connection.execute(
                f"ALTER TABLE {table} DROP CONSTRAINT {constraint}"
            )
            self.connection.execute(
                f"ALTER TABLE {table} ADD CONSTRAINT {constraint} "
                f"CHECK ({column} IN ({old_values}))"
            )

        install_schema(self.connection)
        after_upgrade = self._constraint_identities()

        definitions = " ".join(
            row[0]
            for row in self.connection.execute(
                """
                SELECT pg_get_constraintdef(oid)
                FROM pg_constraint
                WHERE conname IN (
                  'cpk_approval_requests_scope_check',
                  'cpk_approval_decisions_scope_check'
                )
                ORDER BY conname
                """
            ).fetchall()
        )
        self.assertIn(PolicyScope.DELEGATION_KEY_ROTATE_APPROVE.value, definitions)
        self.assertEqual(
            self.connection.execute(
                "SELECT count(*) FROM cpk_approval_requests"
            ).fetchone(),
            (1,),
        )

        install_schema(self.connection)
        self.assertEqual(self._constraint_identities(), after_upgrade)

    def test_install_expands_stale_operation_action_kinds_without_row_loss(
        self,
    ) -> None:
        install_schema(self.connection)
        self._seed_minimal_execution_truth()
        old_kinds = tuple(
            kind
            for kind in OperatorCommandKind
            if kind
            is not OperatorCommandKind.REQUEST_PUBLIC_INGRESS_RESERVATION_RELEASE
        ) + tuple(LifecycleOperationKind)
        old_values = ", ".join(f"'{kind.value}'" for kind in old_kinds)
        self.connection.execute(
            "ALTER TABLE cpk_operation_actions "
            "DROP CONSTRAINT cpk_operation_actions_type_check"
        )
        self.connection.execute(
            "ALTER TABLE cpk_operation_actions "
            "ADD CONSTRAINT cpk_operation_actions_type_check "
            f"CHECK (action_type IN ({old_values}))"
        )

        install_schema(self.connection)
        after_upgrade = self._constraint_identities()
        self.connection.execute(
            """
            INSERT INTO cpk_operation_actions
              (action_id, session_id, ordinal, action_type, actor_id, created_at)
            VALUES (
              'release-action', 'session-a', 1,
              'request-public-ingress-reservation-release',
              'operator', 'release-at'
            )
            """
        )

        self.assertEqual(
            self.connection.execute(
                "SELECT count(*) FROM cpk_operation_actions"
            ).fetchone(),
            (1,),
        )
        install_schema(self.connection)
        self.assertEqual(self._constraint_identities(), after_upgrade)

    def test_install_expands_stale_rotation_status_checks_without_row_loss(
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

        install_schema(self.connection)
        after_upgrade = self._constraint_identities()
        self.connection.execute(
            """
            UPDATE cpk_gateway_key_rotations
            SET status = 'generation-prepared', version = 2,
                generation_provider_registration_id = 'provider-registration-a',
                generation_action_digest = %s
            WHERE rotation_id = 'rotation-a'
            """,
            ("b" * 64,),
        )
        self.connection.execute(
            """
            INSERT INTO cpk_gateway_key_rotation_transitions (
              rotation_id, transition_id, from_status, to_status,
              from_version, to_version, transition_fingerprint,
              advanced_by, advanced_at
            ) VALUES (
              'rotation-a', 'prepare-generation', 'approved',
              'generation-prepared', 1, 2, %s, 'operator-a',
              '2026-08-02T00:00:01Z'
            )
            """,
            ("c" * 64,),
        )
        self.assertEqual(
            self.connection.execute(
                "SELECT status FROM cpk_gateway_key_rotations"
            ).fetchall(),
            [(GatewayKeyRotationStatus.GENERATION_PREPARED.value,)],
        )

        install_schema(self.connection)
        self.assertEqual(self._constraint_identities(), after_upgrade)

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
        self.assertEqual(columns["removed_at"], ("text", "YES"))
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

    def test_install_backfills_exact_legacy_retained_ingress_reservation(self) -> None:
        install_schema(self.connection)
        self.connection.execute(
            """
            INSERT INTO cpk_workspaces (workspace_id, name, lifecycle)
            VALUES ('workspace-a', 'Workspace A', 'running')
            """
        )
        self.connection.execute(
            """
            INSERT INTO cpk_cloudflare_ingress_resources (
              workspace_id, runtime_id, ingress_id, epoch, status,
              authority_ref, provider_kind, tunnel_name, tunnel_id,
              dns_record_id, hostname, zone_id, lifecycle, created_at,
              observed_at, source_run_id, source_activity_id, source_event_id
            ) VALUES (
              'workspace-a', 'docker-a', 'gateway-001', 1, 'active',
              'openj92-public-ingress', 'cloudflare', 'cpk-gateway-001',
              'tunnel-001', 'dns-001', 'cpk-gateway-001.openj92.dev',
              'zone-openj92', 'retained', 'created-at', 'observed-at',
              'run-001', 'activity-001', 'event-001'
            )
            """
        )

        install_schema(self.connection)

        reservation = self.connection.execute(
            """
            SELECT reservation_id, workspace_id, ingress_id, status,
                   dns_record_id, hostname, source_run_id
            FROM cpk_cloudflare_hostname_reservations
            """
        ).fetchone()
        joined = self.connection.execute(
            """
            SELECT reservation_id
            FROM cpk_cloudflare_ingress_resources
            WHERE workspace_id = 'workspace-a' AND ingress_id = 'gateway-001'
            """
        ).fetchone()
        self.assertEqual(reservation[1:], (
            'workspace-a', 'gateway-001', 'bound', 'dns-001',
            'cpk-gateway-001.openj92.dev', 'run-001'
        ))
        self.assertEqual(joined[0], reservation[0])

        install_schema(self.connection)
        self.assertEqual(
            self.connection.execute(
                "SELECT count(*) FROM cpk_cloudflare_hostname_reservations"
            ).fetchone(),
            (1,),
        )

    def test_ambiguous_legacy_retained_ownership_rolls_back_install(self) -> None:
        install_schema(self.connection)
        self.connection.execute(
            """
            INSERT INTO cpk_workspaces (workspace_id, name, lifecycle)
            VALUES ('workspace-a', 'Workspace A', 'running')
            """
        )
        for ingress_id, tunnel_id, dns_record_id in (
            ('gateway-a', 'tunnel-a', 'dns-a'),
            ('gateway-b', 'tunnel-b', 'dns-b'),
        ):
            self.connection.execute(
                """
                INSERT INTO cpk_cloudflare_ingress_resources (
                  workspace_id, runtime_id, ingress_id, epoch, status,
                  authority_ref, provider_kind, tunnel_name, tunnel_id,
                  dns_record_id, hostname, zone_id, lifecycle, created_at,
                  observed_at, source_run_id, source_activity_id, source_event_id
                ) VALUES (
                  'workspace-a', 'docker-a', %s, 1, 'active',
                  'openj92-public-ingress', 'cloudflare', %s, %s,
                  %s, 'cpk-gateway-001.openj92.dev', 'zone-openj92',
                  'retained', 'created-at', 'observed-at', %s, %s, %s
                )
                """,
                (
                    ingress_id,
                    f'cpk-{ingress_id}',
                    tunnel_id,
                    dns_record_id,
                    f'run-{ingress_id}',
                    f'activity-{ingress_id}',
                    f'event-{ingress_id}',
                ),
            )

        self.connection.autocommit = False
        try:
            with self.assertRaisesRegex(ValueError, "ambiguous legacy retained"):
                install_schema(self.connection)
        finally:
            self.connection.rollback()
            self.connection.autocommit = True

        self.assertEqual(
            self.connection.execute(
                "SELECT count(*) FROM cpk_cloudflare_hostname_reservations"
            ).fetchone(),
            (0,),
        )
        self.assertEqual(
            self.connection.execute(
                "SELECT count(*) FROM cpk_cloudflare_ingress_resources"
            ).fetchone(),
            (2,),
        )

    def test_realization_join_requires_exact_reservation_workspace_and_ingress(
        self,
    ) -> None:
        install_schema(self.connection)
        self.connection.execute(
            """
            INSERT INTO cpk_workspaces (workspace_id, name, lifecycle)
            VALUES
              ('workspace-a', 'Workspace A', 'running'),
              ('workspace-b', 'Workspace B', 'running')
            """
        )
        self.connection.execute(
            """
            INSERT INTO cpk_cloudflare_hostname_reservations (
              reservation_id, workspace_id, ingress_id, authority_ref,
              provider_kind, dns_record_id, hostname, zone_id, lifecycle,
              status, created_at, observed_at, source_run_id,
              source_activity_id, source_event_id
            ) VALUES (
              'reservation-001', 'workspace-a', 'gateway-001',
              'openj92-public-ingress', 'cloudflare', 'dns-001',
              'gateway.openj92.dev', 'zone-openj92', 'retained', 'bound',
              'created-at', 'observed-at', 'run-001', 'activity-001', 'event-001'
            )
            """
        )

        with self.assertRaises(ForeignKeyViolation):
            self.connection.execute(
                """
                INSERT INTO cpk_cloudflare_ingress_resources (
                  workspace_id, runtime_id, ingress_id, reservation_id, epoch,
                  status, authority_ref, provider_kind, tunnel_name, tunnel_id,
                  dns_record_id, hostname, zone_id, lifecycle, created_at,
                  observed_at, source_run_id, source_activity_id, source_event_id
                ) VALUES (
                  'workspace-b', 'docker-b', 'gateway-001', 'reservation-001', 1,
                  'active', 'openj92-public-ingress', 'cloudflare',
                  'cpk-gateway-001', 'tunnel-001', 'dns-001',
                  'gateway.openj92.dev', 'zone-openj92', 'retained',
                  'created-at', 'observed-at', 'run-001', 'activity-001',
                  'event-001'
                )
                """
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
                  (request_id, session_id, plan_id, subject_kind, subject_payload,
                   review_digest, requested_by, requested_at,
                   required_scope, max_risk, destructive)
                VALUES ('bad-approval-scope', 'session-a', 'plan-a', 'activity-plan',
                        '{"kind":"activity-plan","plan_id":"plan-a"}'::jsonb,
                        encode(sha256(convert_to('activity-plan:plan-a', 'UTF8')), 'hex'),
                        'operator',
                        'approval-request-at', 'plan:invented', 'low', false)
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
            """
        )
        self.connection.execute(
            """
            INSERT INTO cpk_graph_versions
              (graph_id, workspace_id, version, graph_descriptor, created_by,
               created_at)
            VALUES ('graph-a', 'workspace-a', 1, %s, 'operator', 'graph-at');
            """,
            (Jsonb(DEFAULT_GRAPH_CODEC.encode(DeploymentGraph("current"))),),
        )
        self.connection.execute(
            """
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
              (request_id, session_id, plan_id, subject_kind, subject_payload,
               review_digest, requested_by, requested_at,
               required_scope, max_risk, destructive)
            VALUES ('approval-request-a', 'session-a', 'plan-a', 'activity-plan',
                    '{"kind":"activity-plan","plan_id":"plan-a"}'::jsonb,
                    encode(sha256(convert_to('activity-plan:plan-a', 'UTF8')), 'hex'),
                    'operator',
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
