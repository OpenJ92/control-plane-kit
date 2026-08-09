from __future__ import annotations

import os
import re
import unittest
import uuid

import psycopg

from control_plane_kit_core.delegation_keys import DelegationKeyPurpose
from control_plane_kit_core.secrets import SecretReference
from control_plane_kit_operations.gateway_key_rotations import (
    GatewayKeyRotation,
    GatewayKeyRotationError,
)
import control_plane_kit_operations.postgres as postgres
from control_plane_kit_operations.postgres import migration_inspection
from control_plane_kit_operations.postgres import migration_runner
from control_plane_kit_operations.postgres import schema as schema_module


_V11_HISTORY = (
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
    (11, "gateway-probe-access-path"),
)
_V12_IDENTITY = (12, "gateway-key-rotation-generation-evidence")
_CATEGORICAL_ERROR = "gateway key rotation generation evidence is not accepted"
_TABLE = "cpk_gateway_key_rotations"
_PROVIDER_COLUMN = "generation_provider_registration_id"
_DIGEST_COLUMN = "generation_action_digest"
_CHECKPOINT_CONSTRAINT = "cpk_gateway_key_rotations_generation_checkpoint_check"
_PROVIDER_CONSTRAINT = "cpk_gateway_key_rotations_generation_provider_check"
_DIGEST_CONSTRAINT = "cpk_gateway_key_rotations_generation_digest_check"
_TARGET_CONSTRAINTS = (
    _CHECKPOINT_CONSTRAINT,
    _PROVIDER_CONSTRAINT,
    _DIGEST_CONSTRAINT,
)
_VALID_PROVIDER = "provider.registration:a-1"
_VALID_DIGEST = "a" * 64


class GatewayKeyRotationGenerationEvidenceMigrationTests(unittest.TestCase):
    def setUp(self) -> None:
        database_url = os.environ.get("CPK_OPERATIONS_TEST_DATABASE_URL")
        if not database_url:
            raise RuntimeError(
                "CPK_OPERATIONS_TEST_DATABASE_URL is required; use Docker-first tests"
            )
        self.database_url = database_url
        self.schema = f"gateway_rotation_generation_{uuid.uuid4().hex}"
        self.admin = psycopg.connect(database_url, autocommit=True)
        self.admin.execute(f'CREATE SCHEMA "{self.schema}"')

    def tearDown(self) -> None:
        self.admin.execute(f'DROP SCHEMA IF EXISTS "{self.schema}_other" CASCADE')
        self.admin.execute(f'DROP SCHEMA IF EXISTS "{self.schema}" CASCADE')
        self.admin.close()

    def test_registry_appends_exact_three_sql_step_v12_program(self) -> None:
        registry = postgres.POSTGRES_SCHEMA_MIGRATIONS

        self.assertEqual(registry.target_version, 12)
        self.assertEqual(
            tuple((migration.version, migration.name) for migration in registry.migrations),
            (*_V11_HISTORY, _V12_IDENTITY),
        )
        migration = registry.migrations[-1]
        self.assertIsNone(migration.sql)
        self.assertEqual(len(migration.steps), 3)
        self.assertTrue(
            all(type(step) is postgres.SqlMigrationStep for step in migration.steps)
        )
        self.assertTrue(
            migration.steps[0].sql.lstrip().startswith(
                "LOCK TABLE cpk_gateway_key_rotations IN ACCESS EXCLUSIVE MODE;"
            )
        )
        self.assertIn(
            "count(DISTINCT constraints.conname)", migration.steps[0].sql
        )
        self.assertIn(
            "constraint_count <> constraint_name_count", migration.steps[0].sql
        )
        self.assertGreaterEqual(migration.steps[0].sql.count('COLLATE "C"'), 3)
        self.assertGreaterEqual(migration.steps[2].sql.count('COLLATE "C"'), 3)
        self.assertNotIn(_VALID_PROVIDER, repr(migration))
        pinned = getattr(schema_module, "_POSTGRES_SCHEMA_V12_SHA256", None)
        self.assertEqual(
            tuple(step.checksum_sha256 for step in migration.steps),
            (
                "d8b453218fc3ca4401e0fd5bd44705d4396f046fdbe6ff3d939abb69a4b85b27",
                "159f7d0a78123bcc3c122141f77ef4365880048623083da2aefc41ffdabfb6e9",
                "6d3d9dc0381b81799d123a2384ed8c826ea70c486469a4d5a7a35659af905b75",
            ),
        )
        self.assertEqual(
            pinned,
            "a9d5c552480172e7415def95df8a5ae44b03cd7023710ef13c975de90923732a",
        )
        self.assertEqual(pinned, migration.checksum_sha256)

    def test_absent_columns_preserve_rows_and_unknown_generation_truth(self) -> None:
        connection = self._connection()
        try:
            self._prepare_v11(connection)
            self._seed_workspace(connection)
            self._insert_rotation(connection, index=1)
            self._insert_rotation(connection, index=2)
            self._drop_generation_contract(connection)
            before_rows = self._rows_without_generation(connection)
            before_objects = self._unrelated_objects(connection)

            postgres.install_postgres_schema(connection)

            self.assertEqual(self._history(connection)[-1][:2], _V12_IDENTITY)
            self.assertEqual(self._rows_without_generation(connection), before_rows)
            self.assertEqual(
                connection.execute(
                    f"SELECT rotation_id, {_PROVIDER_COLUMN}, {_DIGEST_COLUMN} "
                    f"FROM {_TABLE} ORDER BY rotation_id"
                ).fetchall(),
                [("rotation-1", None, None), ("rotation-2", None, None)],
            )
            self.assertEqual(self._column_contract(connection), self._expected_columns())
            self.assertEqual(
                set(self._target_constraint_identities(connection)),
                set(_TARGET_CONSTRAINTS),
            )
            self.assertEqual(self._unrelated_objects(connection), before_objects)
            self.assertEqual(self._column_order(connection)[-2:], (
                _PROVIDER_COLUMN,
                _DIGEST_COLUMN,
            ))
            before_repeat = self._complete_snapshot(connection)

            postgres.install_postgres_schema(connection)

            self.assertEqual(self._complete_snapshot(connection), before_repeat)
        finally:
            connection.close()

    def test_exact_present_truth_preserves_checkpoint_and_replaces_legacy_digest(
        self,
    ) -> None:
        connection = self._connection()
        try:
            self._prepare_v11(connection)
            self._seed_workspace(connection)
            self._insert_rotation(connection, index=1)
            self._insert_rotation(
                connection,
                index=2,
                provider=_VALID_PROVIDER,
                digest=_VALID_DIGEST,
            )
            before_rows = self._rows_with_generation(connection)
            before_constraints = self._target_constraint_identities(connection)
            before_checkpoint = before_constraints[_CHECKPOINT_CONSTRAINT]
            before_digest = before_constraints[_DIGEST_CONSTRAINT]

            postgres.install_postgres_schema(connection)

            self.assertEqual(self._rows_with_generation(connection), before_rows)
            after = self._target_constraint_identities(connection)
            self.assertEqual(after[_CHECKPOINT_CONSTRAINT], before_checkpoint)
            self.assertNotEqual(after[_DIGEST_CONSTRAINT][0], before_digest[0])
            self.assertNotEqual(after[_DIGEST_CONSTRAINT][1], before_digest[1])
            self.assertEqual(after[_DIGEST_CONSTRAINT][1].count('COLLATE "C"'), 1)
            self.assertIn(_PROVIDER_CONSTRAINT, after)
            self.assertEqual(self._history(connection)[-1][:2], _V12_IDENTITY)
            before_repeat = self._complete_snapshot(connection)

            postgres.install_postgres_schema(connection)

            self.assertEqual(self._complete_snapshot(connection), before_repeat)
        finally:
            connection.close()

    def test_pending_v11_with_canonical_digest_preserves_canonical_identity(
        self,
    ) -> None:
        connection = self._connection()
        try:
            self._prepare_v11(connection)
            self._seed_workspace(connection)
            self._insert_rotation(connection, index=1)
            self._insert_rotation(
                connection,
                index=2,
                provider=_VALID_PROVIDER,
                digest=_VALID_DIGEST,
            )
            connection.execute(
                f"""
                ALTER TABLE {_TABLE}
                  DROP CONSTRAINT {_DIGEST_CONSTRAINT},
                  ADD CONSTRAINT {_DIGEST_CONSTRAINT}
                  CHECK ({_DIGEST_COLUMN} IS NULL
                    OR ({_DIGEST_COLUMN} COLLATE "C") ~ '^[0-9a-f]{{64}}$')
                """
            )
            before_rows = self._rows_with_generation(connection)
            before_constraints = self._target_constraint_identities(connection)
            before_checkpoint = before_constraints[_CHECKPOINT_CONSTRAINT]
            before_digest = before_constraints[_DIGEST_CONSTRAINT]
            self.assertNotIn(_PROVIDER_CONSTRAINT, before_constraints)

            postgres.install_postgres_schema(connection)

            self.assertEqual(self._history(connection)[-1][:2], _V12_IDENTITY)
            self.assertEqual(self._rows_with_generation(connection), before_rows)
            after = self._target_constraint_identities(connection)
            self.assertEqual(after[_CHECKPOINT_CONSTRAINT], before_checkpoint)
            self.assertEqual(after[_DIGEST_CONSTRAINT], before_digest)
            self.assertIn(_PROVIDER_CONSTRAINT, after)
            before_repeat = self._complete_snapshot(connection)

            postgres.install_postgres_schema(connection)

            self.assertEqual(self._complete_snapshot(connection), before_repeat)
        finally:
            connection.close()

    def test_column_and_retained_value_drift_fail_as_exact_v12_category(self) -> None:
        marker = "private-provider-material-" + ("x" * 4096)
        cases = (
            (
                "provider-only",
                lambda connection: self._set_generation(
                    connection, provider=_VALID_PROVIDER, digest=None
                ),
            ),
            (
                "digest-only",
                lambda connection: self._set_generation(
                    connection, provider=None, digest=_VALID_DIGEST
                ),
            ),
            (
                "malformed-provider",
                lambda connection: self._set_generation(
                    connection, provider=marker, digest=_VALID_DIGEST
                ),
            ),
            (
                "malformed-digest",
                lambda connection: self._set_generation(
                    connection, provider=_VALID_PROVIDER, digest="not-a-digest"
                ),
            ),
            (
                "partial-columns",
                lambda connection: connection.execute(
                    f"ALTER TABLE {_TABLE} DROP COLUMN {_DIGEST_COLUMN}"
                ),
            ),
            (
                "wrong-type",
                lambda connection: connection.execute(
                    f"ALTER TABLE {_TABLE} ALTER COLUMN {_PROVIDER_COLUMN} "
                    "TYPE varchar(200)"
                ),
            ),
            (
                "not-null",
                lambda connection: connection.execute(
                    f"ALTER TABLE {_TABLE} ALTER COLUMN {_PROVIDER_COLUMN} SET NOT NULL"
                ),
            ),
            (
                "default",
                lambda connection: connection.execute(
                    f"ALTER TABLE {_TABLE} ALTER COLUMN {_PROVIDER_COLUMN} "
                    "SET DEFAULT 'provider-default'"
                ),
            ),
        )
        for index, (label, mutate) in enumerate(cases):
            with self.subTest(label=label):
                self._reset_schema()
                connection = self._connection()
                try:
                    self._prepare_v11(connection)
                    self._seed_workspace(connection)
                    self._insert_rotation(
                        connection,
                        index=index,
                        provider=_VALID_PROVIDER,
                        digest=_VALID_DIGEST,
                    )
                    self._drop_generation_constraints(connection)
                    mutate(connection)
                    before = self._complete_snapshot(connection)

                    with self.assertRaises(postgres.SchemaMigrationError) as raised:
                        postgres.install_postgres_schema(connection)

                    self._assert_bounded_v12_error(raised.exception, marker)
                    self.assertEqual(self._complete_snapshot(connection), before)
                    self.assertEqual(
                        tuple(row[:2] for row in self._history(connection)),
                        _V11_HISTORY,
                    )
                finally:
                    connection.close()

    def test_zero_column_history_classifies_all_named_constraints_before_ddl(
        self,
    ) -> None:
        arrangements = (
            (_CHECKPOINT_CONSTRAINT, "CHECK (rotation_id IS NOT NULL)"),
            (_PROVIDER_CONSTRAINT, "CHECK (rotation_id <> '')"),
            (_DIGEST_CONSTRAINT, "CHECK (workspace_id IS NOT NULL)"),
            (_PROVIDER_CONSTRAINT, "CHECK (rotation_id IS NOT NULL) NOT VALID"),
        )
        for index, (constraint, definition) in enumerate(arrangements):
            with self.subTest(constraint=constraint, definition=definition):
                self._reset_schema()
                connection = self._connection()
                try:
                    self._prepare_v11(connection)
                    self._seed_workspace(connection)
                    self._insert_rotation(connection, index=index)
                    self._drop_generation_contract(connection)
                    connection.execute(
                        f"ALTER TABLE {_TABLE} ADD CONSTRAINT {constraint} {definition}"
                    )
                    before = self._complete_snapshot(connection)
                    before_target = self._target_constraint_identities(connection)

                    with self.assertRaises(postgres.SchemaMigrationError) as raised:
                        postgres.install_postgres_schema(connection)

                    self._assert_bounded_v12_error(raised.exception)
                    self.assertEqual(self._complete_snapshot(connection), before)
                    self.assertEqual(
                        self._target_constraint_identities(connection), before_target
                    )
                    self.assertNotIn(_PROVIDER_COLUMN, self._column_order(connection))
                    self.assertNotIn(_DIGEST_COLUMN, self._column_order(connection))
                finally:
                    connection.close()

    def test_missing_constraints_install_and_other_relations_remain_untouched(
        self,
    ) -> None:
        connection = self._connection()
        try:
            self._prepare_v11(connection)
            self._seed_workspace(connection)
            self._insert_rotation(
                connection,
                index=1,
                provider=_VALID_PROVIDER,
                digest=_VALID_DIGEST,
            )
            self._drop_generation_constraints(connection)
            other_schema = f"{self.schema}_other"
            connection.execute(f'CREATE SCHEMA "{other_schema}"')
            for index, name in enumerate(_TARGET_CONSTRAINTS):
                connection.execute(
                    f'CREATE TABLE "{other_schema}".shadow_{index} (value text)'
                )
                connection.execute(
                    f'ALTER TABLE "{other_schema}".shadow_{index} '
                    f"ADD CONSTRAINT {name} CHECK (value IS NOT NULL)"
                )
            before_other = self._named_constraint_identities(connection)

            postgres.install_postgres_schema(connection)

            self.assertEqual(
                set(self._target_constraint_identities(connection)),
                set(_TARGET_CONSTRAINTS),
            )
            self.assertEqual(self._named_constraint_identities(connection), before_other)
            provider_definition = self._target_constraint_identities(connection)[
                _PROVIDER_CONSTRAINT
            ][1]
            self.assertEqual(provider_definition.count('COLLATE "C"'), 2)
        finally:
            connection.close()

    def test_provider_and_digest_vectors_match_public_and_postgres_admission(
        self,
    ) -> None:
        accepted = ("a", "A" + ("z" * 199))
        rejected = (
            "",
            "A" + ("z" * 200),
            "-bad-first",
            "bad/interior",
            "bad interior",
            "prоvider",
            "bad\ninterior",
        )
        for provider in accepted:
            with self.subTest(surface="python-accepted", provider_length=len(provider)):
                self._rotation_value(provider)
        for provider in rejected:
            with self.subTest(surface="python-rejected", provider_length=len(provider)):
                with self.assertRaises(GatewayKeyRotationError):
                    self._rotation_value(provider)

        accepted_digests = ("0" * 64, "0123456789abcdef" * 4)
        rejected_digests = (
            "A" * 64,
            "g" * 64,
            "0" * 63,
            ("0" * 63) + "０",
            ("0" * 32) + "\n" + ("0" * 31),
        )
        for digest in accepted_digests:
            with self.subTest(surface="python-digest-accepted"):
                self._rotation_value(_VALID_PROVIDER, digest=digest)
        for digest in rejected_digests:
            with self.subTest(surface="python-digest-rejected"):
                with self.assertRaises(GatewayKeyRotationError):
                    self._rotation_value(_VALID_PROVIDER, digest=digest)

        connection = self._connection()
        try:
            self._prepare_v11(connection)
            self._seed_workspace(connection)
            postgres.install_postgres_schema(connection)
            for index, provider in enumerate(accepted):
                with self.subTest(surface="postgres-accepted", index=index):
                    self._insert_rotation(
                        connection,
                        index=index,
                        provider=provider,
                        digest=_VALID_DIGEST,
                    )
            for index, provider in enumerate(rejected, start=len(accepted)):
                with self.subTest(surface="postgres-rejected", index=index):
                    with self.assertRaises(psycopg.errors.CheckViolation):
                        self._insert_rotation(
                            connection,
                            index=index,
                            provider=provider,
                            digest=_VALID_DIGEST,
                        )
            digest_start = len(accepted) + len(rejected)
            for index, digest in enumerate(accepted_digests, start=digest_start):
                with self.subTest(surface="postgres-digest-accepted", index=index):
                    self._insert_rotation(
                        connection,
                        index=index,
                        provider=_VALID_PROVIDER,
                        digest=digest,
                    )
            for index, digest in enumerate(
                rejected_digests,
                start=digest_start + len(accepted_digests),
            ):
                with self.subTest(surface="postgres-digest-rejected", index=index):
                    with self.assertRaises(psycopg.errors.CheckViolation):
                        self._insert_rotation(
                            connection,
                            index=index,
                            provider=_VALID_PROVIDER,
                            digest=digest,
                        )
            migration = self._v12()
            self.assertGreaterEqual(migration.steps[0].sql.count('COLLATE "C"'), 3)
            self.assertGreaterEqual(migration.steps[2].sql.count('COLLATE "C"'), 3)
        finally:
            connection.close()

    def test_each_sql_phase_failure_rolls_back_exact_v11_history(self) -> None:
        migration = self._v12()
        for phase, step in enumerate(migration.steps):
            with self.subTest(phase=phase):
                self._reset_schema()
                connection = self._connection()
                try:
                    self._prepare_v11(connection)
                    self._seed_workspace(connection)
                    self._insert_rotation(connection, index=phase)
                    self._drop_generation_contract(connection)
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
                        str(raised.exception), "schema migration application failed"
                    )
                    self.assertIsNone(raised.exception.__context__)
                    self.assertIsNone(raised.exception.__cause__)
                    self.assertEqual(self._complete_snapshot(connection), before)
                    self.assertEqual(
                        tuple(row[:2] for row in self._history(connection)),
                        _V11_HISTORY,
                    )
                finally:
                    connection.close()

    def test_access_exclusive_lock_and_caller_rollback_cover_complete_v12(self) -> None:
        setup = self._connection()
        try:
            self._prepare_v11(setup)
            self._seed_workspace(setup)
            self._insert_rotation(setup, index=1)
            self._drop_generation_contract(setup)
        finally:
            setup.close()

        caller = self._connection(autocommit=False)
        observer = self._connection()
        try:
            postgres.install_postgres_schema(caller)
            self.assertEqual(self._history(caller)[-1][:2], _V12_IDENTITY)
            observer.execute("SET lock_timeout TO '250ms'")

            with self.assertRaises(psycopg.errors.LockNotAvailable):
                observer.execute(f"SELECT count(*) FROM {_TABLE}")

            caller.rollback()
            self.assertNotIn(_PROVIDER_COLUMN, self._column_order(observer))
            self.assertNotIn(_DIGEST_COLUMN, self._column_order(observer))
            self.assertEqual(
                tuple(row[:2] for row in self._history(observer)), _V11_HISTORY
            )
        finally:
            caller.rollback()
            caller.close()
            observer.close()

    def test_final_verifier_is_bounded_and_rejects_owned_contract_drift(self) -> None:
        verifier = getattr(
            migration_inspection,
            "_verify_gateway_key_rotation_generation_evidence_contract",
            None,
        )
        self.assertTrue(callable(verifier))

        valid_columns = [
            (_DIGEST_COLUMN, "text", "YES", True),
            (_PROVIDER_COLUMN, "text", "YES", True),
        ]
        valid_constraints = [
            (_CHECKPOINT_CONSTRAINT, "c", True, True),
            (_DIGEST_CONSTRAINT, "c", True, True),
            (_PROVIDER_CONSTRAINT, "c", True, True),
        ]

        class Cursor:
            def __init__(self, rows):
                self.rows = rows

            def fetchall(self):
                return self.rows

        class ScriptedConnection:
            def __init__(self, column_rows, constraint_rows):
                self.column_rows = column_rows
                self.constraint_rows = constraint_rows
                self.queries = []

            def execute(self, query, params=None):
                normalized = re.sub(r"\s+", " ", query).strip()
                self.queries.append((normalized, params))
                if "information_schema.columns" in normalized:
                    return Cursor(self.column_rows)
                return Cursor(self.constraint_rows)

        exact = ScriptedConnection(valid_columns, valid_constraints)
        verifier(exact)
        column_query, constraint_query = (query for query, _ in exact.queries)
        constraint_params = exact.queries[1][1]
        self.assertIn("LIMIT 3", column_query)
        self.assertIn("column_default IS NULL", column_query)
        self.assertIn("LIMIT 4", constraint_query)
        self.assertIn("pg_get_constraintdef", constraint_query)
        self.assertNotIn("namespace.nspname,", constraint_query)
        self.assertEqual(len(constraint_params), 3)
        self.assertEqual(constraint_params[1].count('COLLATE "C"'), 1)

        for label, columns, constraints in (
            ("duplicate-column", [*valid_columns, valid_columns[0]], valid_constraints),
            (
                "duplicate-constraint",
                valid_columns,
                [*valid_constraints, valid_constraints[0]],
            ),
        ):
            with self.subTest(label=label):
                with self.assertRaises(postgres.SchemaMigrationError) as raised:
                    verifier(ScriptedConnection(columns, constraints))
                self.assertLessEqual(len(str(raised.exception)), 256)

        mutations = (
            f"ALTER TABLE {_TABLE} ALTER COLUMN {_PROVIDER_COLUMN} SET DEFAULT 'x'",
            f"ALTER TABLE {_TABLE} DROP CONSTRAINT {_PROVIDER_CONSTRAINT}",
            f"ALTER TABLE {_TABLE} DROP CONSTRAINT {_DIGEST_CONSTRAINT}; "
            f"ALTER TABLE {_TABLE} ADD CONSTRAINT {_DIGEST_CONSTRAINT} "
            f"CHECK ({_DIGEST_COLUMN} IS NULL)",
        )
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                self._reset_schema()
                connection = self._connection()
                try:
                    postgres.install_postgres_schema(connection)
                    connection.execute(mutation)
                    with self.assertRaises(postgres.SchemaMigrationError):
                        postgres.verify_postgres_schema(connection)
                finally:
                    connection.close()

    def _prepare_v11(self, connection) -> None:
        connection.execute(postgres.POSTGRES_SCHEMA)
        for migration in postgres.POSTGRES_SCHEMA_MIGRATIONS.migrations[1:11]:
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
        for migration in postgres.POSTGRES_SCHEMA_MIGRATIONS.migrations[:11]:
            connection.execute(
                """
                INSERT INTO cpk_schema_migrations (version, name, checksum_sha256)
                VALUES (%s, %s, %s)
                """,
                (migration.version, migration.name, migration.checksum_sha256),
            )

    def _seed_workspace(self, connection) -> None:
        connection.execute(
            """
            INSERT INTO cpk_workspaces (workspace_id, name, lifecycle)
            VALUES ('workspace-a', 'Workspace A', 'created')
            """
        )

    def _insert_rotation(
        self,
        connection,
        *,
        index: int,
        provider: str | None = None,
        digest: str | None = None,
    ) -> None:
        connection.execute(
            f"""
            INSERT INTO {_TABLE} (
              rotation_id, workspace_id, gateway_node_id, purpose, issuer,
              old_key_id, new_secret_reference, key_generation_correlation,
              maximum_grant_lifetime_seconds, clock_skew_seconds,
              correlation_id, requested_by, requested_at, intent_fingerprint,
              status, version, {_PROVIDER_COLUMN}, {_DIGEST_COLUMN}
            ) VALUES (
              %s, 'workspace-a', %s, %s, 'cpk-server', %s,
              'secret://workspace-secrets/keys/gateway-key-b', %s,
              120, 10, %s, 'operator-a', '2026-08-09T12:00:00Z', %s,
              'requested', 1, %s, %s
            )
            """,
            (
                f"rotation-{index}",
                f"gateway-{index}",
                DelegationKeyPurpose.GATEWAY_PROBE.value,
                f"old-key-{index}",
                f"generate-key-{index}",
                f"rotation-correlation-{index}",
                f"{index + 1:064x}",
                provider,
                digest,
            ),
        )

    @staticmethod
    def _set_generation(connection, *, provider, digest) -> None:
        connection.execute(
            f"UPDATE {_TABLE} SET {_PROVIDER_COLUMN} = %s, {_DIGEST_COLUMN} = %s",
            (provider, digest),
        )

    @staticmethod
    def _rotation_value(
        provider: str, *, digest: str = _VALID_DIGEST
    ) -> GatewayKeyRotation:
        return GatewayKeyRotation(
            rotation_id="rotation-value",
            workspace_id="workspace-a",
            gateway_node_id="gateway-a",
            purpose=DelegationKeyPurpose.GATEWAY_PROBE,
            issuer="cpk-server",
            old_key_id="old-key-a",
            new_secret_reference=SecretReference(
                "secret://workspace-secrets/keys/gateway-key-b"
            ),
            key_generation_correlation="generate-key-b",
            maximum_grant_lifetime_seconds=120,
            clock_skew_seconds=10,
            correlation_id="rotation-correlation",
            requested_by="operator-a",
            requested_at="2026-08-09T12:00:00Z",
            intent_fingerprint="a" * 64,
            generation_provider_registration_id=provider,
            generation_action_digest=digest,
        )

    def _drop_generation_contract(self, connection) -> None:
        self._drop_generation_constraints(connection)
        connection.execute(f"ALTER TABLE {_TABLE} DROP COLUMN {_PROVIDER_COLUMN}")
        connection.execute(f"ALTER TABLE {_TABLE} DROP COLUMN {_DIGEST_COLUMN}")

    @staticmethod
    def _drop_generation_constraints(connection) -> None:
        for name in _TARGET_CONSTRAINTS:
            connection.execute(f"ALTER TABLE {_TABLE} DROP CONSTRAINT IF EXISTS {name}")

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
    def _column_order(connection) -> tuple[str, ...]:
        return tuple(
            row[0]
            for row in connection.execute(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema = current_schema()
                  AND table_name = 'cpk_gateway_key_rotations'
                ORDER BY ordinal_position
                """
            ).fetchall()
        )

    @staticmethod
    def _column_contract(connection) -> tuple[tuple[object, ...], ...]:
        return tuple(
            connection.execute(
                """
                SELECT column_name, data_type, is_nullable, column_default IS NULL
                FROM information_schema.columns
                WHERE table_schema = current_schema()
                  AND table_name = 'cpk_gateway_key_rotations'
                  AND column_name IN (
                    'generation_provider_registration_id',
                    'generation_action_digest'
                  )
                ORDER BY column_name
                LIMIT 3
                """
            ).fetchall()
        )

    @staticmethod
    def _expected_columns() -> tuple[tuple[object, ...], ...]:
        return (
            (_DIGEST_COLUMN, "text", "YES", True),
            (_PROVIDER_COLUMN, "text", "YES", True),
        )

    @staticmethod
    def _rows_without_generation(connection) -> tuple[tuple[object, ...], ...]:
        return tuple(
            connection.execute(
                f"""
                SELECT ctid::text, rotation_id, workspace_id, gateway_node_id,
                       purpose, issuer, old_key_id, status, version,
                       requested_at, intent_fingerprint
                FROM {_TABLE}
                ORDER BY rotation_id
                """
            ).fetchall()
        )

    @staticmethod
    def _rows_with_generation(connection) -> tuple[tuple[object, ...], ...]:
        return tuple(
            connection.execute(
                f"""
                SELECT ctid::text, rotation_id, {_PROVIDER_COLUMN}, {_DIGEST_COLUMN}
                FROM {_TABLE}
                ORDER BY rotation_id
                """
            ).fetchall()
        )

    @staticmethod
    def _target_constraint_identities(connection) -> dict[str, tuple[int, str, bool]]:
        return {
            name: (oid, definition, validated)
            for name, oid, definition, validated in connection.execute(
                """
                SELECT constraints.conname, constraints.oid,
                       pg_get_constraintdef(constraints.oid, false),
                       constraints.convalidated
                FROM pg_constraint AS constraints
                JOIN pg_class AS relation ON relation.oid = constraints.conrelid
                JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace
                WHERE namespace.nspname = current_schema()
                  AND relation.relname = 'cpk_gateway_key_rotations'
                  AND constraints.conname IN (
                    'cpk_gateway_key_rotations_generation_checkpoint_check',
                    'cpk_gateway_key_rotations_generation_provider_check',
                    'cpk_gateway_key_rotations_generation_digest_check'
                  )
                ORDER BY constraints.conname, constraints.oid
                """
            ).fetchall()
        }

    def _named_constraint_identities(
        self, connection
    ) -> dict[tuple[str, str, str], tuple[int, str]]:
        return {
            (schema, table, name): (oid, definition)
            for schema, table, name, oid, definition in connection.execute(
                """
                SELECT namespace.nspname, relation.relname, constraints.conname,
                       constraints.oid, pg_get_constraintdef(constraints.oid, false)
                FROM pg_constraint AS constraints
                JOIN pg_class AS relation ON relation.oid = constraints.conrelid
                JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace
                WHERE constraints.conname = ANY(%s)
                  AND namespace.nspname IN (%s, %s)
                  AND NOT (
                    namespace.nspname = %s
                    AND relation.relname = 'cpk_gateway_key_rotations'
                  )
                ORDER BY namespace.nspname, relation.relname, constraints.conname
                """,
                (
                    list(_TARGET_CONSTRAINTS),
                    self.schema,
                    f"{self.schema}_other",
                    self.schema,
                ),
            ).fetchall()
        }

    @staticmethod
    def _unrelated_objects(connection) -> dict[tuple[str, str], tuple[int, str]]:
        return {
            (kind, name): (oid, re.sub(r"\s+", " ", definition).strip())
            for kind, name, oid, definition in connection.execute(
                """
                SELECT 'constraint', constraints.conname, constraints.oid,
                       pg_get_constraintdef(constraints.oid, false)
                FROM pg_constraint AS constraints
                JOIN pg_class AS relation ON relation.oid = constraints.conrelid
                JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace
                WHERE namespace.nspname = current_schema()
                  AND relation.relname = 'cpk_gateway_key_rotations'
                  AND constraints.conname <> ALL(%s)
                UNION ALL
                SELECT 'index', indexes.relname, indexes.oid,
                       pg_get_indexdef(indexes.oid)
                FROM pg_class AS indexes
                JOIN pg_index AS index_contract ON index_contract.indexrelid = indexes.oid
                JOIN pg_class AS relation ON relation.oid = index_contract.indrelid
                JOIN pg_namespace AS namespace ON namespace.oid = indexes.relnamespace
                WHERE namespace.nspname = current_schema()
                  AND relation.relname = 'cpk_gateway_key_rotations'
                  AND indexes.relkind = 'i'
                ORDER BY 1, 2
                """,
                (list(_TARGET_CONSTRAINTS),),
            ).fetchall()
        }

    def _complete_snapshot(self, connection) -> tuple[object, ...]:
        columns = self._column_order(connection)
        rows = (
            self._rows_with_generation(connection)
            if _PROVIDER_COLUMN in columns and _DIGEST_COLUMN in columns
            else self._rows_without_generation(connection)
        )
        return (
            self._history(connection),
            columns,
            rows,
            self._target_constraint_identities(connection),
            self._unrelated_objects(connection),
        )

    @staticmethod
    def _assert_bounded_v12_error(error: Exception, marker: str = "") -> None:
        message = str(error)
        if message != _CATEGORICAL_ERROR:
            raise AssertionError(f"unexpected bounded category: {message!r}")
        if len(message) > 256:
            raise AssertionError("migration error is not bounded")
        for excluded in (
            marker,
            _VALID_PROVIDER,
            _VALID_DIGEST,
            "rotation-",
            "workspace-",
            "gateway-",
            "SELECT",
            "ALTER TABLE",
            "postgresql://",
        ):
            if excluded and excluded in message:
                raise AssertionError("migration error contains forbidden material")
        if error.__context__ is not None or error.__cause__ is not None:
            raise AssertionError("migration error retains provider context")

    @staticmethod
    def _v12():
        migration = postgres.POSTGRES_SCHEMA_MIGRATIONS.migrations[-1]
        if (migration.version, migration.name) != _V12_IDENTITY:
            raise AssertionError("V12 generation-evidence migration is missing")
        return migration


if __name__ == "__main__":
    unittest.main()
