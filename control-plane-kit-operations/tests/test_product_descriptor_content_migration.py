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
from control_plane_kit_operations.postgres import product_descriptor_backfill
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
        self.admin.execute(
            f'DROP SCHEMA IF EXISTS "{self.schema}_other" CASCADE'
        )
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
            self.assertEqual(
                self._history(connection)[-1],
                (11, "gateway-probe-access-path"),
            )
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
            ("registration-semantic-bound", lambda connection: connection.execute(
                "UPDATE cpk_registered_products SET registration_id = %s",
                ("x" * 513,),
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

    def test_backfill_query_bounds_every_value_before_driver_fetch(self) -> None:
        connection = self._connection()
        try:
            self._prepare_v9(connection)
            self._insert_product(
                connection,
                registration_id="rprod-query-law",
                document=self._document(1),
            )
            recording = _RecordingConnection(connection)

            product_descriptor_backfill.backfill_product_descriptor_content_v1(
                recording
            )

            normalized = " ".join(recording.backfill_query.lower().split())
            for expression in (
                "octet_length(registration_id)",
                "octet_length(descriptor_sha256)",
                "octet_length(descriptor_document::text)",
                "octet_length(product_reference::text)",
                "octet_length(descriptor_content)",
                "where registration_id > %s",
                "order by registration_id",
                "limit %s for update",
            ):
                with self.subTest(expression=expression):
                    self.assertIn(expression, normalized)
            self.assertNotIn("collate", normalized)
            self.assertEqual(
                recording.backfill_parameters,
                (
                    2_048,
                    2_048,
                    64,
                    64,
                    524_288,
                    524_288,
                    524_288,
                    524_288,
                    262_144,
                    262_144,
                    "",
                    64,
                ),
            )
            connection.execute("SET enable_seqscan TO off")
            plan = connection.execute(
                "EXPLAIN (FORMAT JSON) " + recording.backfill_query,
                recording.backfill_parameters,
            ).fetchone()[0][0]["Plan"]
            self.assertIn(
                "cpk_registered_products_pkey",
                self._plan_index_names(plan),
            )
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
            "ALTER TABLE cpk_registered_products DROP CONSTRAINT "
            "cpk_registered_products_content_digest_check; "
            "ALTER TABLE cpk_registered_products ADD CONSTRAINT "
            "cpk_registered_products_content_digest_check "
            "UNIQUE (descriptor_content)",
            "ALTER TABLE cpk_registered_products DROP CONSTRAINT "
            "cpk_registered_products_content_digest_check; "
            "ALTER TABLE cpk_registered_products ADD CONSTRAINT "
            "cpk_registered_products_content_digest_check "
            "CHECK (descriptor_sha256 = encode(sha256("
            "convert_to(descriptor_sha256, 'UTF8')), 'hex'))",
            "ALTER TABLE cpk_registered_products DROP CONSTRAINT "
            "cpk_registered_products_content_digest_check; "
            "ALTER TABLE cpk_registered_products ADD CONSTRAINT "
            "cpk_registered_products_content_digest_check "
            "CHECK (descriptor_sha256 = encode(sha256("
            "convert_to(descriptor_content, 'LATIN1')), 'hex'))",
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

    def test_final_verifier_ignores_constraints_outside_the_target_relation(self) -> None:
        connection = self._connection()
        try:
            postgres.install_postgres_schema(connection)
            connection.execute(
                "ALTER TABLE cpk_workspaces ADD CONSTRAINT "
                "cpk_registered_products_content_digest_check "
                "CHECK (workspace_id = workspace_id)"
            )
            connection.execute(
                "ALTER TABLE cpk_registered_products ADD CONSTRAINT "
                "cpk_registered_products_content_digest_check_shadow "
                "CHECK (descriptor_sha256 = descriptor_sha256)"
            )
            other_schema = f"{self.schema}_other"
            self.admin.execute(f'CREATE SCHEMA "{other_schema}"')
            self.admin.execute(
                f'CREATE TABLE "{other_schema}".unrelated_product_contract '
                "(descriptor_sha256 text, descriptor_content text)"
            )
            self.admin.execute(
                f'ALTER TABLE "{other_schema}".unrelated_product_contract '
                "ADD CONSTRAINT cpk_registered_products_content_digest_check "
                "CHECK (descriptor_sha256 = descriptor_sha256)"
            )

            postgres.verify_postgres_schema(connection)

            connection.execute(
                "ALTER TABLE cpk_registered_products DROP CONSTRAINT "
                "cpk_registered_products_content_digest_check"
            )
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
                reader = self._connection()
                writer_started = threading.Event()
                writer_finished = threading.Event()
                failures: list[BaseException] = []
                thread = None
                try:
                    postgres.install_postgres_schema(caller)
                    reader.execute("SET lock_timeout TO '250ms'")
                    with self.assertRaises(psycopg.errors.LockNotAvailable):
                        reader.execute("SELECT count(*) FROM cpk_registered_products")
                    other_schema = f"{self.schema}_other"
                    self.admin.execute(f'CREATE SCHEMA "{other_schema}"')
                    self.admin.execute(
                        f'CREATE TABLE "{other_schema}".cpk_registered_products '
                        "(registration_id text PRIMARY KEY)"
                    )
                    self.assertEqual(
                        self.admin.execute(
                            f'SELECT count(*) FROM "{other_schema}".'
                            "cpk_registered_products"
                        ).fetchone()[0],
                        0,
                    )
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
                    reader.close()
                    self.assertFalse(thread is not None and thread.is_alive())

                observer = self._connection()
                try:
                    expected_history = (
                        (
                            *_V9_HISTORY,
                            (10, "product-descriptor-content"),
                            (11, "gateway-probe-access-path"),
                        )
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

    def test_writer_cannot_enter_behind_a_paused_keyset_cursor(self) -> None:
        setup = self._connection()
        try:
            self._prepare_v9(setup)
            for index in range(65):
                self._insert_product(
                    setup,
                    registration_id=f"rprod-{index:03d}",
                    document=self._document(index),
                )
        finally:
            setup.close()

        migration_connection = self._connection(
            application_name="cpk-v10-paused-migration"
        )
        writer = self._connection(application_name="cpk-v10-behind-cursor-writer")
        paused = _PausingBackfillConnection(migration_connection)
        migration_finished = threading.Event()
        writer_finished = threading.Event()
        failures: list[BaseException] = []
        migration_thread = None
        writer_thread = None
        try:
            def migrate() -> None:
                try:
                    postgres.install_postgres_schema(paused)
                except BaseException as error:
                    failures.append(error)
                finally:
                    migration_finished.set()

            migration_thread = threading.Thread(target=migrate)
            migration_thread.start()
            self.assertTrue(paused.first_batch.wait(timeout=10))

            document = self._document(99)

            def write_behind_cursor() -> None:
                try:
                    self._insert_product(
                        writer,
                        registration_id="rprod-000a",
                        document=document,
                        descriptor_content=document.content.decode("utf-8"),
                    )
                except BaseException as error:
                    failures.append(error)
                finally:
                    writer_finished.set()

            writer_thread = threading.Thread(target=write_behind_cursor)
            writer_thread.start()
            self._wait_for_table_lock("cpk-v10-behind-cursor-writer")
            self.assertFalse(writer_finished.is_set())
            self.assertFalse(migration_finished.is_set())

            paused.release.set()
            self.assertTrue(migration_finished.wait(timeout=15))
            self.assertTrue(writer_finished.wait(timeout=15))
            migration_thread.join(timeout=1)
            writer_thread.join(timeout=1)
            self.assertFalse(migration_thread.is_alive())
            self.assertFalse(writer_thread.is_alive())
            self.assertEqual(failures, [])
        finally:
            paused.release.set()
            if migration_thread is not None and migration_thread.is_alive():
                migration_connection.cancel()
                migration_thread.join(timeout=15)
            if writer_thread is not None and writer_thread.is_alive():
                writer.cancel()
                writer_thread.join(timeout=15)
            migration_alive = (
                migration_thread is not None and migration_thread.is_alive()
            )
            writer_alive = writer_thread is not None and writer_thread.is_alive()
            if not migration_alive:
                migration_connection.close()
            if not writer_alive:
                writer.close()
            self.assertFalse(migration_alive)
            self.assertFalse(writer_alive)

        observer = self._connection()
        try:
            self.assertEqual(
                self._history(observer)[-1],
                (11, "gateway-probe-access-path"),
            )
            self.assertEqual(
                observer.execute(
                    "SELECT count(*) FROM cpk_registered_products "
                    "WHERE descriptor_content IS NULL"
                ).fetchone()[0],
                0,
            )
            self.assertEqual(
                observer.execute(
                    "SELECT count(*) FROM cpk_registered_products"
                ).fetchone()[0],
                66,
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

    def _plan_index_names(self, plan: dict[str, object]) -> set[str]:
        names = set()
        index_name = plan.get("Index Name")
        if isinstance(index_name, str):
            names.add(index_name)
        children = plan.get("Plans", [])
        if isinstance(children, list):
            for child in children:
                if isinstance(child, dict):
                    names.update(self._plan_index_names(child))
        return names

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
        self.admin.execute(
            f'DROP SCHEMA IF EXISTS "{self.schema}_other" CASCADE'
        )
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
        if params:
            return self._connection.execute(query, params)
        return self._connection.execute(query)


class _RecordingConnection:
    def __init__(self, connection) -> None:
        self._connection = connection
        self.backfill_query = ""
        self.backfill_parameters = ()

    @property
    def autocommit(self):
        return self._connection.autocommit

    def transaction(self):
        return self._connection.transaction()

    def execute(self, query, params=()):
        if (
            not self.backfill_query
            and "FOR UPDATE" in query
            and "FROM cpk_registered_products" in query
        ):
            self.backfill_query = query
            self.backfill_parameters = params
        if params:
            return self._connection.execute(query, params)
        return self._connection.execute(query)


class _PausingBackfillConnection:
    def __init__(self, connection) -> None:
        self._connection = connection
        self.first_batch = threading.Event()
        self.release = threading.Event()
        self._paused = False

    @property
    def autocommit(self):
        return self._connection.autocommit

    def transaction(self):
        return self._connection.transaction()

    def execute(self, query, params=()):
        cursor = (
            self._connection.execute(query, params)
            if params
            else self._connection.execute(query)
        )
        if (
            not self._paused
            and "FOR UPDATE" in query
            and "FROM cpk_registered_products" in query
        ):
            self._paused = True
            return _PausingCursor(cursor, self.first_batch, self.release)
        return cursor


class _PausingCursor:
    def __init__(self, cursor, first_batch, release) -> None:
        self._cursor = cursor
        self._first_batch = first_batch
        self._release = release

    def fetchall(self):
        rows = self._cursor.fetchall()
        if len(rows) == 64:
            self._first_batch.set()
            if not self._release.wait(timeout=15):
                raise RuntimeError("paused backfill was not released")
        return rows

    def __getattr__(self, name):
        return getattr(self._cursor, name)


if __name__ == "__main__":
    unittest.main()
