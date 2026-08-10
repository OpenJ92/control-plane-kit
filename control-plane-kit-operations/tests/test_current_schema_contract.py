from __future__ import annotations

import ast
import dataclasses
import hashlib
import importlib
import json
import os
from pathlib import Path
import threading
import time
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
_CONTRACT_DOMAIN = "control-plane-kit.operations.postgres.current-schema"
_CONTRACT_FORMAT_VERSION = 1
_EXPECTED_CONTRACT_SHA256 = (
    "db72f091f5da3cc6f2be6b359899a369c5e27a212b0d71afade1dc5e850d5300"
)
_EXPECTED_CONSTRAINT_KINDS = {"c": 132, "f": 47, "p": 29, "u": 24}
_CURRENT_LOCKS = {
    "cpk_activity_events": "SHARE UPDATE EXCLUSIVE",
    "cpk_activity_plans": "ACCESS EXCLUSIVE",
    "cpk_activity_runs": "SHARE",
    "cpk_approval_decisions": "SHARE",
    "cpk_approval_requests": "SHARE",
    "cpk_cloudflare_ingress_resources": "SHARE",
    "cpk_delegation_signing_keys": "SHARE",
    "cpk_execution_requests": "SHARE",
    "cpk_gateway_key_rotation_deployments": "SHARE UPDATE EXCLUSIVE",
    "cpk_gateway_key_rotation_revocations": "SHARE UPDATE EXCLUSIVE",
    "cpk_gateway_key_rotation_transitions": "SHARE UPDATE EXCLUSIVE",
    "cpk_gateway_key_rotations": "ACCESS EXCLUSIVE",
    "cpk_gateway_probe_attempts": "ACCESS EXCLUSIVE",
    "cpk_generated_ingress_secret_references": "SHARE",
    "cpk_graph_versions": "ACCESS EXCLUSIVE",
    "cpk_image_pull_authorities": "SHARE",
    "cpk_ingress_authorities": "SHARE",
    "cpk_observations": "SHARE",
    "cpk_operation_actions": "SHARE",
    "cpk_operation_sessions": "SHARE",
    "cpk_realized_graph_projections": "ACCESS EXCLUSIVE",
    "cpk_registered_products": "ACCESS EXCLUSIVE",
    "cpk_runtime_authorities": "SHARE",
    "cpk_runtime_authority_deliveries": "SHARE",
    "cpk_schema_migrations": "SHARE",
    "cpk_secret_providers": "SHARE",
    "cpk_secret_references": "SHARE",
    "cpk_secret_use_authorizations": "SHARE",
    "cpk_workspaces": "ACCESS EXCLUSIVE",
}
_ALLOWED_CONTRACT_IMPORTS = {
    "__future__",
    "dataclasses",
}


_CONFLICTS = {
    "AccessShareLock": frozenset({"AccessExclusiveLock"}),
    "RowShareLock": frozenset({"ExclusiveLock", "AccessExclusiveLock"}),
    "RowExclusiveLock": frozenset(
        {
            "ShareLock",
            "ShareRowExclusiveLock",
            "ExclusiveLock",
            "AccessExclusiveLock",
        }
    ),
    "ShareUpdateExclusiveLock": frozenset(
        {
            "ShareUpdateExclusiveLock",
            "ShareLock",
            "ShareRowExclusiveLock",
            "ExclusiveLock",
            "AccessExclusiveLock",
        }
    ),
    "ShareLock": frozenset(
        {
            "RowExclusiveLock",
            "ShareUpdateExclusiveLock",
            "ShareRowExclusiveLock",
            "ExclusiveLock",
            "AccessExclusiveLock",
        }
    ),
    "ShareRowExclusiveLock": frozenset(
        {
            "RowExclusiveLock",
            "ShareUpdateExclusiveLock",
            "ShareLock",
            "ShareRowExclusiveLock",
            "ExclusiveLock",
            "AccessExclusiveLock",
        }
    ),
    "ExclusiveLock": frozenset(
        {
            "RowShareLock",
            "RowExclusiveLock",
            "ShareUpdateExclusiveLock",
            "ShareLock",
            "ShareRowExclusiveLock",
            "ExclusiveLock",
            "AccessExclusiveLock",
        }
    ),
    "AccessExclusiveLock": frozenset(
        {
            "AccessShareLock",
            "RowShareLock",
            "RowExclusiveLock",
            "ShareUpdateExclusiveLock",
            "ShareLock",
            "ShareRowExclusiveLock",
            "ExclusiveLock",
            "AccessExclusiveLock",
        }
    ),
}


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


class _RecordingConnection:
    def __init__(self, delegate) -> None:
        self.delegate = delegate
        self.calls: list[tuple[str, tuple[object, ...]]] = []
        self.predeclared_locks: dict[str, set[str]] | None = None
        self.lock_call_index: int | None = None

    @property
    def autocommit(self):
        return self.delegate.autocommit

    def transaction(self):
        return self.delegate.transaction()

    def execute(self, query, params=()):
        text = query if isinstance(query, str) else str(query)
        self.calls.append((text, params))
        result = self.delegate.execute(query, params)
        if "LOCK TABLE ONLY" in text:
            if self.predeclared_locks is not None:
                raise AssertionError("schema lock plan was not acquired atomically")
            self.lock_call_index = len(self.calls) - 1
            self.predeclared_locks = _relation_locks(self.delegate)
        return result


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
            self.assertEqual(len(set(values)), count, name)

        self.assertEqual(
            tuple(relation.name for relation in contract.relations),
            tuple(sorted(relation.name for relation in contract.relations)),
        )
        for name in ("columns", "constraints", "indexes"):
            values = getattr(contract, name)
            self.assertEqual(
                tuple((value.relation, value.name) for value in values),
                tuple(sorted((value.relation, value.name) for value in values)),
                name,
            )
        self.assertEqual(
            tuple(identity.version for identity in contract.history),
            tuple(range(1, 18)),
        )
        self.assertEqual(
            tuple(
                (identity.version, identity.name, identity.checksum_sha256)
                for identity in contract.history
            ),
            tuple(
                (migration.version, migration.name, migration.checksum_sha256)
                for migration in postgres.POSTGRES_SCHEMA_MIGRATIONS.migrations
            ),
        )

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

        envelope = {
            "domain": _CONTRACT_DOMAIN,
            "format_version": _CONTRACT_FORMAT_VERSION,
            "contract": dataclasses.asdict(contract),
        }
        payload = json.dumps(
            envelope,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("ascii")
        digest = hashlib.sha256(payload).hexdigest()
        self.assertEqual(digest, _EXPECTED_CONTRACT_SHA256)
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
        self.assertLessEqual(imports, _ALLOWED_CONTRACT_IMPORTS)

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
        self.assertEqual(
            {item.relation: item.mode for item in current.relations},
            _CURRENT_LOCKS,
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
                "missing-relation",
                "DROP TABLE cpk_activity_events",
            ),
            (
                "extra-relation",
                "CREATE TABLE cpk_test_extra (id text)",
            ),
            (
                "extra-view",
                "CREATE VIEW cpk_test_view AS SELECT workspace_id "
                "FROM cpk_workspaces",
            ),
            (
                "unlogged",
                "ALTER TABLE cpk_activity_events SET UNLOGGED",
            ),
            (
                "row-security",
                "ALTER TABLE cpk_activity_events ENABLE ROW LEVEL SECURITY",
            ),
            (
                "forced-row-security",
                "ALTER TABLE cpk_activity_events FORCE ROW LEVEL SECURITY",
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
                "column-collation",
                'ALTER TABLE cpk_workspaces ALTER COLUMN name TYPE text COLLATE "C"',
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

                    self._assert_contract_not_current(connection)
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
            self._assert_contract_not_current(connection)

            connection.execute("DROP TRIGGER cpk_test_trigger ON cpk_activity_events")
            connection.execute(
                "CREATE RULE cpk_test_rule AS ON UPDATE TO cpk_activity_events "
                "DO INSTEAD NOTHING"
            )
            self._assert_contract_not_current(connection)

            connection.execute("DROP RULE cpk_test_rule ON cpk_activity_events")
            connection.execute("DROP INDEX cpk_approval_requests_rotation_identity")
            connection.execute(
                "CREATE UNIQUE INDEX cpk_approval_requests_rotation_identity "
                "ON cpk_approval_requests (rotation_id) NULLS NOT DISTINCT "
                "WHERE rotation_id IS NOT NULL"
            )
            self._assert_contract_not_current(connection)
        finally:
            connection.close()

    def test_current_contract_rejects_constraint_and_index_semantic_drift(
        self,
    ) -> None:
        arrangements = (
            (
                "check-no-inherit",
                (
                    "ALTER TABLE cpk_workspaces DROP CONSTRAINT "
                    "cpk_workspaces_lifecycle_check",
                    "ALTER TABLE cpk_workspaces ADD CONSTRAINT "
                    "cpk_workspaces_lifecycle_check CHECK (lifecycle = ANY "
                    "(ARRAY['created'::text, 'running'::text, 'paused'::text, "
                    "'stopped'::text, 'archived'::text, 'deconstructed'::text, "
                    "'deleted'::text, 'failed'::text])) NO INHERIT",
                ),
            ),
            (
                "foreign-key-action",
                (
                    "ALTER TABLE cpk_workspaces DROP CONSTRAINT "
                    "cpk_workspaces_current_realized_projection_fk",
                    "ALTER TABLE cpk_workspaces ADD CONSTRAINT "
                    "cpk_workspaces_current_realized_projection_fk FOREIGN KEY "
                    "(current_realized_projection_id, workspace_id) REFERENCES "
                    "cpk_realized_graph_projections(projection_id, workspace_id) "
                    "ON DELETE CASCADE",
                ),
            ),
            (
                "unique-deferrable",
                (
                    "ALTER TABLE cpk_realized_graph_projections DROP CONSTRAINT "
                    "cpk_realized_graph_projection_workspace_identity CASCADE",
                    "ALTER TABLE cpk_realized_graph_projections ADD CONSTRAINT "
                    "cpk_realized_graph_projection_workspace_identity UNIQUE "
                    "(projection_id, workspace_id) DEFERRABLE",
                ),
            ),
            (
                "index-key-order",
                (
                    "DROP INDEX cpk_approval_requests_idempotency",
                    "CREATE UNIQUE INDEX cpk_approval_requests_idempotency ON "
                    "cpk_approval_requests (idempotency_key, session_id) WHERE "
                    "idempotency_key IS NOT NULL",
                ),
            ),
            (
                "index-predicate",
                (
                    "DROP INDEX cpk_approval_requests_rotation_identity",
                    "CREATE UNIQUE INDEX cpk_approval_requests_rotation_identity "
                    "ON cpk_approval_requests (rotation_id) WHERE rotation_id IS NULL",
                ),
            ),
            (
                "index-opclass-collation",
                (
                    "DROP INDEX cpk_approval_requests_rotation_identity",
                    "CREATE UNIQUE INDEX cpk_approval_requests_rotation_identity "
                    "ON cpk_approval_requests "
                    '((rotation_id COLLATE "C") text_pattern_ops) '
                    "WHERE rotation_id IS NOT NULL",
                ),
            ),
            (
                "index-expression",
                (
                    "DROP INDEX cpk_approval_requests_rotation_identity",
                    "CREATE UNIQUE INDEX cpk_approval_requests_rotation_identity "
                    "ON cpk_approval_requests (lower(rotation_id)) WHERE "
                    "rotation_id IS NOT NULL",
                ),
            ),
        )
        for label, statements in arrangements:
            with self.subTest(label=label):
                connection = self._connection(reset=True)
                try:
                    postgres.install_postgres_schema(connection)
                    for statement in statements:
                        connection.execute(statement)
                    self._assert_contract_not_current(connection)
                finally:
                    connection.close()

    def test_cross_schema_lookalikes_cannot_satisfy_missing_owned_truth(self) -> None:
        connection = self._connection()
        shadow = f"{self.schema}_shadow"
        try:
            postgres.install_postgres_schema(connection)
            connection.execute(f'CREATE SCHEMA "{shadow}"')
            connection.execute(
                f'CREATE TABLE "{shadow}".cpk_approval_requests '
                "(rotation_id text)"
            )
            connection.execute(
                f'CREATE UNIQUE INDEX cpk_approval_requests_rotation_identity '
                f'ON "{shadow}".cpk_approval_requests (rotation_id) '
                "WHERE rotation_id IS NOT NULL"
            )
            connection.execute("DROP INDEX cpk_approval_requests_rotation_identity")
            connection.execute(
                f'SET search_path TO "{self.schema}", "{shadow}"'
            )

            self._assert_contract_not_current(connection)
        finally:
            connection.execute(f'DROP SCHEMA IF EXISTS "{shadow}" CASCADE')
            connection.close()

    def test_fresh_and_current_paths_predeclare_complete_relation_locks(self) -> None:
        module = _contract_module()
        fresh = self._connection(autocommit=False)
        try:
            recorded_fresh = _RecordingConnection(fresh)
            postgres.install_postgres_schema(recorded_fresh)
            locks = _relation_locks(fresh)
            self.assertEqual(
                {relation.name for relation in module.PENDING_SCHEMA_LOCK_PLAN.relations},
                set(locks),
            )
            for relation in module.PENDING_SCHEMA_LOCK_PLAN.relations:
                self.assertIn("AccessExclusiveLock", locks[relation.relation])
            self._assert_fresh_lock_sequence(recorded_fresh)
            self._assert_later_locks_are_subsumed(
                recorded_fresh,
                module.PENDING_SCHEMA_LOCK_PLAN,
                locks,
            )
            fresh.commit()
        finally:
            fresh.rollback()
            fresh.close()

        current = self._connection(autocommit=False)
        try:
            postgres.install_postgres_schema(current)
            current.commit()
            recorded_current = _RecordingConnection(current)
            postgres.install_postgres_schema(recorded_current)
            locks = _relation_locks(current)
            for relation in module.CURRENT_SCHEMA_LOCK_PLAN.relations:
                self.assertIn(
                    self._postgres_lock_mode(relation.mode),
                    locks[relation.relation],
                )
            self.assertIsNotNone(recorded_current.lock_call_index)
            contract_reads = tuple(
                index
                for index, (query, _params) in enumerate(recorded_current.calls)
                if "candidate_relations AS MATERIALIZED" in query
            )
            self.assertEqual(len(contract_reads), 1)
            self.assertLess(recorded_current.lock_call_index, contract_reads[0])
            self._assert_later_locks_are_subsumed(
                recorded_current,
                module.CURRENT_SCHEMA_LOCK_PLAN,
                locks,
            )
        finally:
            current.rollback()
            current.close()

    def test_unversioned_and_versioned_prefix_lock_before_ledger_advancement(
        self,
    ) -> None:
        for label, versioned in (("unversioned", False), ("versioned-v1", True)):
            with self.subTest(label=label):
                setup = self._connection(reset=True)
                try:
                    setup.execute(postgres.POSTGRES_SCHEMA)
                    if versioned:
                        self._create_ledger(setup)
                        migration = postgres.POSTGRES_SCHEMA_MIGRATIONS.migrations[0]
                        setup.execute(
                            "INSERT INTO cpk_schema_migrations "
                            "(version, name, checksum_sha256) VALUES (%s, %s, %s)",
                            (
                                migration.version,
                                migration.name,
                                migration.checksum_sha256,
                            ),
                        )
                finally:
                    setup.close()

                connection = self._connection(autocommit=False)
                try:
                    recorded = _RecordingConnection(connection)
                    postgres.install_postgres_schema(recorded)
                    self.assertIsNotNone(recorded.lock_call_index)
                    ledger_inserts = tuple(
                        index
                        for index, (query, _params) in enumerate(recorded.calls)
                        if "INSERT INTO cpk_schema_migrations" in query
                    )
                    self.assertTrue(ledger_inserts)
                    self.assertLess(recorded.lock_call_index, ledger_inserts[0])
                    self._assert_later_locks_are_subsumed(
                        recorded,
                        _contract_module().PENDING_SCHEMA_LOCK_PLAN,
                        _relation_locks(connection),
                    )
                finally:
                    connection.rollback()
                    connection.close()

    def test_current_lock_timeout_rolls_back_and_outer_owner_controls_release(
        self,
    ) -> None:
        setup = self._connection()
        try:
            postgres.install_postgres_schema(setup)
            before = setup.execute(
                "SELECT version, name, checksum_sha256, applied_at "
                "FROM cpk_schema_migrations ORDER BY version"
            ).fetchall()
        finally:
            setup.close()

        blocker = self._connection(autocommit=False)
        contender = self._connection()
        try:
            blocker.execute(
                "LOCK TABLE cpk_activity_events IN ACCESS EXCLUSIVE MODE"
            )
            contender.execute("SET lock_timeout TO '100ms'")
            with self.assertRaises(postgres.SchemaMigrationError) as raised:
                postgres.verify_postgres_schema(contender)
            self._assert_categorical_contract_error(raised.exception)
            self.assertEqual(
                contender.execute(
                    "SELECT version, name, checksum_sha256, applied_at "
                    "FROM cpk_schema_migrations ORDER BY version"
                ).fetchall(),
                before,
            )
            blocker.rollback()
            postgres.verify_postgres_schema(contender)
        finally:
            blocker.rollback()
            blocker.close()
            contender.close()

        owner = self._connection(autocommit=False)
        observer = self._connection()
        try:
            postgres.verify_postgres_schema(owner)
            observer.execute("SET lock_timeout TO '100ms'")
            with self.assertRaises(psycopg.errors.LockNotAvailable):
                observer.execute(
                    "LOCK TABLE cpk_activity_events IN ACCESS EXCLUSIVE MODE"
                )
            owner.rollback()
            observer.execute(
                "LOCK TABLE cpk_activity_events IN ACCESS EXCLUSIVE MODE"
            )
        finally:
            owner.rollback()
            owner.close()
            observer.close()

    def test_current_lock_cancellation_is_categorical_and_preserves_history(
        self,
    ) -> None:
        setup = self._connection()
        try:
            postgres.install_postgres_schema(setup)
            before = setup.execute(
                "SELECT version, name, checksum_sha256, applied_at "
                "FROM cpk_schema_migrations ORDER BY version"
            ).fetchall()
        finally:
            setup.close()

        blocker = self._connection(autocommit=False)
        contender = self._connection(application_name="cpk-contract-cancel")
        failures: list[BaseException] = []
        finished = threading.Event()
        thread = None
        try:
            blocker.execute(
                "LOCK TABLE cpk_activity_events IN ACCESS EXCLUSIVE MODE"
            )

            def verify() -> None:
                try:
                    postgres.verify_postgres_schema(contender)
                except BaseException as error:
                    failures.append(error)
                finally:
                    finished.set()

            thread = threading.Thread(target=verify)
            thread.start()
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                waiting = self.admin.execute(
                    """
                    SELECT wait_event_type
                    FROM pg_stat_activity
                    WHERE application_name = 'cpk-contract-cancel'
                    """
                ).fetchone()
                if waiting == ("Lock",):
                    break
                if finished.is_set():
                    self.fail("current contract verification did not acquire its lock plan")
                time.sleep(0.01)
            else:
                self.fail("current contract verification did not wait on relation lock")

            contender.cancel()
            thread.join(timeout=10)
            self.assertFalse(thread.is_alive())
            self.assertEqual(len(failures), 1)
            self.assertIsInstance(failures[0], postgres.SchemaMigrationError)
            self._assert_categorical_contract_error(failures[0])
            self.assertEqual(
                contender.execute(
                    "SELECT version, name, checksum_sha256, applied_at "
                    "FROM cpk_schema_migrations ORDER BY version"
                ).fetchall(),
                before,
            )
        finally:
            blocker.rollback()
            blocker.close()
            if thread is not None and thread.is_alive():
                contender.cancel()
                thread.join(timeout=10)
            contender.close()

    def test_catalog_interpreter_is_single_bounded_predeparse_statement(self) -> None:
        verifier = getattr(
            migration_inspection,
            "_verify_current_schema_contract",
            None,
        )
        self.assertIsNotNone(verifier, "current schema interpreter is not implemented")

        class Result:
            def __init__(self, result):
                self.result = result

            def fetchall(self):
                return self.result

        class RecordingConnection:
            def __init__(self, result=((True, True, True, True, True),)):
                self.result = result
                self.calls: list[tuple[str, tuple[object, ...]]] = []

            def execute(self, query, params=()):
                self.calls.append((query, params))
                return Result(self.result)

        connection = RecordingConnection()
        verifier(connection)

        self.assertEqual(len(connection.calls), 1)
        query, parameters = connection.calls[0]
        normalized = " ".join(query.split())
        for candidate in (
            "candidate_relations AS MATERIALIZED",
            "candidate_columns AS MATERIALIZED",
            "candidate_constraints AS MATERIALIZED",
            "candidate_indexes AS MATERIALIZED",
        ):
            with self.subTest(candidate=candidate):
                self.assertIn(candidate, normalized)
        for bound in (30, 358, 233, 79):
            with self.subTest(bound=bound):
                self.assertIn(bound, parameters)
        self.assertIn("WITH ORDINALITY", normalized)
        self.assertIn("LEFT JOIN pg_attribute", normalized)
        self.assertIn("owned_constraint.confrelid", normalized)
        self.assertIn("key.attnum = 0", normalized)
        self.assertIn("cardinality(owned_constraint.conkey)", normalized)
        self.assertIn("cardinality(owned_constraint.confkey)", normalized)
        self.assertIn("relation.relpersistence", normalized)
        self.assertIn("relation.relispartition", normalized)
        self.assertIn("relation.relrowsecurity", normalized)
        self.assertIn("relation.relforcerowsecurity", normalized)
        self.assertIn("attribute.attidentity", normalized)
        self.assertIn("attribute.attgenerated", normalized)
        self.assertIn("attribute.attisdropped IS FALSE", normalized)
        self.assertIn("index.indnkeyatts", normalized)
        self.assertIn("index.indnatts", normalized)
        self.assertIn("index.indnullsnotdistinct", normalized)
        self.assertIn("index.indimmediate", normalized)
        self.assertIn("index.indisvalid", normalized)
        self.assertIn("index.indisready", normalized)
        self.assertIn("index.indislive", normalized)
        self.assertIn("cardinality(index.indclass::oid[])", normalized)
        self.assertIn("cardinality(index.indcollation::oid[])", normalized)
        self.assertIn("cardinality(index.indoption::smallint[])", normalized)
        self.assertLess(normalized.index("LIMIT"), normalized.index("pg_get_expr"))
        self.assertLess(normalized.index("LIMIT"), normalized.index("pg_get_indexdef"))
        self.assertNotIn("pg_get_constraintdef", normalized)

        marker = "private-candidate-definition-and-database-address"
        for result in ((), ((False, True, True, True, True),), ((marker,),)):
            with self.subTest(result=result):
                malformed = RecordingConnection(result)
                with self.assertRaises(postgres.SchemaMigrationError) as raised:
                    verifier(malformed)
                self._assert_categorical_contract_error(raised.exception, marker)

        class FailingConnection:
            def execute(self, _query, _params=()):
                raise RuntimeError(marker)

        with self.assertRaises(postgres.SchemaMigrationError) as raised:
            verifier(FailingConnection())
        self._assert_categorical_contract_error(raised.exception, marker)

    def _connection(
        self,
        *,
        reset: bool = False,
        autocommit: bool = True,
        application_name: str | None = None,
    ):
        if reset:
            self.admin.execute(f'DROP SCHEMA IF EXISTS "{self.schema}" CASCADE')
            self.admin.execute(f'CREATE SCHEMA "{self.schema}"')
        options = {"autocommit": autocommit}
        if application_name is not None:
            options["application_name"] = application_name
        connection = psycopg.connect(self.database_url, **options)
        connection.execute(f'SET search_path TO "{self.schema}"')
        if not autocommit:
            connection.commit()
        return connection

    def _assert_contract_not_current(self, connection) -> None:
        with self.assertRaises(postgres.SchemaMigrationError) as raised:
            postgres.verify_postgres_schema(connection)
        self._assert_categorical_contract_error(raised.exception)

    def _assert_categorical_contract_error(
        self,
        error: BaseException,
        marker: str | None = None,
    ) -> None:
        self.assertEqual(str(error), "database schema contract is not current")
        self.assertLessEqual(len(str(error)), 256)
        if marker is not None:
            self.assertNotIn(marker, str(error))
        self.assertIsNone(error.__context__)
        self.assertIsNone(error.__cause__)

    @staticmethod
    def _create_ledger(connection) -> None:
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

    def _assert_fresh_lock_sequence(self, recorded: _RecordingConnection) -> None:
        self.assertIsNotNone(recorded.lock_call_index)
        create_ledger = next(
            index
            for index, (query, _params) in enumerate(recorded.calls)
            if "CREATE TABLE cpk_schema_migrations" in query
        )
        apply_v1 = next(
            index
            for index, (query, _params) in enumerate(recorded.calls)
            if query == postgres.POSTGRES_SCHEMA
        )
        insert_v1 = next(
            index
            for index, (query, params) in enumerate(recorded.calls)
            if "INSERT INTO cpk_schema_migrations" in query and params[0] == 1
        )
        self.assertLess(create_ledger, apply_v1)
        self.assertLess(apply_v1, recorded.lock_call_index)
        self.assertLess(recorded.lock_call_index, insert_v1)

    def _assert_later_locks_are_subsumed(
        self,
        recorded: _RecordingConnection,
        plan,
        final_locks: dict[str, set[str]],
    ) -> None:
        self.assertIsNotNone(recorded.predeclared_locks)
        declared = {item.relation: self._postgres_lock_mode(item.mode) for item in plan.relations}
        for relation, modes in final_locks.items():
            with self.subTest(relation=relation):
                declared_mode = declared[relation]
                self.assertIn(declared_mode, recorded.predeclared_locks[relation])
                for mode in modes:
                    self.assertLessEqual(_CONFLICTS[mode], _CONFLICTS[declared_mode])

    @staticmethod
    def _reorder_workspace_name(connection) -> None:
        connection.execute("ALTER TABLE cpk_workspaces DROP COLUMN name")
        connection.execute("ALTER TABLE cpk_workspaces ADD COLUMN name text NOT NULL")

    @staticmethod
    def _postgres_lock_mode(mode: str) -> str:
        return "".join(part.title() for part in mode.split()) + "Lock"


if __name__ == "__main__":
    unittest.main()
