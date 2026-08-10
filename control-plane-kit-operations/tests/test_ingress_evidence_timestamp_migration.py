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
from control_plane_kit_core.public_ingress import (
    IngressAuthorityReference,
    PublicIngressLifecycle,
)
from control_plane_kit_core.secrets import SecretReference
from control_plane_kit_operations.ingress_authorities import (
    CloudflareOwnedIngressResource,
    GeneratedIngressSecretReference,
    GeneratedSecretPurpose,
    IngressAuthorityProviderKind,
    OwnedIngressResourceStatus,
)
from control_plane_kit_operations.postgres.ingress_authority_store import (
    GeneratedIngressSecretReferenceStore,
    IngressResourceStore,
)


_V7_HISTORY = [
    (1, "operations-baseline"),
    (2, "coordination-timestamps"),
    (3, "graph-product-authority-timestamps"),
    (4, "secret-registration-timestamps"),
    (5, "delegation-signing-key-timestamps"),
    (6, "gateway-probe-timestamps"),
    (7, "gateway-key-rotation-timestamps"),
]
_V8_HISTORY = [*_V7_HISTORY, (8, "ingress-evidence-timestamps")]
_CURRENT_HISTORY = [
    *_V8_HISTORY,
    (9, "secret-use-authorization-timestamps"),
    (10, "product-descriptor-content"),
    (11, "gateway-probe-access-path"),
    (12, "gateway-key-rotation-generation-evidence"),
    (13, "gateway-key-rotation-status-contracts"),
    (14, "gateway-key-rotation-retirement-evidence"),
    (15, "approval-subject-evidence"),
    (16, "approval-scope-contracts"),
    (17, "graph-lineage-compatibility"),
]
_TEMPORAL_COLUMNS = (
    (
        "cpk_cloudflare_ingress_resources",
        "created_at",
        "timestamp with time zone",
        6,
        "NO",
        True,
    ),
    (
        "cpk_cloudflare_ingress_resources",
        "observed_at",
        "timestamp with time zone",
        6,
        "NO",
        True,
    ),
    (
        "cpk_cloudflare_ingress_resources",
        "removed_at",
        "timestamp with time zone",
        6,
        "YES",
        True,
    ),
    (
        "cpk_generated_ingress_secret_references",
        "recorded_at",
        "timestamp with time zone",
        6,
        "NO",
        True,
    ),
)
_TEMPORAL_IDENTITIES = tuple((row[0], row[1]) for row in _TEMPORAL_COLUMNS)
_SECONDS = "2026-08-08T12:00:00Z"
_MICROS = "2026-08-08T12:00:00.000001Z"
_LATER = "2026-08-08T13:00:00Z"
_OFFSET = "2026-08-08T08:00:00-04:00"
_EXPECTED_REBUILT = {
    ("constraint", "cpk_approval_requests_review_digest_check"),
    ("constraint", "cpk_cloudflare_ingress_resources_removed_evidence_check"),
    ("constraint", "cpk_gateway_key_rotations_generation_digest_check"),
    ("index", "cpk_cloudflare_ingress_resources_workspace"),
    ("index", "cpk_secret_use_authorizations_reference_history"),
}
_CANONICAL_DIGEST_CONSTRAINT = (
    "constraint",
    "cpk_gateway_key_rotations_generation_digest_check",
)
_APPROVAL_DIGEST_CONSTRAINT = (
    "constraint",
    "cpk_approval_requests_review_digest_check",
)
_APPROVAL_DIGEST_DEFINITION = (
    'CHECK (((review_digest COLLATE "C") ~ '
    "'^[0-9a-f]{64}$'::text))"
)
_CANONICAL_DIGEST_DEFINITION = (
    "CHECK (((generation_action_digest IS NULL) OR "
    '((generation_action_digest COLLATE "C") ~ '
    "'^[0-9a-f]{64}$'::text)))"
)
_CURRENT_ADDED_OBJECTS = {
    ("constraint", "cpk_activity_plans_desired_graph_revision_check"),
    ("constraint", "cpk_workspaces_desired_graph_revision_check"),
    ("constraint", "cpk_registered_products_content_digest_check"),
    ("constraint", "cpk_gateway_key_rotations_generation_provider_check"),
}
_V8_SHA256 = "3e7cb7c70c64511d76be9406588d2edc24fa3c9a62d95fd42d7a84fb3946069c"


class _NoAccessConnection:
    def __init__(self) -> None:
        self.accesses = 0

    def execute(self, *args: object, **kwargs: object) -> object:
        del args, kwargs
        self.accesses += 1
        raise AssertionError("timestamp admission must precede connection access")


class IngressEvidenceTimestampMigrationTests(unittest.TestCase):
    def setUp(self) -> None:
        database_url = os.environ.get("CPK_OPERATIONS_TEST_DATABASE_URL")
        if not database_url:
            raise RuntimeError(
                "CPK_OPERATIONS_TEST_DATABASE_URL is required; use Docker-first tests"
            )
        self.schema = f"ingress_evidence_time_{uuid.uuid4().hex}"
        self.connection = psycopg.connect(database_url, autocommit=True)
        self.connection.execute(f'CREATE SCHEMA "{self.schema}"')
        self.connection.execute(f'SET search_path TO "{self.schema}"')

    def tearDown(self) -> None:
        self.connection.execute("SET search_path TO public")
        self.connection.execute(f'DROP SCHEMA IF EXISTS "{self.schema}" CASCADE')
        self.connection.close()

    def test_registry_appends_checksum_guarded_v8_after_immutable_v7(self) -> None:
        registry = postgres.POSTGRES_SCHEMA_MIGRATIONS

        self.assertEqual(registry.target_version, 17)
        self.assertEqual(
            [(item.version, item.name) for item in registry.migrations],
            _CURRENT_HISTORY,
        )
        self.assertEqual(
            [(item.version, item.name) for item in registry.migrations[:7]],
            _V7_HISTORY,
        )
        self.assertEqual(registry.migrations[7].checksum_sha256, _V8_SHA256)
        self.assertEqual(
            registry.migrations[7].checksum_sha256,
            getattr(schema_module, "_POSTGRES_SCHEMA_V8_SHA256", None),
        )
        tree = ast.parse(inspect.getsource(schema_module))
        self.assertTrue(
            any(
                isinstance(node, ast.If)
                and isinstance(node.test, ast.Compare)
                and isinstance(node.test.left, ast.Attribute)
                and isinstance(node.test.left.value, ast.Name)
                and node.test.left.value.id == "_POSTGRES_SCHEMA_V8"
                and node.test.left.attr == "checksum_sha256"
                and any(isinstance(statement, ast.Raise) for statement in node.body)
                for node in ast.walk(tree)
            ),
            "V8 SQL must be protected by an import-time checksum guard",
        )

    def test_fresh_install_has_exact_v8_temporal_contract(self) -> None:
        postgres.install_postgres_schema(self.connection)

        self.assertEqual(self._ledger(), _CURRENT_HISTORY)
        self.assertEqual(self._temporal_contract(), _TEMPORAL_COLUMNS)
        self.assertIs(
            postgres.verify_postgres_schema(self.connection).kind,
            postgres.ObservedSchemaKind.VERSIONED,
        )

    def test_retained_status_and_secret_rows_migrate_without_identity_loss(self) -> None:
        self._install_v7_baseline()
        self.connection.execute("SET TIME ZONE 'America/New_York'")
        self._seed_workspace()
        statuses = tuple(OwnedIngressResourceStatus)
        for index, status in enumerate(statuses):
            removed = status is OwnedIngressResourceStatus.REMOVED
            self._insert_resource(
                index=index,
                status=status.value,
                created_at=_SECONDS if index % 2 == 0 else _MICROS,
                observed_at=_MICROS if index % 2 == 0 else _SECONDS,
                removed_at=_LATER if removed else None,
                removed_by_run_id="run-removed" if removed else None,
            )
        self._insert_secret(index=1, recorded_at=_MICROS)
        before_non_temporal = self._retained_non_temporal()

        postgres.install_postgres_schema(self.connection)

        self.assertEqual(self._retained_non_temporal(), before_non_temporal)
        expected_seconds = datetime(2026, 8, 8, 12, tzinfo=timezone.utc)
        expected_micros = datetime(2026, 8, 8, 12, 0, 0, 1, tzinfo=timezone.utc)
        expected_later = datetime(2026, 8, 8, 13, tzinfo=timezone.utc)
        rows = self.connection.execute(
            """
            SELECT created_at, observed_at, removed_at
            FROM cpk_cloudflare_ingress_resources
            ORDER BY ingress_id
            """
        ).fetchall()
        for index, (created_at, observed_at, removed_at) in enumerate(rows):
            self.assertEqual(
                created_at,
                expected_seconds if index % 2 == 0 else expected_micros,
            )
            self.assertEqual(
                observed_at,
                expected_micros if index % 2 == 0 else expected_seconds,
            )
            expected_removed = (
                expected_later if statuses[index] is OwnedIngressResourceStatus.REMOVED else None
            )
            self.assertEqual(removed_at, expected_removed)
        self.assertEqual(
            self.connection.execute(
                "SELECT recorded_at FROM cpk_generated_ingress_secret_references"
            ).fetchone(),
            (expected_micros,),
        )

    def test_each_column_has_atomic_lexical_and_calendar_failure(self) -> None:
        for label, invalid in (("lexical", _OFFSET), ("calendar", "2026-02-30T12:00:00Z")):
            for index, identity in enumerate(_TEMPORAL_IDENTITIES):
                with self.subTest(label=label, identity=identity):
                    case_schema = f"{self.schema}_{label}_{index}"
                    self.connection.execute(f'CREATE SCHEMA "{case_schema}"')
                    self.connection.execute(f'SET search_path TO "{case_schema}"')
                    try:
                        self._install_v7_baseline()
                        self._seed_workspace()
                        values = {
                            ("cpk_cloudflare_ingress_resources", "created_at"): _SECONDS,
                            ("cpk_cloudflare_ingress_resources", "observed_at"): _MICROS,
                            ("cpk_cloudflare_ingress_resources", "removed_at"): _LATER,
                            ("cpk_generated_ingress_secret_references", "recorded_at"): _SECONDS,
                        }
                        values[identity] = invalid
                        self._insert_resource(
                            index=index,
                            status="removed",
                            created_at=values[("cpk_cloudflare_ingress_resources", "created_at")],
                            observed_at=values[("cpk_cloudflare_ingress_resources", "observed_at")],
                            removed_at=values[("cpk_cloudflare_ingress_resources", "removed_at")],
                            removed_by_run_id="run-removed",
                        )
                        self._insert_secret(
                            index=index,
                            recorded_at=values[("cpk_generated_ingress_secret_references", "recorded_at")],
                        )
                        rows = self._raw_times()
                        objects = self._application_objects()

                        with self.assertRaises(postgres.SchemaMigrationError) as raised:
                            postgres.install_postgres_schema(self.connection)

                        self.assertEqual(
                            str(raised.exception),
                            "ingress evidence timestamps are not canonical UTC",
                        )
                        self.assertLessEqual(len(str(raised.exception)), 256)
                        self.assertNotIn(invalid, str(raised.exception))
                        self.assertNotIn(self.schema, str(raised.exception))
                        self.assertIsNone(raised.exception.__context__)
                        self.assertIsNone(raised.exception.__cause__)
                        self.assertEqual(self._ledger(), _V7_HISTORY)
                        self.assertEqual(self._raw_times(), rows)
                        self.assertEqual(self._application_objects(), objects)
                        self.assertEqual(
                            self._temporal_contract(),
                            tuple(
                                (table, column, "text", None, nullable, True)
                                for table, column, _kind, _precision, nullable, _default
                                in _TEMPORAL_COLUMNS
                            ),
                        )
                    finally:
                        self.connection.execute(f'SET search_path TO "{self.schema}"')
                        self.connection.execute(f'DROP SCHEMA "{case_schema}" CASCADE')

    def test_success_rebuilds_only_exact_changed_column_dependents(self) -> None:
        self._install_v7_baseline()
        self._seed_workspace()
        self._insert_resource(
            index=1,
            status="removed",
            created_at=_SECONDS,
            observed_at=_MICROS,
            removed_at=_LATER,
            removed_by_run_id="run-removed",
        )
        self._insert_secret(index=1, recorded_at=_MICROS)
        before = self._application_objects()

        postgres.install_postgres_schema(self.connection)

        after = self._application_objects()
        self.assertEqual(set(after), set(before) | _CURRENT_ADDED_OBJECTS)
        changed = set()
        for identity, (before_oid, before_definition) in before.items():
            after_oid, after_definition = after[identity]
            if identity == _CANONICAL_DIGEST_CONSTRAINT:
                self.assertEqual(after_definition, _CANONICAL_DIGEST_DEFINITION)
            elif identity == _APPROVAL_DIGEST_CONSTRAINT:
                self.assertEqual(after_definition, _APPROVAL_DIGEST_DEFINITION)
            else:
                self.assertEqual(after_definition, before_definition)
            if before_oid != after_oid:
                changed.add(identity)
        self.assertEqual(changed, _EXPECTED_REBUILT)
        self.assertEqual(
            after[("index", "cpk_generated_ingress_secret_references_secret_ref")],
            before[("index", "cpk_generated_ingress_secret_references_secret_ref")],
        )

    def test_current_verifier_rejects_all_four_facts_for_each_column(self) -> None:
        for identity_index, (table, column) in enumerate(_TEMPORAL_IDENTITIES):
            nullable = next(row[4] for row in _TEMPORAL_COLUMNS if row[:2] == (table, column))
            mutations = (
                ("type", f"TYPE text USING {column}::text"),
                ("precision", f"TYPE timestamptz(5) USING {column}::timestamptz(5)"),
                ("nullability", "DROP NOT NULL" if nullable == "NO" else "SET NOT NULL"),
                ("default", "SET DEFAULT clock_timestamp()"),
            )
            for fact_index, (fact, mutation) in enumerate(mutations):
                with self.subTest(identity=(table, column), fact=fact):
                    case_schema = f"{self.schema}_{identity_index}_{fact_index}"
                    self.connection.execute(f'CREATE SCHEMA "{case_schema}"')
                    self.connection.execute(f'SET search_path TO "{case_schema}"')
                    try:
                        postgres.install_postgres_schema(self.connection)
                        self.connection.execute(
                            f"ALTER TABLE {table} ALTER COLUMN {column} {mutation}"
                        )

                        with self.assertRaises(postgres.SchemaMigrationError) as raised:
                            postgres.verify_postgres_schema(self.connection)

                        self.assertEqual(
                            str(raised.exception),
                            "ingress evidence temporal schema is not current",
                        )
                        self.assertIsNone(raised.exception.__context__)
                        self.assertIsNone(raised.exception.__cause__)
                    finally:
                        self.connection.execute(f'SET search_path TO "{self.schema}"')
                        self.connection.execute(f'DROP SCHEMA "{case_schema}" CASCADE')

    def test_stores_admit_all_supplied_timestamps_before_connection_access(self) -> None:
        connection = _NoAccessConnection()
        resource_store = IngressResourceStore(connection)
        secret_store = GeneratedIngressSecretReferenceStore(connection)
        resource = self._resource_value(created_at="not-a-timestamp")

        with self.assertRaisesRegex(ValueError, "canonical UTC"):
            resource_store.record_cloudflare(resource)
        with self.assertRaisesRegex(ValueError, "canonical UTC"):
            resource_store.record_cloudflare(
                replace(resource, created_at=_SECONDS, observed_at="not-a-timestamp")
            )
        with self.assertRaisesRegex(ValueError, "canonical UTC"):
            resource_store.record_cloudflare(
                replace(
                    resource,
                    created_at=_SECONDS,
                    observed_at=_MICROS,
                    status=OwnedIngressResourceStatus.REMOVED,
                    removed_at="not-a-timestamp",
                    removed_by_run_id="run-removed",
                )
            )
        with self.assertRaisesRegex(ValueError, "canonical UTC"):
            resource_store.mark_removed(
                "workspace-a",
                "ingress-a",
                removed_at="not-a-timestamp",
                removed_by_run_id="run-a",
            )
        with self.assertRaisesRegex(ValueError, "canonical UTC"):
            secret_store.record(self._secret_value(recorded_at="not-a-timestamp"))
        self.assertEqual(connection.accesses, 0)

    def test_store_reads_decode_canonical_utc_and_secret_order_is_chronological(self) -> None:
        postgres.install_postgres_schema(self.connection)
        self._seed_workspace()
        self.connection.execute("SET TIME ZONE 'America/Los_Angeles'")
        resource_store = IngressResourceStore(self.connection)
        secret_store = GeneratedIngressSecretReferenceStore(self.connection)
        resource_store.record_cloudflare(self._resource_value())
        secret_store.record(self._secret_value(recorded_at=_SECONDS, event="event-a"))
        secret_store.record(self._secret_value(recorded_at=_LATER, event="event-b"))

        resource = resource_store.get_cloudflare("workspace-a", "ingress-a")
        evidence = secret_store.list_for_workspace("workspace-a")

        self.assertEqual(resource.created_at, _SECONDS)
        self.assertEqual(resource.observed_at, _MICROS)
        self.assertIsNone(resource.removed_at)
        self.assertEqual([item.source_event_id for item in evidence], ["event-b", "event-a"])
        self.assertEqual([item.recorded_at for item in evidence], [_LATER, _SECONDS])

    def _install_v7_baseline(self) -> None:
        registry = postgres.POSTGRES_SCHEMA_MIGRATIONS
        self.connection.execute(postgres.POSTGRES_SCHEMA)
        for migration in registry.migrations[1:7]:
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
        for migration in registry.migrations[:7]:
            self.connection.execute(
                """
                INSERT INTO cpk_schema_migrations (version, name, checksum_sha256)
                VALUES (%s, %s, %s)
                """,
                (migration.version, migration.name, migration.checksum_sha256),
            )
        self.connection.execute(schema_module._GRAPH_LINEAGE_CONSTRAINTS)

    def _seed_workspace(self) -> None:
        self.connection.execute(
            "INSERT INTO cpk_workspaces (workspace_id, name, lifecycle) "
            "VALUES ('workspace-a', 'Workspace A', 'created')"
        )

    def _insert_resource(
        self,
        *,
        index: int,
        status: str,
        created_at: str,
        observed_at: str,
        removed_at: str | None,
        removed_by_run_id: str | None,
    ) -> None:
        self.connection.execute(
            """
            INSERT INTO cpk_cloudflare_ingress_resources (
              workspace_id, runtime_id, ingress_id, epoch, status, authority_ref,
              provider_kind, tunnel_name, tunnel_id, dns_record_id, hostname,
              zone_id, lifecycle, created_at, observed_at, source_run_id,
              source_activity_id, source_event_id, removed_at, removed_by_run_id
            ) VALUES (
              'workspace-a', 'runtime-a', %s, 1, %s, 'public', 'cloudflare',
              %s, %s, %s, %s, 'zone-a', 'ephemeral', %s, %s,
              %s, %s, %s, %s, %s
            )
            """,
            (
                f"ingress-{index:02d}",
                status,
                f"tunnel-name-{index}",
                f"tunnel-id-{index}",
                f"dns-id-{index}",
                f"ingress-{index}.example.test",
                created_at,
                observed_at,
                f"run-{index}",
                f"activity-{index}",
                f"event-{index}",
                removed_at,
                removed_by_run_id,
            ),
        )

    def _insert_secret(self, *, index: int, recorded_at: str) -> None:
        self.connection.execute(
            """
            INSERT INTO cpk_generated_ingress_secret_references (
              workspace_id, purpose, secret_ref, recorded_at, source_run_id,
              source_activity_id, source_event_id, metadata
            ) VALUES (
              'workspace-a', 'cloudflared-tunnel-token', %s, %s, %s, %s, %s,
              jsonb_build_object(
                'provider_registration_id', 'provider-a',
                'reference_registration_id', 'reference-a',
                'custody_id', 'custody-a',
                'provider_version_id', 'version-a',
                'provider_version_number', 1
              )
            )
            """,
            (
                f"secret://generated/ingress/token-{index}",
                recorded_at,
                f"run-secret-{index}",
                f"activity-secret-{index}",
                f"event-secret-{index}",
            ),
        )

    def _resource_value(self, **changes: object) -> CloudflareOwnedIngressResource:
        values: dict[str, object] = {
            "workspace_id": "workspace-a",
            "runtime_id": "runtime-a",
            "ingress_id": "ingress-a",
            "authority_ref": IngressAuthorityReference("public"),
            "provider_kind": IngressAuthorityProviderKind.CLOUDFLARE,
            "tunnel_name": "tunnel-name-a",
            "tunnel_id": "tunnel-id-a",
            "dns_record_id": "dns-id-a",
            "hostname": "ingress-a.example.test",
            "zone_id": "zone-a",
            "lifecycle": PublicIngressLifecycle.EPHEMERAL,
            "created_at": _SECONDS,
            "observed_at": _MICROS,
            "source_run_id": "run-a",
            "source_activity_id": "activity-a",
            "source_event_id": "event-a",
        }
        values.update(changes)
        return CloudflareOwnedIngressResource(**values)

    def _secret_value(
        self,
        *,
        recorded_at: str,
        event: str = "event-a",
    ) -> GeneratedIngressSecretReference:
        return GeneratedIngressSecretReference(
            workspace_id="workspace-a",
            purpose=GeneratedSecretPurpose.CLOUDFLARED_TUNNEL_TOKEN,
            secret_ref=SecretReference(f"secret://generated/ingress/{event}"),
            provider_registration_id="provider-a",
            reference_registration_id=f"reference-{event}",
            custody_id=f"custody-{event}",
            provider_version_id=f"version-{event}",
            provider_version_number=1,
            recorded_at=recorded_at,
            source_run_id=f"run-{event}",
            source_activity_id=f"activity-{event}",
            source_event_id=event,
        )

    def _ledger(self) -> list[tuple[int, str]]:
        return self.connection.execute(
            "SELECT version, name FROM cpk_schema_migrations ORDER BY version"
        ).fetchall()

    def _temporal_contract(self) -> tuple[tuple[object, ...], ...]:
        return tuple(
            self.connection.execute(
                """
                SELECT table_name, column_name, data_type, datetime_precision,
                       is_nullable, column_default IS NULL
                FROM information_schema.columns
                WHERE table_schema = current_schema()
                  AND (table_name, column_name) IN (
                    ('cpk_cloudflare_ingress_resources', 'created_at'),
                    ('cpk_cloudflare_ingress_resources', 'observed_at'),
                    ('cpk_cloudflare_ingress_resources', 'removed_at'),
                    ('cpk_generated_ingress_secret_references', 'recorded_at')
                  )
                ORDER BY table_name, column_name
                """
            ).fetchall()
        )

    def _raw_times(self) -> tuple[tuple[object, ...], tuple[object, ...]]:
        resource = self.connection.execute(
            "SELECT created_at, observed_at, removed_at "
            "FROM cpk_cloudflare_ingress_resources"
        ).fetchone()
        secret = self.connection.execute(
            "SELECT recorded_at FROM cpk_generated_ingress_secret_references"
        ).fetchone()
        return resource, secret

    def _retained_non_temporal(self) -> tuple[tuple[object, ...], tuple[object, ...]]:
        resources = tuple(
            self.connection.execute(
                """
                SELECT workspace_id, runtime_id, ingress_id, epoch, status,
                       authority_ref, provider_kind, tunnel_name, tunnel_id,
                       dns_record_id, hostname, zone_id, lifecycle, source_run_id,
                       source_activity_id, source_event_id, removed_by_run_id, metadata
                FROM cpk_cloudflare_ingress_resources
                ORDER BY ingress_id
                """
            ).fetchall()
        )
        secrets = tuple(
            self.connection.execute(
                """
                SELECT workspace_id, purpose, secret_ref, source_run_id,
                       source_activity_id, source_event_id, metadata
                FROM cpk_generated_ingress_secret_references
                ORDER BY source_event_id
                """
            ).fetchall()
        )
        return resources, secrets

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
            SELECT indexname, (quote_ident(schemaname) || '.' || quote_ident(indexname))::regclass::oid,
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


if __name__ == "__main__":
    unittest.main()
