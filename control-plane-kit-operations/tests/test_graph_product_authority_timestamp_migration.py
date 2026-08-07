from __future__ import annotations

from datetime import datetime, timezone
import os
import unittest
import uuid

import psycopg
from psycopg.types.json import Jsonb

import control_plane_kit_operations.postgres as postgres
from control_plane_kit_core.topology import DEFAULT_GRAPH_CODEC, DeploymentGraph


_TEMPORAL_COLUMNS = (
    ("cpk_graph_versions", "created_at", "NO", 6),
    ("cpk_image_pull_authorities", "admitted_at", "NO", 6),
    ("cpk_ingress_authorities", "admitted_at", "NO", 6),
    ("cpk_realized_graph_projections", "created_at", "NO", 6),
    ("cpk_registered_products", "imported_at", "NO", 6),
    ("cpk_runtime_authorities", "admitted_at", "NO", 6),
    ("cpk_runtime_authority_deliveries", "admitted_at", "NO", 6),
)


class GraphProductAuthorityTimestampMigrationTests(unittest.TestCase):
    def setUp(self) -> None:
        database_url = os.environ.get("CPK_OPERATIONS_TEST_DATABASE_URL")
        if not database_url:
            raise RuntimeError(
                "CPK_OPERATIONS_TEST_DATABASE_URL is required; use Docker-first tests"
            )
        self.schema = f"graph_product_time_{uuid.uuid4().hex}"
        self.connection = psycopg.connect(database_url, autocommit=True)
        self.connection.execute(f'CREATE SCHEMA "{self.schema}"')
        self.connection.execute(f'SET search_path TO "{self.schema}"')

    def tearDown(self) -> None:
        self.connection.execute("SET search_path TO public")
        self.connection.execute(f'DROP SCHEMA IF EXISTS "{self.schema}" CASCADE')
        self.connection.close()

    def test_registry_appends_exact_graph_product_authority_v3(self) -> None:
        registry = postgres.POSTGRES_SCHEMA_MIGRATIONS

        self.assertEqual(registry.target_version, 3)
        self.assertEqual(
            tuple((migration.version, migration.name) for migration in registry.migrations),
            (
                (1, "operations-baseline"),
                (2, "coordination-timestamps"),
                (3, "graph-product-authority-timestamps"),
            ),
        )
        self.assertEqual(
            postgres.POSTGRES_SCHEMA_V1_SHA256,
            registry.migrations[0].checksum_sha256,
        )

    def test_fresh_install_has_exact_v3_history_and_temporal_contract(self) -> None:
        postgres.install_postgres_schema(self.connection)

        self.assertEqual(
            self._ledger(),
            [
                (1, "operations-baseline"),
                (2, "coordination-timestamps"),
                (3, "graph-product-authority-timestamps"),
            ],
        )
        self.assertEqual(self._temporal_contract(), _TEMPORAL_COLUMNS)
        self.assertIs(
            postgres.verify_postgres_schema(self.connection).kind,
            postgres.ObservedSchemaKind.VERSIONED,
        )

    def test_retained_v2_values_migrate_without_identity_loss(self) -> None:
        self._install_v2_baseline()
        self.connection.execute("SET TIME ZONE 'America/New_York'")
        self._seed_retained_rows("2026-08-07T06:00:00.000001Z")

        postgres.install_postgres_schema(self.connection)

        expected = datetime(2026, 8, 7, 6, 0, 0, 1, tzinfo=timezone.utc)
        self.assertEqual(self._retained_values(), (expected,) * 7)
        self.assertEqual(self._temporal_contract(), _TEMPORAL_COLUMNS)

    def test_retained_noncanonical_value_rolls_back_v3_ledger_and_ddl(self) -> None:
        self._install_v2_baseline()
        self._seed_retained_rows("not-a-timestamp")

        with self.assertRaises(postgres.SchemaMigrationError) as raised:
            postgres.install_postgres_schema(self.connection)

        self.assertLessEqual(len(str(raised.exception)), 256)
        self.assertNotIn("not-a-timestamp", str(raised.exception))
        self.assertIsNone(raised.exception.__context__)
        self.assertEqual(
            self._ledger(),
            [(1, "operations-baseline"), (2, "coordination-timestamps")],
        )
        self.assertEqual(
            self.connection.execute(
                "SELECT created_at FROM cpk_graph_versions"
            ).fetchone(),
            ("not-a-timestamp",),
        )
        self.assertEqual(
            self._column_type("cpk_graph_versions", "created_at"),
            ("text", None),
        )

    def test_retained_calendar_invalid_value_has_bounded_v3_category(self) -> None:
        self._install_v2_baseline()
        self._seed_retained_rows("2026-02-30T06:00:00Z")

        with self.assertRaises(postgres.SchemaMigrationError) as raised:
            postgres.install_postgres_schema(self.connection)

        self.assertEqual(
            str(raised.exception),
            "graph, product, and authority timestamps are not canonical UTC",
        )
        self.assertIsNone(raised.exception.__context__)
        self.assertEqual(
            self._ledger(),
            [(1, "operations-baseline"), (2, "coordination-timestamps")],
        )

    def test_current_verifier_rejects_owned_temporal_precision_drift(self) -> None:
        postgres.install_postgres_schema(self.connection)
        self.connection.execute(
            """
            ALTER TABLE cpk_graph_versions
              ALTER COLUMN created_at TYPE timestamptz(5)
                USING created_at::timestamptz(5)
            """
        )

        with self.assertRaises(postgres.SchemaMigrationError):
            postgres.verify_postgres_schema(self.connection)

    def test_reinstall_backfills_graph_lineage_through_temporal_codec(self) -> None:
        postgres.install_postgres_schema(self.connection)
        created_at = datetime(2026, 8, 7, 6, 0, 0, 1, tzinfo=timezone.utc)
        self.connection.execute(
            """
            INSERT INTO cpk_workspaces
              (workspace_id, name, lifecycle, current_graph_id)
            VALUES ('workspace-a', 'Workspace A', 'created', 'graph-a')
            """
        )
        self.connection.execute(
            """
            INSERT INTO cpk_graph_versions
              (graph_id, workspace_id, version, graph_descriptor, created_by,
               created_at)
            VALUES ('graph-a', 'workspace-a', 1, %s, 'operator-a', %s)
            """,
            (
                Jsonb(DEFAULT_GRAPH_CODEC.encode(DeploymentGraph("graph-a"))),
                created_at,
            ),
        )

        postgres.install_postgres_schema(self.connection)

        self.assertEqual(
            self.connection.execute(
                """
                SELECT created_at
                FROM cpk_realized_graph_projections
                WHERE source_authored_graph_id = 'graph-a'
                """
            ).fetchone(),
            (created_at,),
        )

    def _install_v2_baseline(self) -> None:
        registry = postgres.POSTGRES_SCHEMA_MIGRATIONS
        self.connection.execute(postgres.POSTGRES_SCHEMA)
        self.connection.execute(registry.migrations[1].sql)
        self.connection.execute(
            """
            CREATE TABLE cpk_schema_migrations (
              version integer NOT NULL PRIMARY KEY,
              name text NOT NULL,
              checksum_sha256 text NOT NULL,
              applied_at timestamptz NOT NULL DEFAULT clock_timestamp()
            )
            """
        )
        for migration in registry.migrations[:2]:
            self.connection.execute(
                """
                INSERT INTO cpk_schema_migrations (version, name, checksum_sha256)
                VALUES (%s, %s, %s)
                """,
                (migration.version, migration.name, migration.checksum_sha256),
            )

    def _seed_retained_rows(self, timestamp: str) -> None:
        self.connection.execute(
            """
            INSERT INTO cpk_workspaces (workspace_id, name, lifecycle)
            VALUES ('workspace-a', 'Workspace A', 'created')
            """
        )
        statements = (
            """
            INSERT INTO cpk_graph_versions
              (graph_id, workspace_id, version, graph_descriptor, created_by,
               created_at)
            VALUES ('graph-a', 'workspace-a', 1, '{}'::jsonb, 'operator-a', %s)
            """,
            """
            INSERT INTO cpk_realized_graph_projections
              (projection_id, workspace_id, source_authored_graph_id,
               projection_kind, projection_key, projection_digest,
               graph_descriptor, created_by, created_at)
            VALUES ('projection-a', 'workspace-a', 'graph-a', 'identity',
                    'identity', repeat('a', 64), '{}'::jsonb, 'operator-a', %s)
            """,
            """
            INSERT INTO cpk_registered_products
              (registration_id, workspace_id, product_reference,
               descriptor_sha256, descriptor_document, descriptor_content,
               source, imported_by, imported_at, status)
            VALUES ('product-a', 'workspace-a', '{}'::jsonb, repeat('b', 64),
                    '{}'::jsonb, '{}', '{}'::jsonb, 'operator-a', %s, 'active')
            """,
            """
            INSERT INTO cpk_image_pull_authorities
              (authority_id, workspace_id, authority, registry, repository,
               credential_reference, admitted_by, admitted_at, status)
            VALUES ('image-a', 'workspace-a', '{}'::jsonb, 'ghcr.io', NULL,
                    'secret://local/workspace-a/image', 'operator-a', %s,
                    'active')
            """,
            """
            INSERT INTO cpk_runtime_authorities
              (registration_id, workspace_id, authority_ref, runtime_kind,
               authority_kind, authority, credential_references, admitted_by,
               admitted_at, status)
            VALUES ('runtime-a', 'workspace-a', 'runtime-a', 'docker',
                    'local-docker-socket', '{}'::jsonb, '{}'::jsonb,
                    'operator-a', %s, 'active')
            """,
            """
            INSERT INTO cpk_runtime_authority_deliveries
              (delivery_id, workspace_id, authority_ref, delivery_kind,
               delivery, secret_references, admitted_by, admitted_at, status)
            VALUES ('delivery-a', 'workspace-a', 'runtime-a',
                    'local-docker-socket-mount', '{}'::jsonb, '[]'::jsonb,
                    'operator-a', %s, 'active')
            """,
            """
            INSERT INTO cpk_ingress_authorities
              (registration_id, workspace_id, authority_ref, provider_kind,
               authority, credential_references, allowed_hostname_pattern,
               admitted_by, admitted_at, status)
            VALUES ('ingress-a', 'workspace-a', 'ingress-a', 'cloudflare',
                    '{}'::jsonb, '{}'::jsonb, '*.example.test', 'operator-a',
                    %s, 'active')
            """,
        )
        for statement in statements:
            self.connection.execute(statement, (timestamp,))

    def _retained_values(self) -> tuple[object, ...]:
        statements = (
            "SELECT created_at FROM cpk_graph_versions",
            "SELECT admitted_at FROM cpk_image_pull_authorities",
            "SELECT admitted_at FROM cpk_ingress_authorities",
            "SELECT created_at FROM cpk_realized_graph_projections",
            "SELECT imported_at FROM cpk_registered_products",
            "SELECT admitted_at FROM cpk_runtime_authorities",
            "SELECT admitted_at FROM cpk_runtime_authority_deliveries",
        )
        return tuple(
            self.connection.execute(statement).fetchone()[0]
            for statement in statements
        )

    def _ledger(self) -> list[tuple[int, str]]:
        return self.connection.execute(
            "SELECT version, name FROM cpk_schema_migrations ORDER BY version"
        ).fetchall()

    def _temporal_contract(self) -> tuple[tuple[str, str, str, int], ...]:
        return tuple(
            self.connection.execute(
                """
                SELECT table_name, column_name, is_nullable, datetime_precision
                FROM information_schema.columns
                WHERE table_schema = current_schema()
                  AND (table_name, column_name) IN (
                    ('cpk_graph_versions', 'created_at'),
                    ('cpk_image_pull_authorities', 'admitted_at'),
                    ('cpk_ingress_authorities', 'admitted_at'),
                    ('cpk_realized_graph_projections', 'created_at'),
                    ('cpk_registered_products', 'imported_at'),
                    ('cpk_runtime_authorities', 'admitted_at'),
                    ('cpk_runtime_authority_deliveries', 'admitted_at')
                  )
                ORDER BY table_name, column_name
                """
            ).fetchall()
        )

    def _column_type(self, table: str, column: str) -> tuple[str, int | None]:
        return self.connection.execute(
            """
            SELECT data_type, datetime_precision
            FROM information_schema.columns
            WHERE table_schema = current_schema()
              AND table_name = %s
              AND column_name = %s
            """,
            (table, column),
        ).fetchone()


if __name__ == "__main__":
    unittest.main()
