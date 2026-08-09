from __future__ import annotations

import ast
from dataclasses import replace
from datetime import datetime, timezone
import inspect
import os
import unittest
import uuid

import psycopg

import control_plane_kit_operations.postgres as postgres
import control_plane_kit_operations.postgres.schema as schema_module
import control_plane_kit_operations.secret_providers as secret_provider_module
from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_core.secrets import SecretReference, SecretUseIntent
from control_plane_kit_operations.postgres.secret_provider_store import (
    SecretUseAuthorizationStore,
)
from control_plane_kit_operations.secret_providers import (
    AuthorizeSecretUse,
    AuthorizedSecretUse,
    SecretProviderAuthorizationDenied,
    SecretUseAuthorizationService,
)


_V8_HISTORY = [
    (1, "operations-baseline"),
    (2, "coordination-timestamps"),
    (3, "graph-product-authority-timestamps"),
    (4, "secret-registration-timestamps"),
    (5, "delegation-signing-key-timestamps"),
    (6, "gateway-probe-timestamps"),
    (7, "gateway-key-rotation-timestamps"),
    (8, "ingress-evidence-timestamps"),
]
_V9_HISTORY = [*_V8_HISTORY, (9, "secret-use-authorization-timestamps")]
_CURRENT_HISTORY = [
    *_V9_HISTORY,
    (10, "product-descriptor-content"),
    (11, "gateway-probe-access-path"),
    (12, "gateway-key-rotation-generation-evidence"),
]
_V9_SHA256 = "51e322bc4c578bef768cd516b63fd0018cfeb658bd4b9bfd6eed118666d50adb"
_SECONDS = "2026-08-08T12:00:00Z"
_MICROS = "2026-08-08T12:00:00.000001Z"
_OFFSET = "2026-08-08T08:00:00-04:00"
_COLUMN = (
    "cpk_secret_use_authorizations",
    "requested_at",
    "timestamp with time zone",
    6,
    "NO",
    True,
)
_REBUILT = {
    ("constraint", "cpk_gateway_key_rotations_generation_digest_check"),
    ("index", "cpk_secret_use_authorizations_reference_history"),
}
_CANONICAL_DIGEST_CONSTRAINT = (
    "constraint",
    "cpk_gateway_key_rotations_generation_digest_check",
)
_CANONICAL_DIGEST_DEFINITION = (
    "CHECK (((generation_action_digest IS NULL) OR "
    '((generation_action_digest COLLATE "C") ~ '
    "'^[0-9a-f]{64}$'::text)))"
)
_CURRENT_ADDED_OBJECTS = {
    ("constraint", "cpk_registered_products_content_digest_check"),
    ("constraint", "cpk_gateway_key_rotations_generation_provider_check"),
}


class _NoAccessConnection:
    def __init__(self) -> None:
        self.accesses = 0

    def execute(self, *args: object, **kwargs: object) -> object:
        del args, kwargs
        self.accesses += 1
        raise AssertionError("connection access preceded timestamp admission")


class _CountingUnitOfWorkFactory:
    def __init__(self) -> None:
        self.calls = 0

    def __call__(self) -> object:
        self.calls += 1
        raise ValueError("unit of work opened")


class SecretUseAuthorizationTimestampMigrationTests(unittest.TestCase):
    def setUp(self) -> None:
        database_url = os.environ.get("CPK_OPERATIONS_TEST_DATABASE_URL")
        if not database_url:
            raise RuntimeError(
                "CPK_OPERATIONS_TEST_DATABASE_URL is required; use Docker-first tests"
            )
        self.schema = f"secret_use_time_{uuid.uuid4().hex}"
        self.connection = psycopg.connect(database_url, autocommit=True)
        self.connection.execute(f'CREATE SCHEMA "{self.schema}"')
        self.connection.execute(f'SET search_path TO "{self.schema}"')

    def tearDown(self) -> None:
        self.connection.execute("SET search_path TO public")
        self.connection.execute(f'DROP SCHEMA IF EXISTS "{self.schema}" CASCADE')
        self.connection.close()

    def test_registry_appends_checksum_guarded_v9_after_immutable_v8(self) -> None:
        registry = postgres.POSTGRES_SCHEMA_MIGRATIONS

        self.assertEqual(registry.target_version, 12)
        self.assertEqual(
            [(migration.version, migration.name) for migration in registry.migrations],
            _CURRENT_HISTORY,
        )
        self.assertEqual(
            [(migration.version, migration.name) for migration in registry.migrations[:8]],
            _V8_HISTORY,
        )
        self.assertEqual(registry.migrations[8].checksum_sha256, _V9_SHA256)
        self.assertEqual(
            registry.migrations[8].checksum_sha256,
            getattr(schema_module, "_POSTGRES_SCHEMA_V9_SHA256", None),
        )
        tree = ast.parse(inspect.getsource(schema_module))
        self.assertTrue(
            any(
                isinstance(node, ast.If)
                and isinstance(node.test, ast.Compare)
                and isinstance(node.test.left, ast.Attribute)
                and isinstance(node.test.left.value, ast.Name)
                and node.test.left.value.id == "_POSTGRES_SCHEMA_V9"
                and node.test.left.attr == "checksum_sha256"
                and any(isinstance(statement, ast.Raise) for statement in node.body)
                for node in ast.walk(tree)
            ),
            "V9 SQL must be protected by an import-time checksum guard",
        )

    def test_fresh_install_has_exact_v9_contract_and_history(self) -> None:
        postgres.install_postgres_schema(self.connection)

        self.assertEqual(self._ledger(), _CURRENT_HISTORY)
        self.assertEqual(self._column_contract(), _COLUMN)
        self.assertIs(
            postgres.verify_postgres_schema(self.connection).kind,
            postgres.ObservedSchemaKind.VERSIONED,
        )

    def test_retained_authorization_migrates_without_non_temporal_change(self) -> None:
        self._install_v8_baseline()
        self._seed_authorization(index=1, requested_at=_MICROS)
        before = self._retained_non_temporal()

        postgres.install_postgres_schema(self.connection)

        self.assertEqual(self._retained_non_temporal(), before)
        self.assertEqual(
            self.connection.execute(
                "SELECT requested_at FROM cpk_secret_use_authorizations"
            ).fetchone(),
            (datetime(2026, 8, 8, 12, 0, 0, 1, tzinfo=timezone.utc),),
        )
        self.assertEqual(self._ledger(), _CURRENT_HISTORY)

    def test_lexical_and_calendar_failures_roll_back_every_fact(self) -> None:
        for label, invalid in (
            ("lexical", _OFFSET),
            ("calendar", "2026-02-30T12:00:00Z"),
        ):
            with self.subTest(label=label):
                case_schema = f"{self.schema}_{label}"
                self.connection.execute(f'CREATE SCHEMA "{case_schema}"')
                self.connection.execute(f'SET search_path TO "{case_schema}"')
                try:
                    self._install_v8_baseline()
                    self._seed_authorization(index=1, requested_at=invalid)
                    before_row = self.connection.execute(
                        "SELECT * FROM cpk_secret_use_authorizations"
                    ).fetchone()
                    before_objects = self._application_objects()

                    with self.assertRaises(postgres.SchemaMigrationError) as raised:
                        postgres.install_postgres_schema(self.connection)

                    self.assertEqual(
                        str(raised.exception),
                        "secret-use authorization timestamps are not canonical UTC",
                    )
                    for forbidden in (
                        invalid,
                        self.schema,
                        "secret://",
                        "correlation-",
                        "SELECT",
                        "ALTER TABLE",
                    ):
                        self.assertNotIn(forbidden, str(raised.exception))
                    self.assertLessEqual(len(str(raised.exception)), 256)
                    self.assertIsNone(raised.exception.__context__)
                    self.assertIsNone(raised.exception.__cause__)
                    self.assertEqual(self._ledger(), _V8_HISTORY)
                    self.assertEqual(
                        self.connection.execute(
                            "SELECT * FROM cpk_secret_use_authorizations"
                        ).fetchone(),
                        before_row,
                    )
                    self.assertEqual(self._application_objects(), before_objects)
                    self.assertEqual(self._column_contract()[2], "text")
                finally:
                    self.connection.execute(f'SET search_path TO "{self.schema}"')
                    self.connection.execute(f'DROP SCHEMA "{case_schema}" CASCADE')

    def test_success_rebuilds_only_reference_history_index(self) -> None:
        self._install_v8_baseline()
        self._seed_authorization(index=1, requested_at=_SECONDS)
        before = self._application_objects()

        postgres.install_postgres_schema(self.connection)

        after = self._application_objects()
        self.assertEqual(set(after), set(before) | _CURRENT_ADDED_OBJECTS)
        changed = set()
        for identity, (before_oid, before_definition) in before.items():
            after_oid, after_definition = after[identity]
            if identity == _CANONICAL_DIGEST_CONSTRAINT:
                self.assertEqual(after_definition, _CANONICAL_DIGEST_DEFINITION)
            else:
                self.assertEqual(after_definition, before_definition)
            if after_oid != before_oid:
                changed.add(identity)
        self.assertEqual(changed, _REBUILT)

    def test_verifier_rejects_each_owned_temporal_fact(self) -> None:
        mutations = (
            ("type", "TYPE text USING requested_at::text"),
            ("precision", "TYPE timestamptz(5) USING requested_at::timestamptz(5)"),
            ("nullability", "DROP NOT NULL"),
            ("default", "SET DEFAULT clock_timestamp()"),
        )
        for index, (fact, mutation) in enumerate(mutations):
            with self.subTest(fact=fact):
                case_schema = f"{self.schema}_verify_{index}"
                self.connection.execute(f'CREATE SCHEMA "{case_schema}"')
                self.connection.execute(f'SET search_path TO "{case_schema}"')
                try:
                    postgres.install_postgres_schema(self.connection)
                    self.connection.execute(
                        "ALTER TABLE cpk_secret_use_authorizations "
                        f"ALTER COLUMN requested_at {mutation}"
                    )

                    with self.assertRaises(postgres.SchemaMigrationError) as raised:
                        postgres.verify_postgres_schema(self.connection)

                    self.assertEqual(
                        str(raised.exception),
                        "secret-use authorization temporal schema is not current",
                    )
                    self.assertIsNone(raised.exception.__context__)
                    self.assertIsNone(raised.exception.__cause__)
                finally:
                    self.connection.execute(f'SET search_path TO "{self.schema}"')
                    self.connection.execute(f'DROP SCHEMA "{case_schema}" CASCADE')

    def test_malformed_service_times_precede_both_entrypoint_uows(self) -> None:
        for entrypoint in ("authorize", "authorize_resolution"):
            for label, command in (
                ("first", self._command(requested_at="not-a-timestamp")),
                ("identical", self._command(requested_at="not-a-timestamp")),
                (
                    "conflicting",
                    self._command(requested_at="not-a-timestamp", run_id="run-b"),
                ),
            ):
                with self.subTest(entrypoint=entrypoint, label=label):
                    factory = _CountingUnitOfWorkFactory()
                    service = SecretUseAuthorizationService(factory)
                    with self.assertRaises(ValueError) as raised:
                        getattr(service, entrypoint)(command)
                    self.assertEqual(factory.calls, 0)
                    self.assertEqual(
                        str(raised.exception),
                        "timestamp must be canonical UTC text",
                    )
                    for forbidden in (
                        command.requested_at,
                        command.reference.reference_id,
                        command.correlation_id,
                        command.run_id or "",
                    ):
                        if forbidden:
                            self.assertNotIn(forbidden, str(raised.exception))

    def test_scope_admission_precedes_time_admission_for_both_entrypoints(self) -> None:
        command = replace(
            self._command(requested_at="not-a-timestamp"),
            actor_scopes=(PolicyScope.PLAN_REQUEST,),
        )
        for entrypoint in ("authorize", "authorize_resolution"):
            with self.subTest(entrypoint=entrypoint):
                factory = _CountingUnitOfWorkFactory()
                service = SecretUseAuthorizationService(factory)

                with self.assertRaises(SecretProviderAuthorizationDenied) as raised:
                    getattr(service, entrypoint)(command)

                self.assertEqual(factory.calls, 0)
                self.assertEqual(
                    str(raised.exception),
                    "secret provider operation requires secret-provider:use",
                )
                self.assertNotIn(command.requested_at, str(raised.exception))

    def test_direct_add_admits_time_before_connection_access(self) -> None:
        connection = _NoAccessConnection()
        store = SecretUseAuthorizationStore(connection)

        with self.assertRaises((ValueError, AssertionError)):
            store.add(self._authorized(requested_at="not-a-timestamp"))

        self.assertEqual(connection.accesses, 0)

    def test_both_read_selectors_decode_seconds_and_microseconds_in_utc(self) -> None:
        postgres.install_postgres_schema(self.connection)
        self._seed_authorization(
            index=1,
            requested_at=datetime(2026, 8, 8, 12, tzinfo=timezone.utc),
        )
        self._seed_authorization(
            index=2,
            requested_at=datetime(
                2026, 8, 8, 12, 0, 0, 1, tzinfo=timezone.utc
            ),
        )
        self.connection.execute("SET TIME ZONE 'America/Los_Angeles'")
        store = SecretUseAuthorizationStore(self.connection)

        seconds = store.get("workspace-a", "suse_" + "1" * 64)
        micros = store.for_correlation("workspace-a", "correlation-2")

        self.assertEqual(seconds.requested_at, _SECONDS)
        self.assertIsNotNone(micros)
        self.assertEqual(micros.requested_at, _MICROS)

    def test_service_boundary_has_no_postgres_or_history_api(self) -> None:
        tree = ast.parse(inspect.getsource(secret_provider_module))
        imported = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        imported.update(
            node.module or ""
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
        )
        self.assertFalse(
            any(name.startswith("control_plane_kit_operations.postgres") for name in imported)
        )
        public_methods = {
            node.name
            for node in ast.parse(inspect.getsource(SecretUseAuthorizationStore)).body[0].body
            if isinstance(node, ast.FunctionDef) and not node.name.startswith("_")
        }
        self.assertEqual(
            public_methods,
            {"lock_correlation", "add", "get", "for_correlation"},
        )

    def _install_v8_baseline(self) -> None:
        registry = postgres.POSTGRES_SCHEMA_MIGRATIONS
        self.connection.execute(postgres.POSTGRES_SCHEMA)
        for migration in registry.migrations[1:8]:
            self.connection.execute(migration.sql)
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
        for migration in registry.migrations[:8]:
            self.connection.execute(
                """
                INSERT INTO cpk_schema_migrations (version, name, checksum_sha256)
                VALUES (%s, %s, %s)
                """,
                (migration.version, migration.name, migration.checksum_sha256),
            )
        self.connection.execute(schema_module._GRAPH_LINEAGE_CONSTRAINTS)

    def _seed_authorization(self, *, index: int, requested_at: object) -> None:
        if self.connection.execute(
            "SELECT 1 FROM cpk_workspaces WHERE workspace_id = 'workspace-a'"
        ).fetchone() is None:
            self.connection.execute(
                "INSERT INTO cpk_workspaces (workspace_id, name, lifecycle) "
                "VALUES ('workspace-a', 'Workspace A', 'created')"
            )
            self.connection.execute(
                """
                INSERT INTO cpk_secret_providers (
                  registration_id, workspace_id, provider_id, provider_kind,
                  display_name, endpoint_reference, credential_reference,
                  allowed_reference_prefixes, allowed_intents, admitted_by,
                  admitted_at, status, metadata
                ) VALUES (
                  'provider-registration', 'workspace-a', 'workspace-secrets',
                  'control-plane-kit-secrets', 'Workspace secrets', 'secrets-endpoint',
                  'secret://workspace-secrets/provider/credential',
                  '["secret://workspace-secrets/"]'::jsonb,
                  '["postgres.password"]'::jsonb, 'operator-a',
                  '2026-08-08T11:00:00Z', 'active', '{}'::jsonb
                )
                """
            )
            self.connection.execute(
                """
                INSERT INTO cpk_secret_references (
                  registration_id, workspace_id, secret_reference,
                  provider_registration_id, allowed_intents, admitted_by,
                  admitted_at, status, metadata
                ) VALUES (
                  'reference-registration', 'workspace-a',
                  'secret://workspace-secrets/postgres/password',
                  'provider-registration', '["postgres.password"]'::jsonb,
                  'operator-a', '2026-08-08T11:30:00Z', 'active', '{}'::jsonb
                )
                """
            )
        self.connection.execute(
            """
            INSERT INTO cpk_secret_use_authorizations (
              authorization_id, workspace_id, reference_registration_id,
              provider_registration_id, secret_reference, use_intent,
              actor_subject, correlation_id, requested_at, intent_fingerprint,
              operation_id, session_id, run_id, activity_id, effect_id, probe_id
            ) VALUES (
              %s, 'workspace-a', 'reference-registration',
              'provider-registration',
              'secret://workspace-secrets/postgres/password',
              'postgres.password', 'operator-a', %s, %s, %s,
              %s, %s, %s, %s, %s, %s
            )
            """,
            (
                "suse_" + str(index) * 64,
                f"correlation-{index}",
                requested_at,
                str(index) * 64,
                f"operation-{index}",
                f"session-{index}",
                f"run-{index}",
                f"activity-{index}",
                f"effect-{index}",
                f"probe-{index}",
            ),
        )

    def _retained_non_temporal(self) -> tuple[object, ...]:
        return self.connection.execute(
            """
            SELECT authorization_id, workspace_id, reference_registration_id,
                   provider_registration_id, secret_reference, use_intent,
                   actor_subject, correlation_id, intent_fingerprint,
                   operation_id, session_id, run_id, activity_id, effect_id, probe_id
            FROM cpk_secret_use_authorizations
            """
        ).fetchone()

    def _ledger(self) -> list[tuple[int, str]]:
        return self.connection.execute(
            "SELECT version, name FROM cpk_schema_migrations ORDER BY version"
        ).fetchall()

    def _column_contract(self) -> tuple[object, ...]:
        return self.connection.execute(
            """
            SELECT table_name, column_name, data_type, datetime_precision,
                   is_nullable, column_default IS NULL
            FROM information_schema.columns
            WHERE table_schema = current_schema()
              AND table_name = 'cpk_secret_use_authorizations'
              AND column_name = 'requested_at'
            """
        ).fetchone()

    def _application_objects(self) -> dict[tuple[str, str], tuple[int, str]]:
        constraints = self.connection.execute(
            """
            SELECT conname, oid, pg_get_constraintdef(oid)
            FROM pg_constraint
            WHERE connamespace = current_schema()::regnamespace
            ORDER BY conname
            """
        ).fetchall()
        indexes = self.connection.execute(
            """
            SELECT indexname,
                   (quote_ident(schemaname) || '.' || quote_ident(indexname))::regclass::oid,
                   indexdef
            FROM pg_indexes
            WHERE schemaname = current_schema()
            ORDER BY indexname
            """
        ).fetchall()
        return {
            **{("constraint", name): (oid, definition) for name, oid, definition in constraints},
            **{("index", name): (oid, definition) for name, oid, definition in indexes},
        }

    @staticmethod
    def _command(
        *,
        requested_at: str,
        run_id: str = "run-a",
    ) -> AuthorizeSecretUse:
        return AuthorizeSecretUse(
            workspace_id="workspace-a",
            reference=SecretReference(
                "secret://workspace-secrets/postgres/password"
            ),
            intent=SecretUseIntent.POSTGRES_PASSWORD,
            actor_subject="operator-a",
            correlation_id="correlation-a",
            requested_at=requested_at,
            actor_scopes=(PolicyScope.SECRET_PROVIDER_USE,),
            operation_id="operation-a",
            session_id="session-a",
            run_id=run_id,
            activity_id="activity-a",
            effect_id="effect-a",
            probe_id="probe-a",
        )

    @staticmethod
    def _authorized(*, requested_at: str) -> AuthorizedSecretUse:
        return AuthorizedSecretUse(
            authorization_id="suse_" + "a" * 64,
            workspace_id="workspace-a",
            reference_registration_id="reference-registration",
            provider_registration_id="provider-registration",
            reference=SecretReference(
                "secret://workspace-secrets/postgres/password"
            ),
            intent=SecretUseIntent.POSTGRES_PASSWORD,
            actor_subject="operator-a",
            correlation_id="correlation-a",
            requested_at=requested_at,
            intent_fingerprint="b" * 64,
            operation_id="operation-a",
            session_id="session-a",
            run_id="run-a",
            activity_id="activity-a",
            effect_id="effect-a",
            probe_id="probe-a",
        )


if __name__ == "__main__":
    unittest.main()
