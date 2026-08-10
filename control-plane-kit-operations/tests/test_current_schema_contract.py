from __future__ import annotations

import ast
import dataclasses
import hashlib
import importlib
import json
import os
from pathlib import Path
import unittest
import uuid

import psycopg

import control_plane_kit_operations.postgres as postgres
from control_plane_kit_operations.postgres import migration_inspection


_EXPECTED_COUNTS = {
    "relations": 29,
    "columns": 357,
    "constraints": 232,
    "indexes": 78,
    "history": 17,
}
_EXPECTED_CONSTRAINT_KINDS = {"c": 132, "f": 47, "p": 29, "u": 24}
_EXPECTED_LOCK_MODES = {
    "ACCESS EXCLUSIVE",
    "SHARE",
    "SHARE UPDATE EXCLUSIVE",
}
_FORBIDDEN_CONTRACT_IMPORTS = {
    "control_plane_kit_operations.postgres.schema",
    "control_plane_kit_operations.gateway_key_rotations",
    "control_plane_kit_core",
    "jinja2",
}


def _canonical_value(value: object) -> object:
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {
            "type": type(value).__name__,
            "fields": [
                (field.name, _canonical_value(getattr(value, field.name)))
                for field in dataclasses.fields(value)
            ],
        }
    if isinstance(value, tuple):
        return [_canonical_value(item) for item in value]
    if isinstance(value, (str, int, bool)) or value is None:
        return value
    raise AssertionError(f"contract value is not canonical: {type(value).__name__}")


def _contract_module():
    try:
        return importlib.import_module(
            "control_plane_kit_operations.postgres.current_schema_contract"
        )
    except ModuleNotFoundError:
        raise AssertionError("current schema contract is not implemented") from None


class CurrentSchemaContractValueTests(unittest.TestCase):
    def test_contract_is_frozen_complete_sorted_and_digest_pinned(self) -> None:
        module = _contract_module()
        contract = module.CURRENT_POSTGRES_SCHEMA_CONTRACT

        self.assertTrue(dataclasses.is_dataclass(contract))
        self.assertTrue(contract.__dataclass_params__.frozen)
        self.assertFalse(hasattr(contract, "__dict__"))
        for name, count in _EXPECTED_COUNTS.items():
            values = getattr(contract, name)
            self.assertIsInstance(values, tuple)
            self.assertEqual(len(values), count, name)
            self.assertEqual(values, tuple(sorted(values)), name)
            self.assertEqual(len(set(values)), count, name)

        kinds: dict[str, int] = {}
        for constraint in contract.constraints:
            kinds[constraint.kind] = kinds.get(constraint.kind, 0) + 1
        self.assertEqual(kinds, _EXPECTED_CONSTRAINT_KINDS)
        self.assertEqual(
            sum(index.owning_constraint is not None for index in contract.indexes),
            53,
        )
        self.assertEqual(
            sum(index.owning_constraint is None for index in contract.indexes),
            25,
        )

        payload = json.dumps(
            _canonical_value(contract),
            ensure_ascii=True,
            separators=(",", ":"),
        ).encode("ascii")
        digest = hashlib.sha256(payload).hexdigest()
        self.assertRegex(module.CURRENT_POSTGRES_SCHEMA_CONTRACT_SHA256, r"^[0-9a-f]{64}$")
        self.assertEqual(module.CURRENT_POSTGRES_SCHEMA_CONTRACT_SHA256, digest)

    def test_contract_records_cover_every_semantic_field(self) -> None:
        module = _contract_module()
        expected_fields = {
            "RelationContract": {
                "name",
                "kind",
                "persistence",
                "access_method",
                "replica_identity",
                "is_partition",
                "row_security",
                "force_row_security",
                "non_internal_triggers",
                "policies",
                "user_rules",
            },
            "ColumnContract": {
                "relation",
                "name",
                "type_namespace",
                "formatted_type",
                "not_null",
                "identity",
                "generated",
                "collation_namespace",
                "collation_name",
                "default_expression",
            },
            "ConstraintContract": {
                "relation",
                "name",
                "kind",
                "validated",
                "deferrable",
                "deferred",
                "no_inherit",
                "local_columns",
                "referenced_relation",
                "referenced_columns",
                "update_action",
                "delete_action",
                "match_type",
                "check_expression",
            },
            "IndexContract": {
                "relation",
                "name",
                "owning_constraint",
                "access_method",
                "unique",
                "primary",
                "valid",
                "ready",
                "live",
                "immediate",
                "clustered",
                "replica_identity",
                "nulls_not_distinct",
                "key_entries",
                "include_entries",
                "opclasses",
                "collations",
                "options",
                "predicate",
                "expressions",
            },
            "MigrationIdentity": {"version", "name", "checksum_sha256"},
            "RelationLock": {"relation", "mode"},
            "SchemaLockPlan": {"path", "relations"},
        }
        for name, fields in expected_fields.items():
            with self.subTest(name=name):
                record = getattr(module, name, None)
                self.assertIsNotNone(record, f"{name} is not implemented")
                self.assertTrue(dataclasses.is_dataclass(record))
                self.assertTrue(record.__dataclass_params__.frozen)
                self.assertEqual(
                    {field.name for field in dataclasses.fields(record)},
                    fields,
                )
                self.assertIn("__slots__", record.__dict__)

    def test_contract_module_is_independent_from_renderer_and_live_enums(self) -> None:
        module = _contract_module()
        source = Path(module.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)
        imports: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module is not None:
                imports.add(node.module)
        for forbidden in _FORBIDDEN_CONTRACT_IMPORTS:
            with self.subTest(forbidden=forbidden):
                self.assertFalse(
                    any(
                        imported == forbidden
                        or imported.startswith(f"{forbidden}.")
                        for imported in imports
                    )
                )
        self.assertNotIn("Environment(", source)
        self.assertNotIn("_POSTGRES_SCHEMA_RENDERER", source)
        self.assertNotIn("POSTGRES_SCHEMA_MIGRATIONS", source)

    def test_lock_plans_are_complete_canonical_and_path_specific(self) -> None:
        module = _contract_module()
        contract = module.CURRENT_POSTGRES_SCHEMA_CONTRACT
        relation_names = tuple(relation.name for relation in contract.relations)
        current = module.CURRENT_SCHEMA_LOCK_PLAN
        pending = module.PENDING_SCHEMA_LOCK_PLAN

        self.assertEqual(current.path, "current")
        self.assertEqual(pending.path, "pending")
        for plan in (current, pending):
            with self.subTest(path=plan.path):
                self.assertEqual(len(plan.relations), 29)
                self.assertEqual(
                    tuple(item.relation for item in plan.relations),
                    relation_names,
                )
                self.assertEqual(len(set(plan.relations)), 29)
        self.assertEqual({item.mode for item in pending.relations}, {"ACCESS EXCLUSIVE"})
        self.assertEqual({item.mode for item in current.relations}, _EXPECTED_LOCK_MODES)
        self.assertEqual(
            sum(item.mode == "ACCESS EXCLUSIVE" for item in current.relations),
            7,
        )
        self.assertEqual(sum(item.mode == "SHARE" for item in current.relations), 18)
        self.assertEqual(
            sum(item.mode == "SHARE UPDATE EXCLUSIVE" for item in current.relations),
            4,
        )


class CurrentSchemaContractIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        database_url = os.environ.get("CPK_OPERATIONS_TEST_DATABASE_URL")
        if not database_url:
            raise RuntimeError(
                "CPK_OPERATIONS_TEST_DATABASE_URL is required. Run the "
                "Docker-first operations test harness."
            )
        self.database_url = database_url
        self.schema = f"current_schema_contract_{uuid.uuid4().hex}"
        self.admin = psycopg.connect(database_url, autocommit=True)
        self.admin.execute(f'CREATE SCHEMA "{self.schema}"')

    def tearDown(self) -> None:
        self.admin.execute(f'DROP SCHEMA IF EXISTS "{self.schema}" CASCADE')
        self.admin.close()

    def test_arbitrary_physical_reorder_is_semantic_only_for_versioned_current(self) -> None:
        connection = self._connection()
        try:
            postgres.install_postgres_schema(connection)
            self._reorder_workspace_name(connection)

            observed = postgres.verify_postgres_schema(connection)

            self.assertIs(observed.kind, postgres.ObservedSchemaKind.VERSIONED)
        finally:
            connection.close()

        no_ledger = self._connection(reset=True)
        try:
            no_ledger.execute(postgres.POSTGRES_SCHEMA)
            self._reorder_workspace_name(no_ledger)

            with self.assertRaises(postgres.SchemaMigrationError):
                postgres.inspect_postgres_schema(no_ledger)
        finally:
            no_ledger.close()

    def test_current_contract_rejects_relation_column_constraint_and_index_drift(
        self,
    ) -> None:
        arrangements = (
            (
                "unlogged",
                "ALTER TABLE cpk_activity_events SET UNLOGGED",
            ),
            (
                "row-security",
                "ALTER TABLE cpk_activity_events ENABLE ROW LEVEL SECURITY",
            ),
            (
                "policy",
                "CREATE POLICY cpk_test_policy ON cpk_activity_events USING (true)",
            ),
            (
                "column-type",
                "ALTER TABLE cpk_workspaces ALTER COLUMN name TYPE varchar(100)",
            ),
            (
                "column-default",
                "ALTER TABLE cpk_workspaces ALTER COLUMN name SET DEFAULT ''",
            ),
            (
                "column-nullability",
                "ALTER TABLE cpk_workspaces ALTER COLUMN name DROP NOT NULL",
            ),
            (
                "extra-check",
                "ALTER TABLE cpk_workspaces ADD CONSTRAINT cpk_test_check "
                "CHECK (workspace_id <> '')",
            ),
            (
                "extra-index",
                "CREATE INDEX cpk_test_index ON cpk_workspaces (name)",
            ),
            (
                "clustered-index",
                "CLUSTER cpk_workspaces USING cpk_workspaces_pkey",
            ),
            (
                "replica-index",
                "ALTER TABLE cpk_workspaces REPLICA IDENTITY USING INDEX "
                "cpk_workspaces_pkey",
            ),
        )
        for label, mutation in arrangements:
            with self.subTest(label=label):
                connection = self._connection(reset=True)
                try:
                    postgres.install_postgres_schema(connection)
                    connection.execute(mutation)

                    with self.assertRaises(postgres.SchemaMigrationError):
                        postgres.verify_postgres_schema(connection)
                finally:
                    connection.close()

    def test_current_contract_rejects_trigger_rule_and_null_uniqueness_drift(
        self,
    ) -> None:
        connection = self._connection()
        try:
            postgres.install_postgres_schema(connection)
            connection.execute(
                "CREATE FUNCTION cpk_test_trigger() RETURNS trigger LANGUAGE plpgsql "
                "AS 'BEGIN RETURN NEW; END'"
            )
            connection.execute(
                "CREATE TRIGGER cpk_test_trigger BEFORE UPDATE ON cpk_activity_events "
                "FOR EACH ROW EXECUTE FUNCTION cpk_test_trigger()"
            )
            with self.assertRaises(postgres.SchemaMigrationError):
                postgres.verify_postgres_schema(connection)

            connection.execute("DROP TRIGGER cpk_test_trigger ON cpk_activity_events")
            connection.execute(
                "CREATE RULE cpk_test_rule AS ON UPDATE TO cpk_activity_events "
                "DO INSTEAD NOTHING"
            )
            with self.assertRaises(postgres.SchemaMigrationError):
                postgres.verify_postgres_schema(connection)

            connection.execute("DROP RULE cpk_test_rule ON cpk_activity_events")
            connection.execute("DROP INDEX cpk_approval_requests_rotation_identity")
            connection.execute(
                "CREATE UNIQUE INDEX cpk_approval_requests_rotation_identity "
                "ON cpk_approval_requests (rotation_id) NULLS NOT DISTINCT "
                "WHERE rotation_id IS NOT NULL"
            )
            with self.assertRaises(postgres.SchemaMigrationError):
                postgres.verify_postgres_schema(connection)
        finally:
            connection.close()

    def test_fresh_and_current_paths_predeclare_complete_relation_locks(self) -> None:
        module = _contract_module()
        fresh = self._connection(autocommit=False)
        try:
            postgres.install_postgres_schema(fresh)
            locks = self._relation_locks(fresh)
            self.assertEqual(
                {relation.name for relation in module.PENDING_SCHEMA_LOCK_PLAN.relations},
                set(locks),
            )
            for relation in module.PENDING_SCHEMA_LOCK_PLAN.relations:
                self.assertIn("AccessExclusiveLock", locks[relation.relation])
            fresh.commit()
        finally:
            fresh.rollback()
            fresh.close()

        current = self._connection(autocommit=False)
        try:
            postgres.install_postgres_schema(current)
            current.commit()
            postgres.install_postgres_schema(current)
            locks = self._relation_locks(current)
            for relation in module.CURRENT_SCHEMA_LOCK_PLAN.relations:
                self.assertIn(
                    self._postgres_lock_mode(relation.mode),
                    locks[relation.relation],
                )
        finally:
            current.rollback()
            current.close()

    def test_catalog_interpreter_is_single_bounded_predeparse_statement(self) -> None:
        verifier = getattr(
            migration_inspection,
            "_verify_current_schema_contract",
            None,
        )
        self.assertIsNotNone(verifier, "current schema interpreter is not implemented")

        class Result:
            def fetchall(self):
                return [(True, True, True, True, True)]

        class RecordingConnection:
            calls: list[tuple[str, tuple[object, ...]]] = []

            def execute(self, query, params=()):
                self.calls.append((query, params))
                return Result()

        connection = RecordingConnection()
        verifier(connection)

        self.assertEqual(len(connection.calls), 1)
        query, parameters = connection.calls[0]
        normalized = " ".join(query.split())
        self.assertGreaterEqual(normalized.count("MATERIALIZED"), 4)
        self.assertIn("LIMIT", normalized)
        self.assertLess(normalized.index("LIMIT"), normalized.index("pg_get_expr"))
        self.assertLess(normalized.index("LIMIT"), normalized.index("pg_get_indexdef"))
        self.assertNotIn("pg_get_constraintdef", normalized)
        self.assertTrue(parameters)

    def _connection(self, *, reset: bool = False, autocommit: bool = True):
        if reset:
            self.admin.execute(f'DROP SCHEMA IF EXISTS "{self.schema}" CASCADE')
            self.admin.execute(f'CREATE SCHEMA "{self.schema}"')
        connection = psycopg.connect(self.database_url, autocommit=autocommit)
        connection.execute(f'SET search_path TO "{self.schema}"')
        if not autocommit:
            connection.commit()
        return connection

    @staticmethod
    def _reorder_workspace_name(connection) -> None:
        connection.execute("ALTER TABLE cpk_workspaces DROP COLUMN name")
        connection.execute("ALTER TABLE cpk_workspaces ADD COLUMN name text NOT NULL")

    @staticmethod
    def _relation_locks(connection) -> dict[str, set[str]]:
        rows = connection.execute(
            """
            SELECT relation.relname, lock.mode
            FROM pg_locks AS lock
            JOIN pg_class AS relation ON relation.oid = lock.relation
            JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace
            WHERE lock.pid = pg_backend_pid()
              AND namespace.nspname = current_schema()
              AND relation.relkind = 'r'
            ORDER BY relation.relname, lock.mode
            """
        ).fetchall()
        locks: dict[str, set[str]] = {}
        for relation, mode in rows:
            locks.setdefault(relation, set()).add(mode)
        return locks

    @staticmethod
    def _postgres_lock_mode(mode: str) -> str:
        return "".join(part.title() for part in mode.split()) + "Lock"


if __name__ == "__main__":
    unittest.main()
