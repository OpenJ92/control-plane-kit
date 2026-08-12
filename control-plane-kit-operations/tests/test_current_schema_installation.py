from __future__ import annotations

import ast
from contextlib import contextmanager
import dataclasses
import hashlib
import json
import os
from pathlib import Path
import threading
import unittest
import uuid

import psycopg

import control_plane_kit_operations.postgres as postgres


_EXPECTED_RELATIONS = (
    "cpk_activity_events",
    "cpk_activity_plans",
    "cpk_activity_runs",
    "cpk_approval_decisions",
    "cpk_approval_requests",
    "cpk_cloudflare_ingress_resources",
    "cpk_delegation_signing_keys",
    "cpk_execution_requests",
    "cpk_gateway_key_rotation_deployments",
    "cpk_gateway_key_rotation_revocations",
    "cpk_gateway_key_rotation_transitions",
    "cpk_gateway_key_rotations",
    "cpk_gateway_probe_attempts",
    "cpk_generated_ingress_secret_references",
    "cpk_graph_versions",
    "cpk_image_pull_authorities",
    "cpk_ingress_authorities",
    "cpk_observations",
    "cpk_operation_actions",
    "cpk_operation_sessions",
    "cpk_realized_graph_projections",
    "cpk_registered_products",
    "cpk_runtime_authorities",
    "cpk_runtime_authority_deliveries",
    "cpk_secret_providers",
    "cpk_secret_references",
    "cpk_secret_use_authorizations",
    "cpk_workspaces",
)

_FORBIDDEN_MODULES = (
    "graph_lineage_backfill.py",
    "migration_inspection.py",
    "migration_runner.py",
    "migrations.py",
    "product_descriptor_backfill.py",
)

_FORBIDDEN_EXPORTS = (
    "POSTGRES_SCHEMA",
    "POSTGRES_SCHEMA_MIGRATIONS",
    "POSTGRES_SCHEMA_MIGRATION_LEDGER_COLUMNS",
    "POSTGRES_SCHEMA_MIGRATION_LEDGER_TABLE",
    "POSTGRES_SCHEMA_V1_SHA256",
    "POSTGRES_SCHEMA_V1_TABLE_COLUMNS",
    "AppliedSchemaMigration",
    "DeterministicBackfillStep",
    "MigrationPostgresConnection",
    "ObservedSchemaKind",
    "ObservedSchemaState",
    "SchemaBackfillKind",
    "SchemaMigration",
    "SchemaMigrationAction",
    "SchemaMigrationActionKind",
    "SchemaMigrationError",
    "SchemaMigrationPlan",
    "SchemaMigrationRegistry",
    "SqlMigrationStep",
    "inspect_postgres_schema",
    "install_postgres_schema",
    "plan_postgres_schema_install",
    "verify_postgres_schema",
)

_FORBIDDEN_SCHEMA_NAMES = frozenset(
    {
        "AppliedSchemaMigration",
        "DeterministicBackfillStep",
        "MigrationIdentity",
        "ObservedSchemaKind",
        "ObservedSchemaState",
        "SchemaBackfillKind",
        "SchemaLockPlan",
        "SchemaMigration",
        "SchemaMigrationAction",
        "SchemaMigrationActionKind",
        "SchemaMigrationPlan",
        "SchemaMigrationRegistry",
        "SqlMigrationStep",
        "backfill_graph_lineage_v1",
        "backfill_product_descriptor_content_v1",
        "inspect_postgres_schema",
        "install_postgres_schema",
        "plan_postgres_schema_install",
        "verify_graph_lineage_v1",
        "verify_postgres_schema",
    }
)
_CURRENT_CONTRACT_SHA256 = (
    "b5f56207a526323dc19503968955b1654f3ed34e2a8a2a82ccf9b818cecc5d94"
)
_CURRENT_SCHEMA_SQL_SHA256 = (
    "552a47f2cf569029f70213a60d94390d58b19650dc758b9c811f1c9ac3edf4c8"
)
_CONTRACT_DOMAIN = "control-plane-kit.operations.postgres.current-schema"
_CONTRACT_FORMAT_VERSION = 1
_PURPOSE_VALUES = (
    "gateway-probe",
    "workload-node-control",
    "workload-node-control-surface-read",
    "gateway-node-control-transit",
)
_NEW_PURPOSE = "gateway-node-control-transit"
_OLD_PURPOSE_EXPRESSION = (
    "(purpose = ANY (ARRAY['gateway-probe'::text, "
    "'workload-node-control'::text, "
    "'workload-node-control-surface-read'::text]))"
)
_PURPOSE_EXPRESSION = (
    "(purpose = ANY (ARRAY['gateway-probe'::text, "
    "'workload-node-control'::text, "
    "'workload-node-control-surface-read'::text, "
    "'gateway-node-control-transit'::text]))"
)
_INTENT_VALUES = (
    "application.control-token",
    "cloudflare.api-token",
    "cloudflare.tunnel-token",
    "docker.local-socket-access-marker",
    "docker.remote-tls.ca-certificate",
    "docker.remote-tls.client-certificate",
    "docker.remote-tls.client-key",
    "gateway.probe-signing-key",
    "oci.pull-credential",
    "postgres.password",
    "gateway.node-control-transit-signing-key",
    "workload.node-control-signing-key",
)
_NEW_INTENTS = (
    "gateway.node-control-transit-signing-key",
    "workload.node-control-signing-key",
)
_OLD_INTENT_EXPRESSION = (
    "(use_intent = ANY (ARRAY['application.control-token'::text, "
    "'cloudflare.api-token'::text, 'cloudflare.tunnel-token'::text, "
    "'docker.local-socket-access-marker'::text, "
    "'docker.remote-tls.ca-certificate'::text, "
    "'docker.remote-tls.client-certificate'::text, "
    "'docker.remote-tls.client-key'::text, "
    "'gateway.probe-signing-key'::text, 'oci.pull-credential'::text, "
    "'postgres.password'::text]))"
)
_INTENT_EXPRESSION = (
    "(use_intent = ANY (ARRAY['application.control-token'::text, "
    "'cloudflare.api-token'::text, 'cloudflare.tunnel-token'::text, "
    "'docker.local-socket-access-marker'::text, "
    "'docker.remote-tls.ca-certificate'::text, "
    "'docker.remote-tls.client-certificate'::text, "
    "'docker.remote-tls.client-key'::text, "
    "'gateway.probe-signing-key'::text, 'oci.pull-credential'::text, "
    "'postgres.password'::text, "
    "'gateway.node-control-transit-signing-key'::text, "
    "'workload.node-control-signing-key'::text]))"
)
_TARGET_CONSTRAINTS = {
    "cpk_delegation_signing_keys_purpose_check": (
        "cpk_delegation_signing_keys",
        "purpose",
        _PURPOSE_EXPRESSION,
        _OLD_PURPOSE_EXPRESSION,
    ),
    "cpk_gateway_key_rotations_purpose_check": (
        "cpk_gateway_key_rotations",
        "purpose",
        _PURPOSE_EXPRESSION,
        _OLD_PURPOSE_EXPRESSION,
    ),
    "cpk_secret_use_authorizations_intent_check": (
        "cpk_secret_use_authorizations",
        "use_intent",
        _INTENT_EXPRESSION,
        _OLD_INTENT_EXPRESSION,
    ),
}
_APPROVAL_SCOPE_VALUES = (
    "hub:instance:create",
    "hub:instance:read",
    "instance:workspace:read",
    "instance:workspace:edit",
    "plan:request",
    "plan:approve",
    "plan:approve-destructive",
    "plan:execute",
    "execution:operate",
    "runtime-authority:register",
    "runtime-authority:read",
    "runtime-authority:revoke",
    "runtime-authority:use",
    "runtime-authority-delivery:register",
    "runtime-authority-delivery:read",
    "runtime-authority-delivery:revoke",
    "ingress-authority:register",
    "ingress-authority:read",
    "ingress-authority:revoke",
    "ingress-authority:use",
    "secret-provider:register",
    "secret-provider:read",
    "secret-provider:use",
    "secret-provider:revoke",
    "delegation-key:generate",
    "delegation-key:register",
    "delegation-key:read",
    "delegation-key:activate",
    "delegation-key:retire",
    "delegation-key:revoke",
    "delegation-key:use",
    "delegation-key:rotate",
    "delegation-key:rotate-approve",
    "gateway-probe:use",
)


def _approval_scope_expression(column: str) -> str:
    values = ", ".join(f"'{value}'::text" for value in _APPROVAL_SCOPE_VALUES)
    return f"({column} = ANY (ARRAY[{values}]))"


_APPROVAL_CONSTRAINTS = {
    "cpk_approval_decisions_scope_check": _approval_scope_expression("scope"),
    "cpk_approval_requests_scope_check": _approval_scope_expression(
        "required_scope"
    ),
}
_NODE_CONTROL_SCOPES = (
    "node-control:read",
    "node-control:apply",
    "node-control:execute",
)


class _RecordingConnection:
    def __init__(self, delegate) -> None:
        self.delegate = delegate
        self.calls: list[str] = []

    @property
    def autocommit(self):
        return self.delegate.autocommit

    def transaction(self):
        return self.delegate.transaction()

    def execute(self, query, params=()):
        text = query if isinstance(query, str) else str(query)
        self.calls.append(text)
        if params == ():
            return self.delegate.execute(query)
        return self.delegate.execute(query, params)


class _FailingConnection(_RecordingConnection):
    def __init__(self, delegate, *, fail_on: int) -> None:
        super().__init__(delegate)
        self.fail_on = fail_on

    def execute(self, query, params=()):
        if len(self.calls) + 1 == self.fail_on:
            self.calls.append(str(query))
            raise RuntimeError(
                "postgresql://operator:secret@internal.example:5432/private"
            )
        return super().execute(query, params)


class _AlwaysFailingConnection:
    autocommit = True

    @contextmanager
    def transaction(self):
        yield

    def execute(self, query, params=()):
        raise RuntimeError(
            "postgresql://operator:secret@internal.example:5432/private"
        )


def _captured_install_error(connection) -> BaseException:
    try:
        postgres.install_schema(connection)
    except BaseException as error:
        return error
    raise AssertionError("schema installation unexpectedly succeeded")


class CurrentSchemaStaticLawTests(unittest.TestCase):
    def test_public_postgres_connection_is_execute_only(self) -> None:
        connection_members = set(postgres.PostgresConnection.__dict__)

        self.assertIn("execute", connection_members)
        self.assertNotIn("autocommit", connection_members)
        self.assertNotIn("transaction", connection_members)

    def test_public_and_source_residue_is_exactly_removed(self) -> None:
        self.assertTrue(hasattr(postgres, "SchemaInstallationError"))
        for name in _FORBIDDEN_EXPORTS:
            with self.subTest(export=name):
                self.assertFalse(hasattr(postgres, name))

        package = Path(postgres.__file__).resolve().parent
        for name in _FORBIDDEN_MODULES:
            with self.subTest(module=name):
                self.assertFalse((package / name).exists())

        observed_names: set[str] = set()
        for source in package.glob("*.py"):
            tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
            for node in ast.walk(tree):
                if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                    observed_names.add(node.name)
        self.assertEqual(observed_names & _FORBIDDEN_SCHEMA_NAMES, set())

        scoped_text = "\n".join(
            source.read_text(encoding="utf-8") for source in package.glob("*.py")
        )
        for value in (
            "cpk_schema_migrations",
            "operations-baseline",
            "schema-migration-program",
        ):
            with self.subTest(value=value):
                self.assertNotIn(value, scoped_text)

    def test_current_contract_has_only_exact_functional_truth(self) -> None:
        from control_plane_kit_operations.postgres import current_schema_contract

        contract = current_schema_contract.CURRENT_POSTGRES_SCHEMA_CONTRACT
        self.assertEqual(len(contract.relations), 28)
        self.assertEqual(len(contract.columns), 353)
        self.assertEqual(len(contract.constraints), 231)
        self.assertEqual(len(contract.indexes), 77)
        self.assertFalse(hasattr(contract, "history"))
        self.assertEqual(
            tuple(relation.name for relation in contract.relations),
            _EXPECTED_RELATIONS,
        )
        self.assertNotIn("cpk_schema_migrations", repr(contract))
        self.assertFalse(hasattr(current_schema_contract, "SchemaLockPlan"))
        self.assertFalse(hasattr(current_schema_contract, "PENDING_SCHEMA_LOCK_PLAN"))
        self.assertEqual(
            current_schema_contract.CURRENT_POSTGRES_SCHEMA_CONTRACT_SHA256,
            _CURRENT_CONTRACT_SHA256,
        )

        source = Path(current_schema_contract.__file__).read_text(encoding="utf-8")
        imports = {
            node.module
            for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.ImportFrom)
        }
        self.assertLessEqual(imports, {"__future__", "dataclasses"})

    def test_current_contract_identifies_exact_node_control_vocabulary(self) -> None:
        from control_plane_kit_operations.postgres import current_schema_contract
        from control_plane_kit_operations.postgres import schema

        contract = current_schema_contract.CURRENT_POSTGRES_SCHEMA_CONTRACT
        constraints = {value.name: value for value in contract.constraints}
        for name, (relation, column, expression, _) in _TARGET_CONSTRAINTS.items():
            with self.subTest(constraint=name):
                value = constraints[name]
                self.assertEqual(value.relation, relation)
                self.assertEqual(value.local_columns, (column,))
                self.assertTrue(value.validated)
                self.assertEqual(value.check_expression, expression)
                self.assertEqual(
                    schema._CURRENT_SCHEMA_SQL.count(
                        f"CONSTRAINT {name} CHECK ({expression})"
                    ),
                    1,
                )

        for name, expected_expression in _APPROVAL_CONSTRAINTS.items():
            with self.subTest(approval_constraint=name):
                expression = constraints[name].check_expression
                self.assertEqual(expression, expected_expression)
                self.assertEqual(
                    schema._CURRENT_SCHEMA_SQL.count(
                        f"CONSTRAINT {name} CHECK ({expected_expression})"
                    ),
                    1,
                )

        payload = json.dumps(
            {
                "domain": _CONTRACT_DOMAIN,
                "format_version": _CONTRACT_FORMAT_VERSION,
                "contract": dataclasses.asdict(contract),
            },
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("ascii")
        self.assertEqual(
            current_schema_contract.CURRENT_POSTGRES_SCHEMA_CONTRACT_SHA256,
            hashlib.sha256(payload).hexdigest(),
        )

    def test_direct_schema_program_is_unconditional_and_nonhistorical(self) -> None:
        from control_plane_kit_operations.postgres import schema

        self.assertTrue(hasattr(schema, "_CURRENT_SCHEMA_SQL"))
        sql = getattr(schema, "_CURRENT_SCHEMA_SQL", "")
        self.assertIsInstance(sql, str)
        normalized = " ".join(sql.lower().split())
        self.assertNotIn("if exists", normalized)
        self.assertNotIn("if not exists", normalized)
        self.assertNotIn("cpk_schema_migrations", normalized)
        for forbidden in (" drop ", " truncate ", " insert ", " update ", " delete "):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, f" {normalized} ")
        statements = tuple(
            statement.strip()
            for statement in sql.split(";")
            if statement.strip()
        )
        self.assertTrue(statements)
        for statement in statements:
            with self.subTest(statement=statement[:80]):
                self.assertRegex(
                    " ".join(statement.lower().split()),
                    r"^(create table|create index|create unique index|alter table) ",
                )
        self.assertEqual(
            sum(statement.lower().startswith("create table ") for statement in statements),
            28,
        )
        self.assertEqual(
            hashlib.sha256(sql.encode("utf-8")).hexdigest(),
            _CURRENT_SCHEMA_SQL_SHA256,
        )


class CurrentSchemaInstallationTests(unittest.TestCase):
    def setUp(self) -> None:
        database_url = os.environ.get("CPK_OPERATIONS_TEST_DATABASE_URL")
        if not database_url:
            raise RuntimeError(
                "CPK_OPERATIONS_TEST_DATABASE_URL is required through the "
                "disposable operations Docker test environment"
            )
        self.database_url = database_url
        self.schema = f"schema_cutover_{uuid.uuid4().hex}"
        self.connection = psycopg.connect(database_url, autocommit=True)
        self.connection.execute(f'CREATE SCHEMA "{self.schema}"')
        self.connection.execute(f'SET search_path TO "{self.schema}"')

    def tearDown(self) -> None:
        self.connection.execute("SET search_path TO public")
        self.connection.execute(f'DROP SCHEMA "{self.schema}" CASCADE')
        self.connection.close()

    def test_object_free_install_creates_only_exact_current_truth(self) -> None:
        postgres.install_schema(self.connection)

        self.assertEqual(self._relations(), _EXPECTED_RELATIONS)
        self.assertEqual(self._catalog_counts(), (28, 353, 231, 77))
        self.assertEqual(
            self.connection.execute(
                "SELECT to_regclass('cpk_schema_migrations') IS NULL"
            ).fetchone(),
            (True,),
        )

    def test_fresh_schema_persists_new_authority_purpose_and_intents(self) -> None:
        postgres.install_schema(self.connection)
        self._assert_target_constraints()
        self._seed_authority_vocabulary_rows()

        self.assertEqual(
            self.connection.execute(
                "SELECT purpose FROM cpk_delegation_signing_keys"
            ).fetchall(),
            [(_NEW_PURPOSE,)],
        )
        self.assertEqual(
            self.connection.execute(
                "SELECT purpose FROM cpk_gateway_key_rotations"
            ).fetchall(),
            [(_NEW_PURPOSE,)],
        )
        self.assertEqual(
            tuple(
                row[0]
                for row in self.connection.execute(
                    "SELECT use_intent FROM cpk_secret_use_authorizations "
                    "ORDER BY use_intent"
                ).fetchall()
            ),
            tuple(sorted(_NEW_INTENTS)),
        )

    def test_authority_rows_and_approval_identity_survive_current_reentry(
        self,
    ) -> None:
        postgres.install_schema(self.connection)
        self._assert_target_constraints()
        self._seed_authority_vocabulary_rows()
        before_objects = self._object_identities()
        before_rows = self._authority_rows()
        recorder = _RecordingConnection(self.connection)

        postgres.install_schema(recorder)

        self.assertEqual(self._object_identities(), before_objects)
        self.assertEqual(self._authority_rows(), before_rows)
        self._assert_calls_are_read_only(recorder.calls)
        self._seed_approval_subject()
        self._assert_node_control_scopes_are_not_approval_scopes()

    def test_authority_constraint_drift_is_reset_required_without_repair(
        self,
    ) -> None:
        names = tuple(_TARGET_CONSTRAINTS)
        cases = (
            ("pre-change", names[0]),
            ("missing", names[1]),
            ("wrong", names[2]),
            ("extra", names[0]),
            ("unvalidated", names[1]),
        )
        for variant, name in cases:
            with self.subTest(constraint=name, variant=variant):
                self._reset_owned_schema()
                postgres.install_schema(self.connection)
                self._assert_target_constraints()
                relation, column, expression, old_expression = _TARGET_CONSTRAINTS[name]
                self.connection.execute(
                    "INSERT INTO cpk_workspaces "
                    "(workspace_id, name, lifecycle) "
                    "VALUES ('workspace-a', 'Workspace A', 'created')"
                )
                if variant == "extra":
                    self.connection.execute(
                        f"ALTER TABLE {relation} ADD CONSTRAINT {name}_extra "
                        f"CHECK (({column} <> ''::text))"
                    )
                else:
                    self.connection.execute(
                        f"ALTER TABLE {relation} DROP CONSTRAINT {name}"
                    )
                    if variant != "missing":
                        replacement = {
                            "pre-change": old_expression,
                            "wrong": f"({column} <> ''::text)",
                            "unvalidated": expression,
                        }[variant]
                        validation = " NOT VALID" if variant == "unvalidated" else ""
                        self.connection.execute(
                            f"ALTER TABLE {relation} ADD CONSTRAINT {name} "
                            f"CHECK ({replacement}){validation}"
                        )
                before = self._constraint_snapshot()
                recorder = _RecordingConnection(self.connection)

                error = _captured_install_error(recorder)

                self._assert_install_error(error, "operations schema reset is required")
                self.assertEqual(self._constraint_snapshot(), before)
                self.assertEqual(
                    self.connection.execute(
                        "SELECT workspace_id, name, lifecycle FROM cpk_workspaces"
                    ).fetchall(),
                    [("workspace-a", "Workspace A", "created")],
                )
                self._assert_calls_are_read_only(recorder.calls)

    def test_current_reinstall_is_query_only_and_identity_stable(self) -> None:
        postgres.install_schema(self.connection)
        self.connection.execute(
            "INSERT INTO cpk_workspaces (workspace_id, name, lifecycle) "
            "VALUES ('workspace-a', 'Workspace A', 'created')"
        )
        before = self._object_identities()
        recorder = _RecordingConnection(self.connection)

        postgres.install_schema(recorder)

        self.assertEqual(self._object_identities(), before)
        self.assertEqual(
            self.connection.execute(
                "SELECT workspace_id, name, lifecycle FROM cpk_workspaces"
            ).fetchall(),
            [("workspace-a", "Workspace A", "created")],
        )
        self.assertNotIn("cpk_schema_migrations", "\n".join(recorder.calls))
        for call in recorder.calls:
            normalized = " ".join(call.lower().split())
            self.assertNotRegex(
                normalized,
                r"\b(create|alter|drop|truncate|insert|update|delete)\b",
            )

    def test_nonempty_owned_object_families_require_reset_before_effect(self) -> None:
        fixtures = (
            "CREATE TABLE stray_table (value integer)",
            "CREATE VIEW stray_view AS SELECT 1 AS value",
            "CREATE MATERIALIZED VIEW stray_materialized AS SELECT 1 AS value",
            "CREATE SEQUENCE stray_sequence",
            "CREATE TYPE stray_enum AS ENUM ('value')",
            "CREATE DOMAIN stray_domain AS text",
            "CREATE FUNCTION stray_function() RETURNS integer LANGUAGE SQL "
            "IMMUTABLE AS 'SELECT 1'",
        )
        for ddl in fixtures:
            with self.subTest(ddl=ddl):
                self._reset_owned_schema()
                self.connection.execute(ddl)
                before = self._namespace_objects()

                error = _captured_install_error(self.connection)

                self._assert_install_error(error, "operations schema reset is required")
                self.assertEqual(self._namespace_objects(), before)
                self.assertEqual(set(self._relations()) & set(_EXPECTED_RELATIONS), set())

    def test_cross_schema_objects_are_preserved_and_ignored(self) -> None:
        other = f"other_{uuid.uuid4().hex}"
        self.connection.execute(f'CREATE SCHEMA "{other}"')
        try:
            self.connection.execute(f'CREATE TABLE "{other}".cpk_workspaces (x int)')
            self.connection.execute(f'CREATE TYPE "{other}".stray_enum AS ENUM (\'x\')')
            self.connection.execute(
                f'CREATE TABLE "{other}".cpk_delegation_signing_keys '
                "(purpose text)"
            )
            self.connection.execute(
                f'ALTER TABLE "{other}".cpk_delegation_signing_keys '
                "ADD CONSTRAINT cpk_delegation_signing_keys_purpose_check "
                f"CHECK ({_OLD_PURPOSE_EXPRESSION})"
            )
            lookalike = self._constraint_snapshot(other)

            postgres.install_schema(self.connection)

            self._assert_target_constraints()
            self.assertEqual(self._relations(), _EXPECTED_RELATIONS)
            self.assertEqual(self._constraint_snapshot(other), lookalike)
            self.assertEqual(
                self.connection.execute(
                    "SELECT to_regclass(%s) IS NOT NULL",
                    (f'"{other}".cpk_workspaces',),
                ).fetchone(),
                (True,),
            )
        finally:
            self.connection.execute(f'DROP SCHEMA "{other}" CASCADE')

    def test_current_drift_rejects_without_repair_or_row_loss(self) -> None:
        postgres.install_schema(self.connection)
        self.connection.execute(
            "INSERT INTO cpk_workspaces (workspace_id, name, lifecycle) "
            "VALUES ('workspace-a', 'Workspace A', 'created')"
        )
        self.connection.execute("ALTER TABLE cpk_workspaces ADD COLUMN stray text")
        before = self._object_identities()

        error = _captured_install_error(self.connection)

        self._assert_install_error(error, "operations schema reset is required")
        self.assertEqual(self._object_identities(), before)
        self.assertEqual(
            self.connection.execute(
                "SELECT workspace_id, name, lifecycle, stray FROM cpk_workspaces"
            ).fetchall(),
            [("workspace-a", "Workspace A", "created", None)],
        )

    def test_expected_relation_replaced_by_view_rejects_before_relation_lock(self) -> None:
        postgres.install_schema(self.connection)
        self.connection.execute("DROP TABLE cpk_observations CASCADE")
        self.connection.execute(
            "CREATE VIEW cpk_observations AS SELECT NULL::text AS workspace_id "
            "WHERE false"
        )
        before = self._object_identities()
        recorder = _RecordingConnection(self.connection)

        error = _captured_install_error(recorder)

        self._assert_install_error(error, "operations schema reset is required")
        self.assertEqual(self._object_identities(), before)
        self.assertEqual(
            self.connection.execute(
                "SELECT relkind FROM pg_class AS relation "
                "JOIN pg_namespace AS namespace "
                "ON namespace.oid=relation.relnamespace "
                "WHERE namespace.nspname=current_schema() "
                "AND relation.relname='cpk_observations'"
            ).fetchone(),
            ("v",),
        )
        self.assertFalse(
            any("LOCK TABLE ONLY" in call for call in recorder.calls),
            recorder.calls,
        )

    def test_empty_install_failure_rolls_back_every_schema_effect(self) -> None:
        failing = _FailingConnection(self.connection, fail_on=5)

        error = _captured_install_error(failing)

        self._assert_install_error(error, "operations schema installation failed")
        self.assertEqual(self._namespace_objects(), ())

    def test_caller_owned_outer_transaction_retains_authority(self) -> None:
        marker = f"cpk_cutover_marker_{uuid.uuid4().hex}"
        self.connection.execute(
            f'CREATE TABLE public."{marker}" (value text)'
        )
        self.connection.autocommit = False
        try:
            self.connection.execute(
                f'INSERT INTO public."{marker}" VALUES (\'outer\')'
            )
            postgres.install_schema(self.connection)
            self.assertEqual(self._relations(), _EXPECTED_RELATIONS)
            self.connection.rollback()
        finally:
            if self.connection.info.transaction_status != psycopg.pq.TransactionStatus.IDLE:
                self.connection.rollback()
            self.connection.autocommit = True
            self.connection.execute(f'DROP TABLE public."{marker}"')

        self.assertEqual(self._namespace_objects(), ())

    def test_concurrent_empty_installers_serialize_to_one_current_schema(self) -> None:
        barrier = threading.Barrier(2)
        failures: list[BaseException] = []

        def install() -> None:
            connection = psycopg.connect(self.database_url, autocommit=True)
            try:
                connection.execute(f'SET search_path TO "{self.schema}"')
                barrier.wait(timeout=5)
                postgres.install_schema(connection)
            except BaseException as error:
                failures.append(error)
            finally:
                connection.close()

        threads = (threading.Thread(target=install), threading.Thread(target=install))
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)

        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertEqual(failures, [])
        self.assertEqual(self._relations(), _EXPECTED_RELATIONS)
        self.assertEqual(self._catalog_counts(), (28, 353, 231, 77))

    def test_relation_lock_timeout_is_generic_and_retryable_after_release(self) -> None:
        postgres.install_schema(self.connection)
        self.connection.execute(
            "INSERT INTO cpk_workspaces (workspace_id, name, lifecycle) "
            "VALUES ('workspace-a', 'Workspace A', 'created')"
        )
        before = self._object_identities()
        blocker = psycopg.connect(self.database_url, autocommit=False)
        contender = psycopg.connect(self.database_url, autocommit=True)
        try:
            blocker.execute(f'SET search_path TO "{self.schema}"')
            contender.execute(f'SET search_path TO "{self.schema}"')
            contender.execute("SET lock_timeout = '500ms'")
            blocker.execute(
                "LOCK TABLE ONLY cpk_activity_events IN ACCESS EXCLUSIVE MODE"
            )
            recorder = _RecordingConnection(contender)

            error = _captured_install_error(recorder)

            self._assert_install_error(
                error,
                "operations schema installation failed",
            )
            self.assertNotIn("reset", str(error).lower())
            for call in recorder.calls:
                self.assertNotRegex(
                    " ".join(call.lower().split()),
                    r"\b(create|alter|drop|truncate|insert|update|delete)\b",
                )

            blocker.rollback()
            postgres.install_schema(contender)

            self.assertEqual(self._object_identities(), before)
            self.assertEqual(
                self.connection.execute(
                    "SELECT workspace_id, name, lifecycle FROM cpk_workspaces"
                ).fetchall(),
                [("workspace-a", "Workspace A", "created")],
            )
        finally:
            if blocker.info.transaction_status != psycopg.pq.TransactionStatus.IDLE:
                blocker.rollback()
            blocker.close()
            contender.close()

    def test_driver_failure_is_not_reset_advice_and_is_redacted(self) -> None:
        error = _captured_install_error(_AlwaysFailingConnection())

        self._assert_install_error(error, "operations schema installation failed")
        rendered = repr(error)
        for forbidden in ("operator", "secret", "internal.example", "5432", "private"):
            self.assertNotIn(forbidden, rendered)

    def _assert_target_constraints(self) -> None:
        observed = {
            row[1]: (row[0], row[2], row[3])
            for row in self.connection.execute(
                "SELECT relation.relname, owned.conname, owned.convalidated, "
                "pg_get_expr(owned.conbin, owned.conrelid) "
                "FROM pg_constraint AS owned "
                "JOIN pg_class AS relation ON relation.oid=owned.conrelid "
                "JOIN pg_namespace AS namespace "
                "ON namespace.oid=relation.relnamespace "
                "WHERE namespace.nspname=current_schema() "
                "AND owned.conname = ANY(%s) "
                "ORDER BY owned.conname",
                (list(_TARGET_CONSTRAINTS),),
            ).fetchall()
        }
        expected = {
            name: (relation, True, expression)
            for name, (relation, _, expression, _) in _TARGET_CONSTRAINTS.items()
        }
        self.assertEqual(observed, expected)

    def _seed_authority_vocabulary_rows(self) -> None:
        self.connection.execute(
            "INSERT INTO cpk_workspaces (workspace_id, name, lifecycle) "
            "VALUES ('workspace-a', 'Workspace A', 'created')"
        )
        self.connection.execute(
            "INSERT INTO cpk_delegation_signing_keys ("
            "registration_id, workspace_id, purpose, issuer, key_id, algorithm, "
            "public_key_pem, public_fingerprint_sha256, private_key_reference, "
            "admitted_by, admitted_at, status"
            ") VALUES (%s, 'workspace-a', %s, 'issuer-a', 'key-a', 'ed25519', "
            "'public-key-a', %s, 'secret://provider-a/signing/key-a', "
            "'operator-a', '2026-08-11T00:00:00Z', 'verify-only')",
            ("dkey_" + "1" * 64, _NEW_PURPOSE, "1" * 64),
        )
        self.connection.execute(
            "INSERT INTO cpk_gateway_key_rotations ("
            "rotation_id, workspace_id, gateway_node_id, purpose, issuer, "
            "old_key_id, new_secret_reference, key_generation_correlation, "
            "maximum_grant_lifetime_seconds, clock_skew_seconds, correlation_id, "
            "requested_by, requested_at, intent_fingerprint, status, version"
            ") VALUES ('rotation-1', 'workspace-a', 'gateway-a', %s, 'issuer-a', "
            "'key-a', 'secret://provider-a/signing/key-b', 'generate-key-b', "
            "120, 10, 'rotate-key-a', 'operator-a', '2026-08-11T00:00:00Z', "
            "%s, 'requested', 1)",
            (_NEW_PURPOSE, "2" * 64),
        )

        allowed_intents = json.dumps(_INTENT_VALUES, separators=(",", ":"))
        self.connection.execute(
            "INSERT INTO cpk_secret_providers ("
            "registration_id, workspace_id, provider_id, provider_kind, "
            "display_name, endpoint_reference, credential_reference, "
            "allowed_reference_prefixes, allowed_intents, admitted_by, "
            "admitted_at, status"
            ") VALUES ('provider-registration-a', 'workspace-a', 'provider-a', "
            "'control-plane-kit-secrets', 'Provider A', 'provider-a', "
            "'secret://bootstrap/provider-token', %s, %s, 'operator-a', "
            "'2026-08-11T00:00:00Z', 'active')",
            ('["secret://provider-a/"]', allowed_intents),
        )
        self.connection.execute(
            "INSERT INTO cpk_secret_references ("
            "registration_id, workspace_id, secret_reference, "
            "provider_registration_id, allowed_intents, admitted_by, "
            "admitted_at, status"
            ") VALUES ('reference-registration-a', 'workspace-a', "
            "'secret://provider-a/signing/key', 'provider-registration-a', %s, "
            "'operator-a', '2026-08-11T00:00:00Z', 'active')",
            (allowed_intents,),
        )
        for index, intent in enumerate(_NEW_INTENTS, start=1):
            self.connection.execute(
                "INSERT INTO cpk_secret_use_authorizations ("
                "authorization_id, workspace_id, reference_registration_id, "
                "provider_registration_id, secret_reference, use_intent, "
                "actor_subject, correlation_id, requested_at, intent_fingerprint"
                ") VALUES (%s, 'workspace-a', 'reference-registration-a', "
                "'provider-registration-a', 'secret://provider-a/signing/key', "
                "%s, 'operator-a', %s, '2026-08-11T00:00:00Z', %s)",
                (
                    f"suse_{index:064x}",
                    intent,
                    f"authority-use-{index}",
                    f"{index + 20:064x}",
                ),
            )

    def _seed_approval_subject(self) -> None:
        self.connection.execute(
            "INSERT INTO cpk_operation_sessions ("
            "session_id, workspace_id, actor_id, title, status, created_at"
            ") VALUES ('session-a', 'workspace-a', 'operator-a', "
            "'Node control approval boundary', 'open', '2026-08-11T00:00:00Z')"
        )
        self.connection.execute(
            "INSERT INTO cpk_gateway_key_rotations ("
            "rotation_id, workspace_id, gateway_node_id, purpose, issuer, "
            "old_key_id, new_secret_reference, key_generation_correlation, "
            "maximum_grant_lifetime_seconds, clock_skew_seconds, correlation_id, "
            "requested_by, requested_at, intent_fingerprint, status, version"
            ") VALUES ('rotation-approval', 'workspace-a', 'gateway-a', "
            "'gateway-probe', 'issuer-a', 'key-a', "
            "'secret://provider-a/signing/key-c', 'generate-key-c', 120, 10, "
            "'rotate-key-approval', 'operator-a', '2026-08-11T00:00:00Z', "
            "%s, 'requested', 1)",
            ("3" * 64,),
        )
        self.connection.execute(
            "INSERT INTO cpk_approval_requests ("
            "request_id, session_id, rotation_id, subject_kind, subject_payload, "
            "review_digest, requested_by, requested_at, required_scope, "
            "max_risk, destructive"
            ") VALUES ('approval-valid', 'session-a', 'rotation-approval', "
            "'gateway-key-rotation', '{}', %s, 'operator-a', "
            "'2026-08-11T00:00:00Z', 'delegation-key:rotate-approve', "
            "'medium', false)",
            ("f" * 64,),
        )

    def _assert_node_control_scopes_are_not_approval_scopes(self) -> None:
        for index, scope in enumerate(_NODE_CONTROL_SCOPES, start=1):
            with self.subTest(request_scope=scope):
                with self.assertRaises(psycopg.errors.CheckViolation) as raised:
                    self.connection.execute(
                        "INSERT INTO cpk_approval_requests ("
                        "request_id, session_id, rotation_id, subject_kind, "
                        "subject_payload, review_digest, requested_by, requested_at, "
                        "required_scope, max_risk, destructive"
                        ") VALUES (%s, 'session-a', 'rotation-approval', "
                        "'gateway-key-rotation', '{}', %s, 'operator-a', "
                        "'2026-08-11T00:00:00Z', %s, 'medium', false)",
                        (f"approval-invalid-{index}", "e" * 64, scope),
                    )
                self.assertEqual(
                    raised.exception.diag.constraint_name,
                    "cpk_approval_requests_scope_check",
                )
            with self.subTest(decision_scope=scope):
                with self.assertRaises(psycopg.errors.CheckViolation) as raised:
                    self.connection.execute(
                        "INSERT INTO cpk_approval_decisions ("
                        "decision_id, request_id, actor_id, decision, scope, decided_at"
                        ") VALUES (%s, 'approval-valid', 'reviewer-a', 'approved', "
                        "%s, '2026-08-11T00:00:00Z')",
                        (f"decision-invalid-{index}", scope),
                    )
                self.assertEqual(
                    raised.exception.diag.constraint_name,
                    "cpk_approval_decisions_scope_check",
                )

    def _authority_rows(self) -> tuple[tuple[object, ...], ...]:
        return tuple(
            self.connection.execute(
                "SELECT 'key', registration_id, purpose "
                "FROM cpk_delegation_signing_keys UNION ALL "
                "SELECT 'rotation', rotation_id, purpose "
                "FROM cpk_gateway_key_rotations UNION ALL "
                "SELECT 'authorization', authorization_id, use_intent "
                "FROM cpk_secret_use_authorizations ORDER BY 1, 2, 3"
            ).fetchall()
        )

    def _constraint_snapshot(
        self,
        schema: str | None = None,
    ) -> tuple[tuple[object, ...], ...]:
        return tuple(
            self.connection.execute(
                "SELECT relation.relname, owned.conname, owned.oid::bigint, "
                "owned.convalidated, pg_get_expr(owned.conbin, owned.conrelid) "
                "FROM pg_constraint AS owned "
                "JOIN pg_class AS relation ON relation.oid=owned.conrelid "
                "JOIN pg_namespace AS namespace "
                "ON namespace.oid=relation.relnamespace "
                "WHERE namespace.nspname=%s ORDER BY 1, 2, 3",
                (self.schema if schema is None else schema,),
            ).fetchall()
        )

    def _assert_calls_are_read_only(self, calls: list[str]) -> None:
        for call in calls:
            self.assertNotRegex(
                " ".join(call.lower().split()),
                r"\b(create|alter|drop|truncate|insert|update|delete)\b",
            )

    def _assert_install_error(self, error: BaseException, message: str) -> None:
        error_type = getattr(postgres, "SchemaInstallationError", None)
        self.assertIsNotNone(error_type)
        self.assertIs(type(error), error_type)
        self.assertEqual(str(error), message)
        self.assertIsNone(error.__cause__)
        self.assertIsNone(error.__context__)

    def _reset_owned_schema(self) -> None:
        self.connection.execute("SET search_path TO public")
        self.connection.execute(f'DROP SCHEMA "{self.schema}" CASCADE')
        self.connection.execute(f'CREATE SCHEMA "{self.schema}"')
        self.connection.execute(f'SET search_path TO "{self.schema}"')

    def _relations(self) -> tuple[str, ...]:
        return tuple(
            row[0]
            for row in self.connection.execute(
                "SELECT relation.relname FROM pg_class AS relation "
                "JOIN pg_namespace AS namespace ON namespace.oid=relation.relnamespace "
                "WHERE namespace.nspname=current_schema() AND relation.relkind='r' "
                "ORDER BY relation.relname"
            ).fetchall()
        )

    def _catalog_counts(self) -> tuple[int, int, int, int]:
        relation_count = self.connection.execute(
            "SELECT count(*) FROM pg_class AS relation "
            "JOIN pg_namespace AS namespace ON namespace.oid=relation.relnamespace "
            "WHERE namespace.nspname=current_schema() AND relation.relkind='r'"
        ).fetchone()[0]
        column_count = self.connection.execute(
            "SELECT count(*) FROM pg_attribute AS attribute "
            "JOIN pg_class AS relation ON relation.oid=attribute.attrelid "
            "JOIN pg_namespace AS namespace ON namespace.oid=relation.relnamespace "
            "WHERE namespace.nspname=current_schema() AND relation.relkind='r' "
            "AND attribute.attnum > 0 AND NOT attribute.attisdropped"
        ).fetchone()[0]
        constraint_count = self.connection.execute(
            "SELECT count(*) FROM pg_constraint AS owned "
            "JOIN pg_class AS relation ON relation.oid=owned.conrelid "
            "JOIN pg_namespace AS namespace ON namespace.oid=relation.relnamespace "
            "WHERE namespace.nspname=current_schema()"
        ).fetchone()[0]
        index_count = self.connection.execute(
            "SELECT count(*) FROM pg_class AS indexed "
            "JOIN pg_namespace AS namespace ON namespace.oid=indexed.relnamespace "
            "WHERE namespace.nspname=current_schema() AND indexed.relkind='i'"
        ).fetchone()[0]
        return relation_count, column_count, constraint_count, index_count

    def _namespace_objects(self) -> tuple[tuple[str, str], ...]:
        return tuple(
            self.connection.execute(
                "SELECT 'relation', relation.relname FROM pg_class AS relation "
                "JOIN pg_namespace AS namespace ON namespace.oid=relation.relnamespace "
                "WHERE namespace.nspname=current_schema() "
                "UNION ALL "
                "SELECT 'routine', routine.proname FROM pg_proc AS routine "
                "JOIN pg_namespace AS namespace ON namespace.oid=routine.pronamespace "
                "WHERE namespace.nspname=current_schema() "
                "UNION ALL "
                "SELECT 'type', owned_type.typname FROM pg_type AS owned_type "
                "JOIN pg_namespace AS namespace ON namespace.oid=owned_type.typnamespace "
                "WHERE namespace.nspname=current_schema() "
                "ORDER BY 1, 2"
            ).fetchall()
        )

    def _object_identities(self) -> tuple[tuple[object, ...], ...]:
        return tuple(
            self.connection.execute(
                "SELECT 'relation', relation.relname, relation.oid::bigint "
                "FROM pg_class AS relation "
                "JOIN pg_namespace AS namespace ON namespace.oid=relation.relnamespace "
                "WHERE namespace.nspname=current_schema() "
                "UNION ALL "
                "SELECT 'constraint', owned.conname, owned.oid::bigint "
                "FROM pg_constraint AS owned "
                "JOIN pg_class AS relation ON relation.oid=owned.conrelid "
                "JOIN pg_namespace AS namespace ON namespace.oid=relation.relnamespace "
                "WHERE namespace.nspname=current_schema() "
                "ORDER BY 1, 2, 3"
            ).fetchall()
        )


if __name__ == "__main__":
    unittest.main()
