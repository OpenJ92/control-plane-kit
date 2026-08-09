from __future__ import annotations

import json
import os
import threading
import time
import unittest
import uuid

import psycopg
from psycopg.types.json import Jsonb

from control_plane_kit_core.algebra import BlockSockets, ProviderSocket
from control_plane_kit_core.products import (
    ContainerServerProduct,
    OciImageReference,
    ProductDescriptorCodec,
    ProductIdentity,
    ProductReference,
    ProductReferenceCodec,
    ProductRuntimeContract,
)
from control_plane_kit_core.types import Protocol
import control_plane_kit_operations.postgres as postgres
from control_plane_kit_operations.postgres import migration_inspection
from control_plane_kit_operations.postgres import migration_runner
from control_plane_kit_operations.postgres import schema as schema_module


_V9_HISTORY = (
    (1, "operations-baseline"),
    (2, "coordination-timestamps"),
    (3, "graph-product-authority-timestamps"),
    (4, "secret-registration-timestamps"),
    (5, "delegation-signing-key-timestamps"),
    (6, "gateway-probe-timestamps"),
    (7, "gateway-key-rotation-timestamps"),
    (8, "ingress-evidence-timestamps"),
    (9, "secret-use-authorization-timestamps"),
)


class ProductDescriptorContentMigrationTests(unittest.TestCase):
    def setUp(self) -> None:
        database_url = os.environ.get("CPK_OPERATIONS_TEST_DATABASE_URL")
        if not database_url:
            raise RuntimeError(
                "CPK_OPERATIONS_TEST_DATABASE_URL is required. Run the "
                "Docker-first operations test harness."
            )
        self.database_url = database_url
        self.schema = f"product_content_{uuid.uuid4().hex}"
        self.admin = psycopg.connect(database_url, autocommit=True)
        self.admin.execute(f'CREATE SCHEMA "{self.schema}"')

    def tearDown(self) -> None:
        self.admin.execute(f'DROP SCHEMA IF EXISTS "{self.schema}" CASCADE')
        self.admin.close()

    def test_v10_backfills_historical_rows_in_batches_and_is_idempotent(self) -> None:
        connection = self._connection()
        try:
            self._prepare_v9(connection)
            documents = [self._document(index) for index in range(66)]
            for index, document in enumerate(documents):
                self._insert_product(
                    connection,
                    registration_id=f"rprod-{index:03d}",
                    document=document,
                    descriptor_content=(
                        document.content.decode("utf-8") if index == 0 else None
                    ),
                )
            existing_ctid = connection.execute(
                "SELECT ctid::text FROM cpk_registered_products "
                "WHERE registration_id = 'rprod-000'"
            ).fetchone()[0]
            table_oid = connection.execute(
                "SELECT 'cpk_registered_products'::regclass::oid"
            ).fetchone()[0]

            postgres.install_postgres_schema(connection)

            rows = connection.execute(
                "SELECT registration_id, descriptor_content "
                "FROM cpk_registered_products ORDER BY registration_id"
            ).fetchall()
            self.assertEqual(len(rows), 66)
            self.assertEqual(
                rows,
                [
                    (f"rprod-{index:03d}", document.content.decode("utf-8"))
                    for index, document in enumerate(documents)
                ],
            )
            self.assertEqual(
                connection.execute(
                    "SELECT ctid::text FROM cpk_registered_products "
                    "WHERE registration_id = 'rprod-000'"
                ).fetchone()[0],
                existing_ctid,
            )
            self.assertEqual(
                connection.execute(
                    "SELECT 'cpk_registered_products'::regclass::oid"
                ).fetchone()[0],
                table_oid,
            )
            self.assertEqual(self._history(connection)[-1], (10, "product-descriptor-content"))
            self._assert_current_contract(connection)
            before = connection.execute(
                "SELECT * FROM cpk_registered_products ORDER BY registration_id"
            ).fetchall()

            postgres.install_postgres_schema(connection)

            self.assertEqual(
                connection.execute(
                    "SELECT * FROM cpk_registered_products ORDER BY registration_id"
                ).fetchall(),
                before,
            )
        finally:
            connection.close()

    def test_retained_semantic_or_transport_drift_rolls_back_exact_v9_truth(self) -> None:
        marker = "private-retained-product-material"
        cases = (
            ("digest", lambda connection: connection.execute(
                "UPDATE cpk_registered_products SET descriptor_sha256 = %s",
                ("b" * 64,),
            )),
            ("reference", lambda connection: connection.execute(
                "UPDATE cpk_registered_products SET product_reference = %s",
                (Jsonb(ProductReferenceCodec().encode(
                    ProductReference.from_document(self._document(99))
                )),),
            )),
            ("existing-content", lambda connection: connection.execute(
                "UPDATE cpk_registered_products SET descriptor_content = %s",
                (marker,),
            )),
            ("malformed-document", lambda connection: connection.execute(
                "UPDATE cpk_registered_products SET descriptor_document = %s",
                (Jsonb({"schema": "private.invalid"}),),
            )),
            ("registration-bound", lambda connection: connection.execute(
                "UPDATE cpk_registered_products SET registration_id = %s",
                ("x" * 2049,),
            )),
            ("document-transport-bound", lambda connection: connection.execute(
                "UPDATE cpk_registered_products SET descriptor_document = %s",
                (Jsonb({"padding": "x" * 524_289}),),
            )),
            ("reference-transport-bound", lambda connection: connection.execute(
                "UPDATE cpk_registered_products SET product_reference = %s",
                (Jsonb({"padding": "x" * 524_289}),),
            )),
            ("content-bound", lambda connection: connection.execute(
                "UPDATE cpk_registered_products SET descriptor_content = %s",
                ("x" * 262_145,),
            )),
        )
        for name, mutate in cases:
            with self.subTest(name=name):
                self._reset_schema()
                connection = self._connection()
                try:
                    self._prepare_v9(connection)
                    self._insert_product(
                        connection,
                        registration_id="rprod-valid",
                        document=self._document(1),
                    )
                    mutate(connection)
                    before = connection.execute(
                        "SELECT * FROM cpk_registered_products"
                    ).fetchall()

                    with self.assertRaises(postgres.SchemaMigrationError) as raised:
                        postgres.install_postgres_schema(connection)

                    self.assertEqual(
                        str(raised.exception),
                        "product descriptor content backfill failed",
                    )
                    self.assertLessEqual(len(str(raised.exception)), 128)
                    self.assertNotIn(marker, str(raised.exception))
                    self.assertIsNone(raised.exception.__context__)
                    self.assertIsNone(raised.exception.__cause__)
                    self.assertEqual(self._history(connection), _V9_HISTORY)
                    self.assertEqual(
                        connection.execute(
                            "SELECT * FROM cpk_registered_products"
                        ).fetchall(),
                        before,
                    )
                    self._assert_v9_nullable_without_content_constraint(connection)
                finally:
                    connection.close()

    def test_sql_failures_roll_back_first_middle_and_final_program_phases(self) -> None:
        marker = "private-provider-address-and-credential-material"
        for phase in ("first", "final"):
            with self.subTest(phase=phase):
                self._reset_schema()
                connection = self._connection()
                try:
                    self._prepare_v9(connection)
                    self._insert_product(
                        connection,
                        registration_id="rprod-rollback",
                        document=self._document(1),
                    )
                    failing = _FailingPhaseConnection(connection, phase, marker)

                    with self.assertRaises(postgres.SchemaMigrationError) as raised:
                        postgres.install_postgres_schema(failing)

                    self.assertEqual(
                        str(raised.exception),
                        "schema migration application failed",
                    )
                    self.assertNotIn(marker, str(raised.exception))
                    self.assertIsNone(raised.exception.__context__)
                    self.assertIsNone(raised.exception.__cause__)
                    self.assertEqual(self._history(connection), _V9_HISTORY)
                    self.assertIsNone(
                        connection.execute(
                            "SELECT descriptor_content FROM cpk_registered_products"
                        ).fetchone()[0]
                    )
                    self._assert_v9_nullable_without_content_constraint(connection)
                finally:
                    connection.close()

    def test_final_verifier_rejects_column_and_digest_constraint_drift(self) -> None:
        mutations = (
            "ALTER TABLE cpk_registered_products "
            "ALTER COLUMN descriptor_content DROP NOT NULL",
            "ALTER TABLE cpk_registered_products DROP CONSTRAINT "
            "cpk_registered_products_content_digest_check",
            "ALTER TABLE cpk_registered_products DROP CONSTRAINT "
            "cpk_registered_products_content_digest_check; "
            "ALTER TABLE cpk_registered_products ADD CONSTRAINT "
            "cpk_registered_products_content_digest_check "
            "CHECK (descriptor_sha256 = descriptor_sha256) NOT VALID",
        )
        for mutation in mutations:
            with self.subTest(mutation=mutation[:48]):
                self._reset_schema()
                connection = self._connection()
                try:
                    postgres.install_postgres_schema(connection)
                    self._assert_current_contract(connection)
                    connection.execute(mutation)

                    with self.assertRaises(postgres.SchemaMigrationError):
                        postgres.verify_postgres_schema(connection)
                finally:
                    connection.close()

    def test_access_exclusive_lock_lives_until_caller_transaction_end(self) -> None:
        for outcome in ("commit", "rollback"):
            with self.subTest(outcome=outcome):
                self._reset_schema()
                setup = self._connection()
                try:
                    self._prepare_v9(setup)
                    self._insert_product(
                        setup,
                        registration_id="rprod-before",
                        document=self._document(1),
                    )
                finally:
                    setup.close()

                caller = self._connection(autocommit=False)
                writer = self._connection(application_name=f"cpk-v10-writer-{outcome}")
                writer_started = threading.Event()
                writer_finished = threading.Event()
                failures: list[BaseException] = []
                thread = None
                try:
                    postgres.install_postgres_schema(caller)
                    document = self._document(2)

                    def write_after_migration() -> None:
                        writer_started.set()
                        try:
                            self._insert_product(
                                writer,
                                registration_id="rprod-after",
                                document=document,
                                descriptor_content=document.content.decode("utf-8"),
                            )
                        except BaseException as error:
                            failures.append(error)
                        finally:
                            writer_finished.set()

                    thread = threading.Thread(target=write_after_migration)
                    thread.start()
                    self.assertTrue(writer_started.wait(timeout=2))
                    self._wait_for_table_lock(f"cpk-v10-writer-{outcome}")
                    self.assertFalse(writer_finished.is_set())

                    if outcome == "commit":
                        caller.commit()
                    else:
                        caller.rollback()
                    self.assertTrue(writer_finished.wait(timeout=10))
                    thread.join(timeout=1)
                    self.assertFalse(thread.is_alive())
                    self.assertEqual(failures, [])
                finally:
                    if thread is not None and thread.is_alive():
                        caller.rollback()
                        writer.cancel()
                        thread.join(timeout=15)
                    if not caller.closed:
                        caller.rollback()
                        caller.close()
                    if thread is None or not thread.is_alive():
                        writer.close()
                    self.assertFalse(thread is not None and thread.is_alive())

                observer = self._connection()
                try:
                    expected_history = (
                        (*_V9_HISTORY, (10, "product-descriptor-content"))
                        if outcome == "commit"
                        else _V9_HISTORY
                    )
                    self.assertEqual(self._history(observer), expected_history)
                    self.assertEqual(
                        observer.execute(
                            "SELECT count(*) FROM cpk_registered_products"
                        ).fetchone()[0],
                        2,
                    )
                finally:
                    observer.close()

    def _prepare_v9(self, connection) -> None:
        production = schema_module.POSTGRES_SCHEMA_MIGRATIONS
        v9 = postgres.SchemaMigrationRegistry(production.migrations[:9])
        previous = (
            schema_module.POSTGRES_SCHEMA_MIGRATIONS,
            migration_runner.POSTGRES_SCHEMA_MIGRATIONS,
            migration_inspection.POSTGRES_SCHEMA_MIGRATIONS,
        )
        schema_module.POSTGRES_SCHEMA_MIGRATIONS = v9
        migration_runner.POSTGRES_SCHEMA_MIGRATIONS = v9
        migration_inspection.POSTGRES_SCHEMA_MIGRATIONS = v9
        try:
            postgres.install_postgres_schema(connection)
        finally:
            (
                schema_module.POSTGRES_SCHEMA_MIGRATIONS,
                migration_runner.POSTGRES_SCHEMA_MIGRATIONS,
                migration_inspection.POSTGRES_SCHEMA_MIGRATIONS,
            ) = previous
        connection.execute(
            "ALTER TABLE cpk_registered_products "
            "ALTER COLUMN descriptor_content DROP NOT NULL"
        )
        connection.execute(
            "INSERT INTO cpk_workspaces (workspace_id, name, lifecycle) "
            "VALUES ('workspace-a', 'Workspace A', 'created')"
        )

    def _insert_product(
        self,
        connection,
        *,
        registration_id: str,
        document,
        descriptor_content: str | None = None,
    ) -> None:
        reference = ProductReference.from_document(document)
        connection.execute(
            """
            INSERT INTO cpk_registered_products (
              registration_id,
              workspace_id,
              product_reference,
              descriptor_sha256,
              descriptor_document,
              descriptor_content,
              source,
              imported_by,
              imported_at,
              status,
              metadata
            ) VALUES (%s, 'workspace-a', %s, %s, %s, %s, %s,
                      'operator-a', '2026-08-08T12:00:00Z', 'active', '{}'::jsonb)
            """,
            (
                registration_id,
                Jsonb(ProductReferenceCodec().encode(reference)),
                document.content_digest,
                Jsonb(json.loads(document.content.decode("utf-8"))),
                descriptor_content,
                Jsonb({"kind": "inline"}),
            ),
        )

    def _document(self, index: int):
        name = f"server-{index:03d}"
        return ProductDescriptorCodec().encode_document(
            ContainerServerProduct(
                identity=ProductIdentity("cpk-servers", name, 1),
                image=OciImageReference(
                    "ghcr.io",
                    f"openj92/control-plane-kit-servers/{name}",
                    "sha256:" + f"{index % 16:x}" * 64,
                    tag="v1",
                ),
                runtime_contract=ProductRuntimeContract(
                    sockets=BlockSockets(
                        providers=(ProviderSocket("http", Protocol.HTTP),)
                    )
                ),
                display_name=f"Server {index}",
                description="Backfill fixture with Unicode pottery: \u00e9 \U0001f3fa",
            )
        )

    def _assert_current_contract(self, connection) -> None:
        column = connection.execute(
            """
            SELECT data_type, is_nullable, column_default
            FROM information_schema.columns
            WHERE table_schema = current_schema()
              AND table_name = 'cpk_registered_products'
              AND column_name = 'descriptor_content'
            """
        ).fetchone()
        self.assertEqual(column, ("text", "NO", None))
        constraints = connection.execute(
            """
            SELECT constraint_type, is_deferrable, initially_deferred
            FROM information_schema.table_constraints
            WHERE constraint_schema = current_schema()
              AND table_name = 'cpk_registered_products'
              AND constraint_name = 'cpk_registered_products_content_digest_check'
            """
        ).fetchall()
        self.assertEqual(constraints, [("CHECK", "NO", "NO")])
        postgres.verify_postgres_schema(connection)

    def _assert_v9_nullable_without_content_constraint(self, connection) -> None:
        self.assertEqual(
            connection.execute(
                """
                SELECT is_nullable
                FROM information_schema.columns
                WHERE table_schema = current_schema()
                  AND table_name = 'cpk_registered_products'
                  AND column_name = 'descriptor_content'
                """
            ).fetchone()[0],
            "YES",
        )
        self.assertEqual(
            connection.execute(
                """
                SELECT count(*)
                FROM pg_constraint
                WHERE connamespace = current_schema()::regnamespace
                  AND conname = 'cpk_registered_products_content_digest_check'
                """
            ).fetchone()[0],
            0,
        )

    def _wait_for_table_lock(self, application_name: str) -> None:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            row = self.admin.execute(
                """
                SELECT wait_event_type
                FROM pg_stat_activity
                WHERE application_name = %s
                """,
                (application_name,),
            ).fetchone()
            if row is not None and row[0] == "Lock":
                return
            time.sleep(0.01)
        self.fail("concurrent product writer did not wait on the V10 table lock")

    def _history(self, connection) -> tuple[tuple[int, str], ...]:
        return tuple(
            connection.execute(
                "SELECT version, name FROM cpk_schema_migrations ORDER BY version"
            ).fetchall()
        )

    def _connection(
        self,
        *,
        autocommit: bool = True,
        application_name: str | None = None,
    ):
        kwargs = {"autocommit": autocommit}
        if application_name is not None:
            kwargs["application_name"] = application_name
        connection = psycopg.connect(self.database_url, **kwargs)
        connection.execute(f'SET search_path TO "{self.schema}"')
        if not autocommit:
            connection.commit()
        return connection

    def _reset_schema(self) -> None:
        self.admin.execute(f'DROP SCHEMA IF EXISTS "{self.schema}" CASCADE')
        self.admin.execute(f'CREATE SCHEMA "{self.schema}"')


class _FailingPhaseConnection:
    def __init__(self, connection, phase: str, marker: str) -> None:
        self._connection = connection
        self._phase = phase
        self._marker = marker

    @property
    def autocommit(self):
        return self._connection.autocommit

    def transaction(self):
        return self._connection.transaction()

    def execute(self, query, params=()):
        is_first = "LOCK TABLE cpk_registered_products" in query
        is_final = "ALTER COLUMN descriptor_content SET NOT NULL" in query
        if (self._phase == "first" and is_first) or (
            self._phase == "final" and is_final
        ):
            raise RuntimeError(self._marker)
        return self._connection.execute(query, params)


if __name__ == "__main__":
    unittest.main()
