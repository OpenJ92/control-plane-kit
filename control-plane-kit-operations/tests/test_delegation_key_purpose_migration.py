from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import os
import threading
import time
import unittest
import uuid

import psycopg
from psycopg import errors

from control_plane_kit_core.delegation_keys import DelegationKeyPurpose
import control_plane_kit_operations.postgres as postgres
from control_plane_kit_operations.postgres import migration_inspection
from control_plane_kit_operations.postgres import migration_runner


_V17_IDENTITY = (17, "graph-lineage-compatibility")
_V18_IDENTITY = (18, "delegation-key-surface-read-purpose")
_V1_SHA256 = "fc9b5547fc51ec681130c41facea785dbd24649049417455b184ea05886beed8"
_V15_SHA256 = "215c6a71efd06f699c1d988a7e55435920075726009f030eecbd4a8c0fd91a0b"
_V18_SHA256 = "9f47d96f3b866cf88489f254f422108ee4a4685f22fc45599db0223d4bf9d3b4"
_V18_STEP_SHA256 = (
    "ce046b4dc957d5be8934f6bcbe31d16f4b7f33a10f56873eca89709bdafbe92a",
    "29df66419ae9d98058347bb2b3b1262dd5d78b4d7405c129e96c42bb41aeee88",
)
_ERROR = "delegation key purpose contract is not accepted"
_OLD_PURPOSES = ("gateway-probe", "workload-node-control")
_CURRENT_PURPOSES = (*_OLD_PURPOSES, "workload-node-control-surface-read")
_TARGETS = (
    (
        "cpk_delegation_signing_keys",
        "cpk_delegation_signing_keys_purpose_check",
    ),
    (
        "cpk_gateway_key_rotations",
        "cpk_gateway_key_rotations_purpose_check",
    ),
)


def _definition(purposes: tuple[str, ...]) -> str:
    values = ", ".join(f"'{purpose}'::text" for purpose in purposes)
    return f"CHECK ((purpose = ANY (ARRAY[{values}])))"


class DelegationKeyPurposeMigrationTests(unittest.TestCase):
    def setUp(self) -> None:
        database_url = os.environ.get("CPK_OPERATIONS_TEST_DATABASE_URL")
        if not database_url:
            raise RuntimeError(
                "CPK_OPERATIONS_TEST_DATABASE_URL is required; use Docker-first tests"
            )
        self.database_url = database_url
        self.schema = f"dkeypurpose_{uuid.uuid4().hex}"
        self.other_schema = f"{self.schema}_other"
        self.admin = psycopg.connect(database_url, autocommit=True)
        self.admin.execute(f'CREATE SCHEMA "{self.schema}"')

    def tearDown(self) -> None:
        self.admin.execute(f'DROP SCHEMA IF EXISTS "{self.other_schema}" CASCADE')
        self.admin.execute(f'DROP SCHEMA IF EXISTS "{self.schema}" CASCADE')
        self.admin.close()

    def test_registry_freezes_history_and_appends_exact_two_step_v18(self) -> None:
        purpose = getattr(
            DelegationKeyPurpose,
            "WORKLOAD_NODE_CONTROL_SURFACE_READ",
            None,
        )
        self.assertIsNotNone(purpose)
        self.assertEqual(
            tuple(item.value for item in DelegationKeyPurpose),
            _CURRENT_PURPOSES,
        )
        registry = postgres.POSTGRES_SCHEMA_MIGRATIONS
        self.assertEqual(registry.target_version, 18)
        self.assertEqual(
            tuple((item.version, item.name) for item in registry.migrations[-2:]),
            (_V17_IDENTITY, _V18_IDENTITY),
        )
        self.assertEqual(registry.migrations[0].checksum_sha256, _V1_SHA256)
        self.assertEqual(registry.migrations[14].checksum_sha256, _V15_SHA256)

        migration = registry.migrations[17]
        self.assertIsNone(migration.sql)
        self.assertEqual(len(migration.steps), 2)
        self.assertTrue(
            all(type(step) is postgres.SqlMigrationStep for step in migration.steps)
        )
        self.assertEqual(migration.checksum_sha256, _V18_SHA256)
        self.assertEqual(
            tuple(step.checksum_sha256 for step in migration.steps),
            _V18_STEP_SHA256,
        )
        preflight, convergence = (step.sql for step in migration.steps)
        self.assertLess(
            preflight.index(
                "LOCK TABLE cpk_delegation_signing_keys IN ACCESS EXCLUSIVE MODE;"
            ),
            preflight.index(
                "LOCK TABLE cpk_gateway_key_rotations IN ACCESS EXCLUSIVE MODE;"
            ),
        )
        self.assertNotIn("ALTER TABLE", preflight)
        self.assertIn("information_schema.columns", preflight)
        self.assertIn("pg_get_constraintdef", preflight)
        self.assertIn("count(DISTINCT constraints.oid)", preflight)
        self.assertIn(_definition(_OLD_PURPOSES).replace("'", "''"), preflight)
        self.assertIn(_ERROR, preflight)
        for relation, constraint in _TARGETS:
            self.assertIn(f"ALTER TABLE {relation}", convergence)
            self.assertIn(f"DROP CONSTRAINT {constraint}", convergence)
            self.assertIn(f"ADD CONSTRAINT {constraint}", convergence)
        self.assertIn(
            ", ".join(f"'{purpose}'" for purpose in _CURRENT_PURPOSES),
            convergence,
        )

    def test_v17_upgrade_preserves_rows_and_replaces_only_target_constraints(
        self,
    ) -> None:
        connection = self._connection()
        try:
            self._prepare_v17(connection)
            self._seed_rows(connection)
            rows_before = self._retained_rows(connection)
            constraints_before = self._constraints(connection)

            postgres.install_postgres_schema(connection)

            constraints_after = self._constraints(connection)
            self.assertEqual(self._retained_rows(connection), rows_before)
            self.assertEqual(self._history(connection)[-1][:2], _V18_IDENTITY)
            target_keys = set(_TARGETS)
            self.assertEqual(
                {
                    key: value
                    for key, value in constraints_after.items()
                    if key not in target_keys
                },
                {
                    key: value
                    for key, value in constraints_before.items()
                    if key not in target_keys
                },
            )
            for key in target_keys:
                self.assertNotEqual(
                    constraints_after[key][0],
                    constraints_before[key][0],
                )
                self.assertEqual(constraints_after[key][1], _definition(_CURRENT_PURPOSES))
        finally:
            connection.close()

    def test_fresh_install_admits_exact_new_purpose_and_reinstall_is_read_only(
        self,
    ) -> None:
        connection = self._connection()
        try:
            postgres.install_postgres_schema(connection)
            self._seed_workspace(connection)
            self._insert_key(
                connection,
                "workload-node-control-surface-read",
                "c",
            )
            self._insert_rotation(
                connection,
                "workload-node-control-surface-read",
                "c",
            )
            before = (
                self._retained_rows(connection),
                self._constraints(connection),
                self._history(connection),
            )

            postgres.install_postgres_schema(connection)

            self.assertEqual(
                (
                    self._retained_rows(connection),
                    self._constraints(connection),
                    self._history(connection),
                ),
                before,
            )
            for table in ("cpk_delegation_signing_keys", "cpk_gateway_key_rotations"):
                with self.subTest(table=table):
                    with self.assertRaises(errors.CheckViolation):
                        connection.execute(
                            f"UPDATE {table} SET purpose = %s",
                            ("private-unknown-purpose",),
                        )
        finally:
            connection.close()

    def test_missing_wrong_unvalidated_or_wrong_column_contract_rejects_before_v18(
        self,
    ) -> None:
        cases = ("missing", "wrong", "unvalidated", "column-default")
        for relation, constraint in _TARGETS:
            for case in cases:
                with self.subTest(relation=relation, case=case):
                    self._reset_schema()
                    connection = self._connection()
                    try:
                        self._prepare_v17(connection)
                        connection.execute(
                            f"ALTER TABLE {relation} DROP CONSTRAINT {constraint}"
                        )
                        if case == "wrong":
                            connection.execute(
                                f"ALTER TABLE {relation} ADD CONSTRAINT {constraint} "
                                "CHECK (purpose IN ('gateway-probe', "
                                "'workload-node-control', 'private-drift-material'))"
                            )
                        elif case == "unvalidated":
                            connection.execute(
                                f"ALTER TABLE {relation} ADD CONSTRAINT {constraint} "
                                "CHECK (purpose IN ('gateway-probe', "
                                "'workload-node-control')) NOT VALID"
                            )
                        elif case == "column-default":
                            connection.execute(
                                f"ALTER TABLE {relation} ADD CONSTRAINT {constraint} "
                                "CHECK (purpose IN ('gateway-probe', "
                                "'workload-node-control'))"
                            )
                            connection.execute(
                                f"ALTER TABLE {relation} ALTER COLUMN purpose "
                                "SET DEFAULT 'gateway-probe'"
                            )
                        before = self._snapshot(connection)

                        with self.assertRaisesRegex(
                            postgres.SchemaMigrationError,
                            f"^{_ERROR}$",
                        ) as raised:
                            postgres.install_postgres_schema(connection)

                        self.assertIsNone(raised.exception.__context__)
                        self.assertIsNone(raised.exception.__cause__)
                        self.assertNotIn("private-drift-material", repr(raised.exception))
                        self.assertEqual(self._snapshot(connection), before)
                        self.assertEqual(self._history(connection)[-1][:2], _V17_IDENTITY)
                    finally:
                        connection.close()

    def test_cross_schema_lookalikes_are_ignored_and_preserved(self) -> None:
        connection = self._connection()
        try:
            self._prepare_v17(connection)
            self.admin.execute(f'CREATE SCHEMA "{self.other_schema}"')
            for relation, constraint in _TARGETS:
                self.admin.execute(
                    f'CREATE TABLE "{self.other_schema}".{relation} '
                    "(purpose text NOT NULL)"
                )
                self.admin.execute(
                    f'ALTER TABLE "{self.other_schema}".{relation} '
                    f"ADD CONSTRAINT {constraint} CHECK (purpose = 'lookalike')"
                )
            lookalikes_before = self._other_constraints()

            postgres.install_postgres_schema(connection)

            self.assertEqual(self._other_constraints(), lookalikes_before)
        finally:
            connection.close()

    def test_convergence_failure_and_caller_rollback_restore_exact_v17(self) -> None:
        for mode in ("convergence", "final-verification", "caller-rollback"):
            with self.subTest(mode=mode):
                self._reset_schema()
                connection = self._connection(autocommit=mode != "caller-rollback")
                try:
                    self._prepare_v17(connection)
                    self._seed_rows(connection)
                    if not connection.autocommit:
                        connection.commit()
                    before = self._snapshot(connection)
                    migration = postgres.POSTGRES_SCHEMA_MIGRATIONS.migrations[17]

                    if mode == "caller-rollback":
                        postgres.install_postgres_schema(connection)
                        self.assertEqual(self._history(connection)[-1][:2], _V18_IDENTITY)
                        connection.rollback()
                    else:
                        wrapper = _FailingConnection(
                            connection,
                            migration.steps[1].sql if mode == "convergence" else None,
                            mode == "final-verification",
                        )
                        expected = (
                            _ERROR
                            if mode == "convergence"
                            else "database schema contract is not current"
                        )
                        with self.assertRaisesRegex(
                            postgres.SchemaMigrationError,
                            f"^{expected}$",
                        ) as raised:
                            postgres.install_postgres_schema(wrapper)
                        self.assertIsNone(raised.exception.__context__)
                        self.assertIsNone(raised.exception.__cause__)

                    self.assertEqual(self._snapshot(connection), before)
                finally:
                    connection.rollback()
                    connection.close()

    def test_migration_local_locks_block_writers_to_both_owned_relations(self) -> None:
        setup = self._connection()
        owner = self._connection(autocommit=False)
        contenders = [self._connection() for _target in _TARGETS]
        try:
            self._prepare_v17(setup)
            self._seed_rows(setup)
            migration_runner._apply_schema_migration(
                owner,
                postgres.POSTGRES_SCHEMA_MIGRATIONS.migrations[17],
            )
            started = [threading.Event(), threading.Event()]

            def write(index: int) -> None:
                relation = _TARGETS[index][0]
                started[index].set()
                contenders[index].execute(
                    f"UPDATE {relation} SET purpose = purpose"
                )

            with ThreadPoolExecutor(max_workers=2) as executor:
                futures = tuple(executor.submit(write, index) for index in range(2))
                self.assertTrue(all(event.wait(timeout=2) for event in started))
                time.sleep(0.1)
                self.assertTrue(all(not future.done() for future in futures))
                owner.rollback()
                for future in futures:
                    future.result(timeout=5)
        finally:
            owner.rollback()
            setup.close()
            owner.close()
            for contender in contenders:
                contender.close()

    def _prepare_v17(self, connection) -> None:
        connection.execute(postgres.POSTGRES_SCHEMA)
        for migration in postgres.POSTGRES_SCHEMA_MIGRATIONS.migrations[1:17]:
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
        for migration in postgres.POSTGRES_SCHEMA_MIGRATIONS.migrations[:17]:
            connection.execute(
                "INSERT INTO cpk_schema_migrations "
                "(version, name, checksum_sha256) VALUES (%s, %s, %s)",
                (migration.version, migration.name, migration.checksum_sha256),
            )

    def _seed_rows(self, connection) -> None:
        self._seed_workspace(connection)
        for purpose, suffix in zip(_OLD_PURPOSES, ("a", "b")):
            self._insert_key(connection, purpose, suffix)
            self._insert_rotation(connection, purpose, suffix)

    @staticmethod
    def _seed_workspace(connection) -> None:
        connection.execute(
            "INSERT INTO cpk_workspaces (workspace_id, name, lifecycle) "
            "VALUES ('workspace-a', 'Workspace A', 'created')"
        )

    @staticmethod
    def _insert_key(connection, purpose: str, suffix: str) -> None:
        connection.execute(
            """
            INSERT INTO cpk_delegation_signing_keys (
              registration_id, workspace_id, purpose, issuer, key_id, algorithm,
              public_key_pem, public_fingerprint_sha256, private_key_reference,
              admitted_by, admitted_at, status
            ) VALUES (%s, 'workspace-a', %s, 'cpk-server', %s, 'ed25519',
                      'bounded-public-test-material', %s, %s, 'operator-a',
                      '2026-08-10T00:00:00Z', 'verify-only')
            """,
            (
                "dkey_" + suffix * 64,
                purpose,
                f"key-{suffix}",
                suffix * 64,
                f"secret://workspace-secrets/keys/{suffix}",
            ),
        )

    @staticmethod
    def _insert_rotation(connection, purpose: str, suffix: str) -> None:
        connection.execute(
            """
            INSERT INTO cpk_gateway_key_rotations (
              rotation_id, workspace_id, gateway_node_id, purpose, issuer,
              old_key_id, new_secret_reference, key_generation_correlation,
              maximum_grant_lifetime_seconds, clock_skew_seconds,
              correlation_id, requested_by, requested_at, intent_fingerprint,
              status, version
            ) VALUES (%s, 'workspace-a', %s, %s, 'cpk-server', %s, %s, %s,
                      60, 5, %s, 'operator-a', '2026-08-10T00:00:00Z', %s,
                      'requested', 1)
            """,
            (
                f"rotation-{suffix}",
                f"gateway-{suffix}",
                purpose,
                f"key-{suffix}",
                f"secret://workspace-secrets/keys/new-{suffix}",
                f"generate-{suffix}",
                f"correlation-{suffix}",
                suffix * 64,
            ),
        )

    def _connection(self, *, autocommit: bool = True):
        connection = psycopg.connect(self.database_url, autocommit=autocommit)
        connection.execute(f'SET search_path TO "{self.schema}"')
        if not autocommit:
            connection.commit()
        return connection

    def _reset_schema(self) -> None:
        self.admin.execute(f'DROP SCHEMA IF EXISTS "{self.schema}" CASCADE')
        self.admin.execute(f'CREATE SCHEMA "{self.schema}"')

    @staticmethod
    def _history(connection):
        return tuple(
            connection.execute(
                "SELECT version, name, checksum_sha256, applied_at "
                "FROM cpk_schema_migrations ORDER BY version"
            ).fetchall()
        )

    @staticmethod
    def _retained_rows(connection):
        keys = tuple(
            connection.execute(
                "SELECT registration_id, workspace_id, purpose, issuer, key_id, "
                "admitted_at, status FROM cpk_delegation_signing_keys "
                "ORDER BY registration_id"
            ).fetchall()
        )
        rotations = tuple(
            connection.execute(
                "SELECT rotation_id, workspace_id, gateway_node_id, purpose, issuer, "
                "old_key_id, requested_at, status, version "
                "FROM cpk_gateway_key_rotations ORDER BY rotation_id"
            ).fetchall()
        )
        return keys, rotations

    @staticmethod
    def _constraints(connection):
        rows = connection.execute(
            """
            SELECT relation.relname, constraints.conname, constraints.oid,
                   pg_get_constraintdef(constraints.oid, false)
            FROM pg_constraint AS constraints
            JOIN pg_class AS relation ON relation.oid = constraints.conrelid
            JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace
            WHERE namespace.nspname = current_schema()
            ORDER BY relation.relname, constraints.conname, constraints.oid
            """
        ).fetchall()
        return {(row[0], row[1]): row[2:] for row in rows}

    def _other_constraints(self):
        return tuple(
            self.admin.execute(
                """
                SELECT relation.relname, constraints.conname, constraints.oid,
                       pg_get_constraintdef(constraints.oid, false)
                FROM pg_constraint AS constraints
                JOIN pg_class AS relation ON relation.oid = constraints.conrelid
                JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace
                WHERE namespace.nspname = %s
                ORDER BY relation.relname, constraints.conname, constraints.oid
                """,
                (self.other_schema,),
            ).fetchall()
        )

    def _snapshot(self, connection):
        columns = tuple(
            connection.execute(
                """
                SELECT table_name, column_name, data_type, is_nullable, column_default
                FROM information_schema.columns
                WHERE table_schema = current_schema()
                ORDER BY table_name, ordinal_position
                """
            ).fetchall()
        )
        return (
            self._history(connection),
            self._retained_rows(connection),
            tuple(sorted(self._constraints(connection).items())),
            columns,
        )


class _FailingConnection:
    def __init__(self, delegate, rejected_sql, reject_final: bool) -> None:
        self._delegate = delegate
        self._rejected_sql = rejected_sql
        self._reject_final = reject_final

    @property
    def autocommit(self):
        return self._delegate.autocommit

    def transaction(self):
        return self._delegate.transaction()

    def execute(self, query, params=()):
        if query == self._rejected_sql:
            raise _CategoricalSqlFailure("private convergence material")
        if self._reject_final and query == migration_inspection._CURRENT_SCHEMA_CONTRACT_QUERY:
            raise RuntimeError("private final verification material")
        return self._delegate.execute(query, params)


class _CategoricalSqlFailure(RuntimeError):
    sqlstate = "P1110"


if __name__ == "__main__":
    unittest.main()
