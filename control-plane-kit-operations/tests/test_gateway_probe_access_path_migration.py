from __future__ import annotations

import os
import re
import unittest
import uuid

import psycopg

import control_plane_kit_operations.postgres as postgres
from control_plane_kit_operations.postgres import migration_runner
from control_plane_kit_operations.postgres import schema as schema_module


_V10_HISTORY = (
    (1, "operations-baseline"),
    (2, "coordination-timestamps"),
    (3, "graph-product-authority-timestamps"),
    (4, "secret-registration-timestamps"),
    (5, "delegation-signing-key-timestamps"),
    (6, "gateway-probe-timestamps"),
    (7, "gateway-key-rotation-timestamps"),
    (8, "ingress-evidence-timestamps"),
    (9, "secret-use-authorization-timestamps"),
    (10, "product-descriptor-content"),
)
_V11_IDENTITY = (11, "gateway-probe-access-path")
_V12_IDENTITY = (12, "gateway-key-rotation-generation-evidence")
_V13_IDENTITY = (13, "gateway-key-rotation-status-contracts")
_V14_IDENTITY = (14, "gateway-key-rotation-retirement-evidence")
_V15_IDENTITY = (15, "approval-subject-evidence")
_V16_IDENTITY = (16, "approval-scope-contracts")
_CURRENT_IDENTITY = (17, "graph-lineage-compatibility")
_ACCESS_PATH_COLUMN = ("text", "NO", "'runtime-private'::text")
_ACCESS_PATH_CONSTRAINT = (
    "cpk_gateway_probe_attempts",
    "cpk_gateway_probe_access_path_check",
    "c",
    True,
    "CHECK ((access_path = ANY (ARRAY['runtime-private'::text, "
    "'named-public-ingress'::text])))",
)
_ACCESS_PATH_CONSTRAINT_NAME = "cpk_gateway_probe_access_path_check"


class GatewayProbeAccessPathMigrationTests(unittest.TestCase):
    def setUp(self) -> None:
        database_url = os.environ.get("CPK_OPERATIONS_TEST_DATABASE_URL")
        if not database_url:
            raise RuntimeError(
                "CPK_OPERATIONS_TEST_DATABASE_URL is required; use Docker-first tests"
            )
        self.database_url = database_url
        self.schema = f"gateway_probe_path_{uuid.uuid4().hex}"
        self.admin = psycopg.connect(database_url, autocommit=True)
        self.admin.execute(f'CREATE SCHEMA "{self.schema}"')

    def tearDown(self) -> None:
        self.admin.execute(f'DROP SCHEMA IF EXISTS "{self.schema}_other" CASCADE')
        self.admin.execute(f'DROP SCHEMA IF EXISTS "{self.schema}" CASCADE')
        self.admin.close()

    def test_registry_appends_exact_three_sql_step_v11_program(self) -> None:
        registry = postgres.POSTGRES_SCHEMA_MIGRATIONS

        self.assertEqual(registry.target_version, 17)
        self.assertEqual(
            tuple((migration.version, migration.name) for migration in registry.migrations),
            (
                *_V10_HISTORY,
                _V11_IDENTITY,
                _V12_IDENTITY,
                _V13_IDENTITY,
                _V14_IDENTITY,
                _V15_IDENTITY,
                _V16_IDENTITY,
                _CURRENT_IDENTITY,
            ),
        )
        migration = registry.migrations[10]
        self.assertIsNone(migration.sql)
        self.assertEqual(len(migration.steps), 3)
        self.assertTrue(
            all(type(step) is postgres.SqlMigrationStep for step in migration.steps)
        )
        self.assertTrue(
            migration.steps[0].sql.lstrip().startswith(
                "LOCK TABLE cpk_gateway_probe_attempts IN ACCESS EXCLUSIVE MODE;"
            )
        )
        self.assertNotIn("runtime-private", repr(migration))
        pinned = getattr(schema_module, "_POSTGRES_SCHEMA_V11_SHA256", None)
        self.assertEqual(pinned, migration.checksum_sha256)
        self.assertRegex(pinned, r"^[0-9a-f]{64}$")

    def test_absent_column_converges_without_other_row_or_object_changes(self) -> None:
        connection = self._connection()
        try:
            self._prepare_v10(connection)
            self._seed_foundation(connection)
            self._insert_attempt(connection, index=1, access_path="runtime-private")
            self._insert_attempt(
                connection,
                index=2,
                access_path="named-public-ingress",
            )
            connection.execute(
                "ALTER TABLE cpk_gateway_probe_attempts "
                "DROP CONSTRAINT cpk_gateway_probe_access_path_check"
            )
            connection.execute(
                "ALTER TABLE cpk_gateway_probe_attempts DROP COLUMN access_path"
            )
            before_rows = self._rows_without_access_path(connection)
            before_objects = self._unrelated_objects(connection)

            postgres.install_postgres_schema(connection)

            self.assertEqual(self._rows_without_access_path(connection), before_rows)
            self.assertEqual(
                connection.execute(
                    "SELECT probe_id, access_path FROM cpk_gateway_probe_attempts "
                    "ORDER BY probe_id"
                ).fetchall(),
                [
                    ("probe-1", "runtime-private"),
                    ("probe-2", "runtime-private"),
                ],
            )
            self.assertEqual(self._unrelated_objects(connection), before_objects)
            self.assertEqual(self._history(connection)[-1][:2], _CURRENT_IDENTITY)
            self.assertEqual(self._column_contract(connection), _ACCESS_PATH_COLUMN)
            self.assertEqual(self._constraint_contract(connection), _ACCESS_PATH_CONSTRAINT)
            self.assertEqual(self._column_order(connection)[-1], "access_path")

            self._insert_attempt(connection, index=3, omit_access_path=True)
            self.assertEqual(
                connection.execute(
                    "SELECT access_path FROM cpk_gateway_probe_attempts "
                    "WHERE probe_id = 'probe-3'"
                ).fetchone()[0],
                "runtime-private",
            )
            before_repeat = self._complete_snapshot(connection)

            postgres.install_postgres_schema(connection)

            self.assertEqual(self._complete_snapshot(connection), before_repeat)
        finally:
            connection.close()

    def test_exact_present_values_and_target_constraint_identity_are_preserved(
        self,
    ) -> None:
        connection = self._connection()
        try:
            self._prepare_v10(connection)
            self._seed_foundation(connection)
            self._insert_attempt(connection, index=1, access_path="runtime-private")
            self._insert_attempt(
                connection,
                index=2,
                access_path="named-public-ingress",
            )
            before_rows = connection.execute(
                "SELECT ctid::text, * FROM cpk_gateway_probe_attempts ORDER BY probe_id"
            ).fetchall()
            before_constraint = self._target_constraint_identity(connection)
            before_objects = self._unrelated_objects(connection)

            postgres.install_postgres_schema(connection)

            self.assertEqual(
                connection.execute(
                    "SELECT ctid::text, * FROM cpk_gateway_probe_attempts "
                    "ORDER BY probe_id"
                ).fetchall(),
                before_rows,
            )
            self.assertEqual(
                self._target_constraint_identity(connection),
                before_constraint,
            )
            self.assertEqual(self._unrelated_objects(connection), before_objects)
            self.assertEqual(self._history(connection)[-1][:2], _CURRENT_IDENTITY)
        finally:
            connection.close()

    def test_present_column_schema_or_value_drift_rolls_back_exact_v10_truth(
        self,
    ) -> None:
        marker = "private-probe-path-material-" + ("x" * 4096)
        cases = (
            (
                "null",
                lambda connection: (
                    connection.execute(
                        "ALTER TABLE cpk_gateway_probe_attempts "
                        "DROP CONSTRAINT cpk_gateway_probe_access_path_check"
                    ),
                    connection.execute(
                        "ALTER TABLE cpk_gateway_probe_attempts "
                        "ALTER COLUMN access_path DROP NOT NULL"
                    ),
                    connection.execute(
                        "UPDATE cpk_gateway_probe_attempts SET access_path = NULL"
                    ),
                ),
            ),
            (
                "wrong-type",
                lambda connection: (
                    connection.execute(
                        "ALTER TABLE cpk_gateway_probe_attempts "
                        "DROP CONSTRAINT cpk_gateway_probe_access_path_check"
                    ),
                    connection.execute(
                        "ALTER TABLE cpk_gateway_probe_attempts "
                        "ALTER COLUMN access_path DROP DEFAULT"
                    ),
                    connection.execute(
                        "ALTER TABLE cpk_gateway_probe_attempts "
                        "ALTER COLUMN access_path TYPE varchar(64)"
                    ),
                ),
            ),
            (
                "nullable",
                lambda connection: connection.execute(
                    "ALTER TABLE cpk_gateway_probe_attempts "
                    "ALTER COLUMN access_path DROP NOT NULL"
                ),
            ),
            (
                "wrong-default",
                lambda connection: connection.execute(
                    "ALTER TABLE cpk_gateway_probe_attempts "
                    "ALTER COLUMN access_path SET DEFAULT 'named-public-ingress'"
                ),
            ),
            (
                "unknown",
                lambda connection: (
                    connection.execute(
                        "ALTER TABLE cpk_gateway_probe_attempts "
                        "DROP CONSTRAINT cpk_gateway_probe_access_path_check"
                    ),
                    connection.execute(
                        "UPDATE cpk_gateway_probe_attempts SET access_path = 'unknown'"
                    ),
                ),
            ),
            (
                "oversized",
                lambda connection: (
                    connection.execute(
                        "ALTER TABLE cpk_gateway_probe_attempts "
                        "DROP CONSTRAINT cpk_gateway_probe_access_path_check"
                    ),
                    connection.execute(
                        "UPDATE cpk_gateway_probe_attempts SET access_path = %s",
                        (marker,),
                    ),
                ),
            ),
        )
        for index, (label, mutate) in enumerate(cases):
            with self.subTest(label=label):
                self._reset_schema()
                connection = self._connection()
                try:
                    self._prepare_v10(connection)
                    self._seed_foundation(connection)
                    self._insert_attempt(
                        connection,
                        index=index,
                        access_path="runtime-private",
                    )
                    mutate(connection)
                    before = self._complete_snapshot(connection)

                    with self.assertRaises(postgres.SchemaMigrationError) as raised:
                        postgres.install_postgres_schema(connection)

                    self.assertEqual(
                        str(raised.exception),
                        "gateway probe access path is not accepted",
                    )
                    self.assertLessEqual(len(str(raised.exception)), 256)
                    for excluded in (
                        marker,
                        "unknown",
                        self.schema,
                        "probe-",
                        "workspace-",
                        "gateway-",
                        "runtime-",
                        "SELECT",
                        "ALTER TABLE",
                        self.database_url,
                    ):
                        self.assertNotIn(excluded, str(raised.exception))
                    self.assertIsNone(raised.exception.__context__)
                    self.assertIsNone(raised.exception.__cause__)
                    self.assertEqual(self._complete_snapshot(connection), before)
                    self.assertEqual(
                        tuple(row[:2] for row in self._history(connection)),
                        _V10_HISTORY,
                    )
                finally:
                    connection.close()

    def test_missing_constraint_is_installed_but_wrong_target_contract_fails(
        self,
    ) -> None:
        connection = self._connection()
        try:
            self._prepare_v10(connection)
            self._seed_foundation(connection)
            self._insert_attempt(connection, index=1, access_path="runtime-private")
            connection.execute(
                "ALTER TABLE cpk_gateway_probe_attempts "
                "DROP CONSTRAINT cpk_gateway_probe_access_path_check"
            )

            migration = postgres.POSTGRES_SCHEMA_MIGRATIONS.migrations[10]
            self.assertEqual((migration.version, migration.name), _V11_IDENTITY)
            migration_runner._apply_schema_migration(connection, migration)

            self.assertEqual(self._constraint_contract(connection), _ACCESS_PATH_CONSTRAINT)
            self.assertEqual(
                tuple(row[:2] for row in self._history(connection)),
                _V10_HISTORY,
            )

            postgres.install_postgres_schema(connection)

            self.assertEqual(self._constraint_contract(connection), _ACCESS_PATH_CONSTRAINT)
            self.assertEqual(self._history(connection)[-1][:2], _CURRENT_IDENTITY)
        finally:
            connection.close()

        arrangements = (
            (
                "wrong-definition",
                "ALTER TABLE cpk_gateway_probe_attempts ADD CONSTRAINT "
                "cpk_gateway_probe_access_path_check "
                "CHECK (access_path = 'runtime-private')",
            ),
            (
                "unvalidated",
                "ALTER TABLE cpk_gateway_probe_attempts ADD CONSTRAINT "
                "cpk_gateway_probe_access_path_check CHECK (access_path IN "
                "('runtime-private', 'named-public-ingress')) NOT VALID",
            ),
            (
                "wrong-type",
                "ALTER TABLE cpk_gateway_probe_attempts ADD CONSTRAINT "
                "cpk_gateway_probe_access_path_check UNIQUE (access_path)",
            ),
        )
        for index, (label, definition) in enumerate(arrangements):
            with self.subTest(label=label):
                self._reset_schema()
                connection = self._connection()
                try:
                    self._prepare_v10(connection)
                    self._seed_foundation(connection)
                    self._insert_attempt(
                        connection,
                        index=index,
                        access_path="runtime-private",
                    )
                    connection.execute(
                        "ALTER TABLE cpk_gateway_probe_attempts "
                        "DROP CONSTRAINT cpk_gateway_probe_access_path_check"
                    )
                    connection.execute(definition)
                    before = self._complete_snapshot(connection)

                    with self.assertRaises(postgres.SchemaMigrationError) as raised:
                        postgres.install_postgres_schema(connection)

                    self.assertEqual(
                        str(raised.exception),
                        "gateway probe access path is not accepted",
                    )
                    self.assertEqual(self._complete_snapshot(connection), before)
                finally:
                    connection.close()

    def test_same_name_other_relations_do_not_suppress_target_installation(self) -> None:
        connection = self._connection()
        try:
            self._prepare_v10(connection)
            self._seed_foundation(connection)
            self._insert_attempt(connection, index=1, access_path="runtime-private")
            connection.execute(
                "ALTER TABLE cpk_gateway_probe_attempts "
                "DROP CONSTRAINT cpk_gateway_probe_access_path_check"
            )
            other_schema = f"{self.schema}_other"
            connection.execute(f'CREATE SCHEMA "{other_schema}"')
            connection.execute(
                f'CREATE TABLE "{other_schema}".probe_shadow (access_path text)'
            )
            connection.execute(
                f'ALTER TABLE "{other_schema}".probe_shadow ADD CONSTRAINT '
                "cpk_gateway_probe_access_path_check CHECK (access_path IS NOT NULL)"
            )
            before_other = self._named_constraint_identities(connection)

            postgres.install_postgres_schema(connection)

            self.assertEqual(self._constraint_contract(connection), _ACCESS_PATH_CONSTRAINT)
            after_other = self._named_constraint_identities(connection)
            self.assertEqual(
                {
                    key: value
                    for key, value in after_other.items()
                    if key != (self.schema, "cpk_gateway_probe_attempts")
                },
                before_other,
            )
        finally:
            connection.close()

    def test_each_sql_phase_failure_rolls_back_absent_history(self) -> None:
        registry = postgres.POSTGRES_SCHEMA_MIGRATIONS
        migration = registry.migrations[10]
        self.assertEqual((migration.version, migration.name), _V11_IDENTITY)
        for phase, step in enumerate(migration.steps):
            with self.subTest(phase=phase):
                self._reset_schema()
                connection = self._connection()
                try:
                    self._prepare_v10(connection)
                    self._seed_foundation(connection)
                    self._insert_attempt(
                        connection,
                        index=phase,
                        access_path="runtime-private",
                    )
                    connection.execute(
                        "ALTER TABLE cpk_gateway_probe_attempts "
                        "DROP CONSTRAINT cpk_gateway_probe_access_path_check"
                    )
                    connection.execute(
                        "ALTER TABLE cpk_gateway_probe_attempts DROP COLUMN access_path"
                    )
                    before = self._complete_snapshot(connection)

                    class FailingConnection:
                        @property
                        def autocommit(self):
                            return connection.autocommit

                        def transaction(self):
                            return connection.transaction()

                        def execute(self, query, params=None):
                            if query == step.sql:
                                raise RuntimeError("private driver material")
                            return connection.execute(query, params)

                    with self.assertRaises(postgres.SchemaMigrationError) as raised:
                        postgres.install_postgres_schema(FailingConnection())

                    self.assertEqual(
                        str(raised.exception),
                        "schema migration application failed",
                    )
                    self.assertIsNone(raised.exception.__context__)
                    self.assertIsNone(raised.exception.__cause__)
                    self.assertEqual(self._complete_snapshot(connection), before)
                    self.assertNotIn("access_path", self._column_order(connection))
                    self.assertEqual(
                        tuple(row[:2] for row in self._history(connection)),
                        _V10_HISTORY,
                    )
                finally:
                    connection.close()

    def test_access_exclusive_lock_survives_until_caller_transaction_end(self) -> None:
        setup = self._connection()
        try:
            self._prepare_v10(setup)
            self._seed_foundation(setup)
            self._insert_attempt(setup, index=1, access_path="runtime-private")
            setup.execute(
                "ALTER TABLE cpk_gateway_probe_attempts "
                "DROP CONSTRAINT cpk_gateway_probe_access_path_check"
            )
            setup.execute(
                "ALTER TABLE cpk_gateway_probe_attempts DROP COLUMN access_path"
            )
        finally:
            setup.close()

        caller = self._connection(autocommit=False)
        observer = self._connection()
        try:
            postgres.install_postgres_schema(caller)
            observer.execute("SET lock_timeout TO '250ms'")

            with self.assertRaises(psycopg.errors.LockNotAvailable):
                observer.execute("SELECT count(*) FROM cpk_gateway_probe_attempts")

            caller.rollback()
            self.assertEqual(
                observer.execute(
                    "SELECT count(*) FROM cpk_gateway_probe_attempts"
                ).fetchone()[0],
                1,
            )
            self.assertNotIn("access_path", self._column_order(observer))
            self.assertEqual(
                tuple(row[:2] for row in self._history(observer)),
                _V10_HISTORY,
            )
        finally:
            caller.rollback()
            caller.close()
            observer.close()

    def test_final_verifier_rejects_column_and_constraint_drift(self) -> None:
        mutations = (
            (
                "nullable",
                "ALTER TABLE cpk_gateway_probe_attempts "
                "ALTER COLUMN access_path DROP NOT NULL",
            ),
            (
                "default",
                "ALTER TABLE cpk_gateway_probe_attempts "
                "ALTER COLUMN access_path DROP DEFAULT",
            ),
            (
                "wrong-type",
                "ALTER TABLE cpk_gateway_probe_attempts "
                "DROP CONSTRAINT cpk_gateway_probe_access_path_check; "
                "ALTER TABLE cpk_gateway_probe_attempts "
                "ALTER COLUMN access_path DROP DEFAULT; "
                "ALTER TABLE cpk_gateway_probe_attempts "
                "ALTER COLUMN access_path TYPE varchar(64)",
            ),
            (
                "constraint-missing",
                "ALTER TABLE cpk_gateway_probe_attempts "
                "DROP CONSTRAINT cpk_gateway_probe_access_path_check",
            ),
            (
                "constraint-definition",
                "ALTER TABLE cpk_gateway_probe_attempts "
                "DROP CONSTRAINT cpk_gateway_probe_access_path_check; "
                "ALTER TABLE cpk_gateway_probe_attempts ADD CONSTRAINT "
                "cpk_gateway_probe_access_path_check "
                "CHECK (access_path = 'runtime-private')",
            ),
            (
                "constraint-unvalidated",
                "ALTER TABLE cpk_gateway_probe_attempts "
                "DROP CONSTRAINT cpk_gateway_probe_access_path_check; "
                "ALTER TABLE cpk_gateway_probe_attempts ADD CONSTRAINT "
                "cpk_gateway_probe_access_path_check CHECK (access_path IN "
                "('runtime-private', 'named-public-ingress')) NOT VALID",
            ),
        )
        for index, (label, mutation) in enumerate(mutations):
            with self.subTest(label=label):
                self._reset_schema()
                connection = self._connection()
                try:
                    postgres.install_postgres_schema(connection)
                    connection.execute(mutation)

                    with self.assertRaises(postgres.SchemaMigrationError) as raised:
                        postgres.verify_postgres_schema(connection)

                    self.assertEqual(
                        str(raised.exception),
                        "gateway probe access path schema is not current",
                    )
                    self.assertLessEqual(len(str(raised.exception)), 256)
                finally:
                    connection.close()

    def _prepare_v10(self, connection) -> None:
        connection.execute(postgres.POSTGRES_SCHEMA)
        for migration in postgres.POSTGRES_SCHEMA_MIGRATIONS.migrations[1:10]:
            migration_runner._apply_schema_migration(connection, migration)
        connection.execute(
            """
            CREATE TABLE cpk_schema_migrations (
              version integer NOT NULL PRIMARY KEY,
              name text NOT NULL,
              checksum_sha256 text NOT NULL,
              applied_at timestamptz NOT NULL DEFAULT clock_timestamp()
            )
            """
        )
        for migration in postgres.POSTGRES_SCHEMA_MIGRATIONS.migrations[:10]:
            connection.execute(
                """
                INSERT INTO cpk_schema_migrations
                  (version, name, checksum_sha256)
                VALUES (%s, %s, %s)
                """,
                (migration.version, migration.name, migration.checksum_sha256),
            )

    def _seed_foundation(self, connection) -> None:
        connection.execute(
            """
            INSERT INTO cpk_workspaces (workspace_id, name, lifecycle)
            VALUES ('workspace-a', 'Workspace A', 'created')
            """
        )
        connection.execute(
            """
            INSERT INTO cpk_graph_versions (
              graph_id, workspace_id, version, graph_descriptor, created_by, created_at
            ) VALUES (
              'graph-current', 'workspace-a', 1, '{}'::jsonb,
              'operator-a', '2026-08-08T12:00:00Z'
            )
            """
        )

    def _insert_attempt(
        self,
        connection,
        *,
        index: int,
        access_path: str = "runtime-private",
        omit_access_path: bool = False,
    ) -> None:
        columns = (
            "probe_id, workspace_id, request_id, actor_id, current_graph_id, "
            "gateway_node_id, gateway_runtime_id, probe_kind, target_id, "
            "request_digest, issuer, key_id, audience, grant_jti, issued_at, "
            "expires_at, status, requested_at, intent_fingerprint, evidence"
        )
        values = (
            "%s, 'workspace-a', %s, 'operator-a', 'graph-current', "
            "'gateway-a', 'runtime-a', 'http-status', 'hello.http', %s, "
            "'cpk-test', 'key-a', 'gateway:workspace-a:gateway-a', %s, "
            "1800000000, 1800000060, 'intended', "
            "'2026-08-08T12:00:00Z', %s, '{}'::jsonb"
        )
        params: tuple[object, ...] = (
            f"probe-{index}",
            f"request-{index}",
            f"{index + 1:064x}",
            f"grant-{index}",
            f"{index + 11:064x}",
        )
        if not omit_access_path:
            columns += ", access_path"
            values += ", %s"
            params = (*params, access_path)
        connection.execute(
            f"INSERT INTO cpk_gateway_probe_attempts ({columns}) VALUES ({values})",
            params,
        )

    def _connection(self, *, autocommit: bool = True):
        connection = psycopg.connect(self.database_url, autocommit=autocommit)
        connection.execute(f'SET search_path TO "{self.schema}"')
        return connection

    def _reset_schema(self) -> None:
        self.admin.execute(f'DROP SCHEMA IF EXISTS "{self.schema}" CASCADE')
        self.admin.execute(f'CREATE SCHEMA "{self.schema}"')

    @staticmethod
    def _history(connection) -> tuple[tuple[object, ...], ...]:
        return tuple(
            connection.execute(
                "SELECT version, name, checksum_sha256, applied_at "
                "FROM cpk_schema_migrations ORDER BY version"
            ).fetchall()
        )

    @staticmethod
    def _rows_without_access_path(connection) -> tuple[tuple[object, ...], ...]:
        return tuple(
            connection.execute(
                """
                SELECT probe_id, workspace_id, request_id, actor_id,
                       current_graph_id, gateway_node_id, gateway_runtime_id,
                       probe_kind, target_id, request_digest, issuer, key_id,
                       audience, grant_jti, issued_at, expires_at, status,
                       requested_at, intent_fingerprint, completed_at,
                       result_code, evidence
                FROM cpk_gateway_probe_attempts
                ORDER BY probe_id
                """
            ).fetchall()
        )

    @staticmethod
    def _column_contract(connection) -> tuple[object, ...] | None:
        return connection.execute(
            """
            SELECT data_type, is_nullable, column_default
            FROM information_schema.columns
            WHERE table_schema = current_schema()
              AND table_name = 'cpk_gateway_probe_attempts'
              AND column_name = 'access_path'
            """
        ).fetchone()

    @staticmethod
    def _constraint_contract(connection) -> tuple[object, ...] | None:
        return connection.execute(
            """
            SELECT relation.relname, constraints.conname,
                   constraints.contype::text, constraints.convalidated,
                   pg_get_constraintdef(constraints.oid, false)
            FROM pg_constraint AS constraints
            JOIN pg_class AS relation ON relation.oid = constraints.conrelid
            JOIN pg_namespace AS namespace
              ON namespace.oid = constraints.connamespace
            WHERE namespace.nspname = current_schema()
              AND relation.relname = 'cpk_gateway_probe_attempts'
              AND constraints.conname = %s
            """,
            (_ACCESS_PATH_CONSTRAINT_NAME,),
        ).fetchone()

    @staticmethod
    def _target_constraint_identity(connection) -> tuple[int, str]:
        return connection.execute(
            """
            SELECT constraints.oid,
                   pg_get_constraintdef(constraints.oid, false)
            FROM pg_constraint AS constraints
            JOIN pg_class AS relation ON relation.oid = constraints.conrelid
            JOIN pg_namespace AS namespace
              ON namespace.oid = constraints.connamespace
            WHERE namespace.nspname = current_schema()
              AND relation.relname = 'cpk_gateway_probe_attempts'
              AND constraints.conname = %s
            """,
            (_ACCESS_PATH_CONSTRAINT_NAME,),
        ).fetchone()

    @staticmethod
    def _column_order(connection) -> tuple[str, ...]:
        return tuple(
            row[0]
            for row in connection.execute(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema = current_schema()
                  AND table_name = 'cpk_gateway_probe_attempts'
                ORDER BY ordinal_position
                """
            ).fetchall()
        )

    @staticmethod
    def _unrelated_objects(connection) -> dict[tuple[str, str], str]:
        rows = connection.execute(
            """
            SELECT 'constraint', constraints.conname,
                   pg_get_constraintdef(constraints.oid, false)
            FROM pg_constraint AS constraints
            JOIN pg_class AS relation ON relation.oid = constraints.conrelid
            JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace
            WHERE namespace.nspname = current_schema()
              AND relation.relname = 'cpk_gateway_probe_attempts'
              AND constraints.conname <> 'cpk_gateway_probe_access_path_check'
            UNION ALL
            SELECT 'index', indexes.relname,
                   pg_get_indexdef(indexes.oid)
            FROM pg_class AS indexes
            JOIN pg_index AS index_contract ON index_contract.indexrelid = indexes.oid
            JOIN pg_class AS relation ON relation.oid = index_contract.indrelid
            JOIN pg_namespace AS namespace ON namespace.oid = indexes.relnamespace
            WHERE namespace.nspname = current_schema()
              AND relation.relname = 'cpk_gateway_probe_attempts'
              AND indexes.relkind = 'i'
            ORDER BY 1, 2
            """
        ).fetchall()
        return {
            (kind, name): re.sub(r"\s+", " ", definition).strip()
            for kind, name, definition in rows
        }

    def _named_constraint_identities(
        self, connection
    ) -> dict[tuple[str, str], tuple[int, str]]:
        return {
            (schema, table): (identity, definition)
            for schema, table, identity, definition in connection.execute(
                """
                SELECT namespace.nspname, relation.relname, constraints.oid,
                       pg_get_constraintdef(constraints.oid, false)
                FROM pg_constraint AS constraints
                JOIN pg_class AS relation ON relation.oid = constraints.conrelid
                JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace
                WHERE constraints.conname = %s
                  AND namespace.nspname IN (%s, %s)
                ORDER BY namespace.nspname, relation.relname
                """,
                (
                    _ACCESS_PATH_CONSTRAINT_NAME,
                    self.schema,
                    f"{self.schema}_other",
                ),
            ).fetchall()
        }

    def _complete_snapshot(self, connection) -> tuple[object, ...]:
        return (
            self._history(connection),
            self._column_order(connection),
            tuple(
                connection.execute(
                    "SELECT * FROM cpk_gateway_probe_attempts ORDER BY probe_id"
                ).fetchall()
            ),
            self._unrelated_objects(connection),
            self._constraint_contract(connection),
        )


if __name__ == "__main__":
    unittest.main()
