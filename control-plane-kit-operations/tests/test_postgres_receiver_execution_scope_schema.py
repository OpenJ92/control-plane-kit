"""#1902 exact prospective catalog and SQL-enforced scope representation."""

import unittest
from hashlib import sha256

import psycopg

from control_plane_kit_operations.postgres import install_schema
from control_plane_kit_operations.postgres.current_schema_contract import CURRENT_POSTGRES_SCHEMA_CONTRACT
from tests.receiver_execution_scope_fixture import ReceiverExecutionScopeFixture, SCOPES
from tests.test_current_schema_installation import _RecordingConnection


class PostgresReceiverExecutionScopeSchemaTests(ReceiverExecutionScopeFixture, unittest.TestCase):
    def test_exact_scope_relation_header_constraints_and_indexes(self):
        self.require_catalog()
        contract = CURRENT_POSTGRES_SCHEMA_CONTRACT
        columns = {column.name: column for column in contract.columns if column.relation == SCOPES}
        self.assertEqual(set(columns), {"request_id", "workspace_id", "scope_ordinal", "scope_kind", "runtime_id", "node_id"})
        for name, column in columns.items():
            self.assertEqual(column.formatted_type, "integer" if name == "scope_ordinal" else "text")
            self.assertEqual(column.not_null, name != "node_id")
            self.assertIsNone(column.default_expression)
        header = {column.name: column for column in contract.columns
                  if column.relation == "cpk_execution_requests" and column.name.startswith("receiver_scope_")}
        self.assertEqual(set(header), {"receiver_scope_count", "receiver_scope_digest"})
        for name, column in header.items():
            self.assertTrue(column.not_null)
            self.assertIsNone(column.default_expression)
            self.assertEqual(column.formatted_type, "integer" if name.endswith("count") else "text")
        self.assertEqual({item.name for item in contract.constraints
            if item.relation == "cpk_execution_requests" and item.name.startswith("cpk_execution_requests_receiver_scope_")},
            {"cpk_execution_requests_receiver_scope_count_check", "cpk_execution_requests_receiver_scope_digest_check"})
        constraints = {value.name: value for value in contract.constraints if value.relation == SCOPES}
        self.assertEqual(set(constraints), {SCOPES + suffix for suffix in (
            "_pkey", "_request_workspace_fk", "_position_check", "_kind_check",
            "_runtime_check", "_node_check", "_key_bytes_check")})
        fk = constraints[SCOPES + "_request_workspace_fk"]
        self.assertEqual((fk.local_columns, fk.referenced_relation, fk.referenced_columns),
                         (("request_id", "workspace_id"), "cpk_execution_requests", ("request_id", "workspace_id")))
        self.assertFalse(fk.deferrable)
        self.assertFalse(fk.deferred)
        self.assertEqual((fk.update_action, fk.delete_action), ("a", "a"))
        indexes = {value.name: value for value in contract.indexes if value.relation == SCOPES}
        expected = {
            "_pkey": (("request_id", "scope_ordinal"), True, None),
            "_runtime_lookup": (("workspace_id", "runtime_id", "request_id"), True, "(scope_kind = 'runtime'::text)"),
            "_node_lookup": (("workspace_id", "runtime_id", "node_id", "request_id"), True, "(scope_kind = 'node'::text)"),
            "_runtime_nodes_lookup": (("workspace_id", "runtime_id", "request_id", "node_id"), False, "(scope_kind = 'node'::text)"),
        }
        self.assertEqual(set(indexes), {SCOPES + suffix for suffix in expected})
        for suffix, (keys, unique, predicate) in expected.items():
            actual = indexes[SCOPES + suffix]
            self.assertEqual((actual.key_entries, actual.unique, actual.predicate), (keys, unique, predicate))
            self.assertEqual(actual.access_method, "btree")
            self.assertEqual(actual.include_entries, ())
        cancel = next(item for item in contract.indexes if item.name == "cpk_operation_actions_receiver_cancel")
        self.assertEqual(cancel.key_entries, ("session_id", "((payload ->> 'run_id'::text))", "action_id"))
        self.assertEqual(cancel.predicate, "(action_type = 'cancel-run'::text)")
        self.assertFalse(cancel.unique)
        self.assertEqual(self.connection.execute("SHOW block_size").fetchone()[0], "8192")
        actual_columns = self.connection.execute("SELECT attname FROM pg_attribute WHERE attrelid=%s::regclass AND attnum>0 AND NOT attisdropped", (SCOPES,)).fetchall()
        self.assertEqual({row[0] for row in actual_columns}, set(columns))
        actual_indexes = self.connection.execute("SELECT indexrelid::regclass::text FROM pg_index WHERE indrelid=%s::regclass", (SCOPES,)).fetchall()
        self.assertEqual({row[0] for row in actual_indexes}, set(indexes))

    def test_sql_scope_constraints_reject_bad_kind_shape_position_and_crossed_workspace(self):
        self.require_catalog()
        self.admit()
        original = self.scope_rows()
        cases = (
            ("scope_kind", "unknown", psycopg.errors.CheckViolation),
            ("scope_kind", "runtime", psycopg.errors.CheckViolation),
            ("node_id", None, psycopg.errors.CheckViolation),
            ("node_id", "", psycopg.errors.CheckViolation),
            ("runtime_id", " ", psycopg.errors.CheckViolation),
            ("scope_ordinal", 1024, psycopg.errors.CheckViolation),
            ("scope_ordinal", -1, psycopg.errors.CheckViolation),
            ("workspace_id", "foreign", psycopg.errors.ForeignKeyViolation),
        )
        for column, value, error in cases:
            with self.subTest(column=column, value=value), self.assertRaises(error):
                with self.connection.transaction():
                    self.connection.execute(psycopg.sql.SQL("UPDATE cpk_execution_receiver_scopes SET {}=%s WHERE request_id='execution-a'").format(psycopg.sql.Identifier(column)), (value,))
            self.assertEqual(self.scope_rows(), original)

    def test_sql_header_has_no_default_or_missing_empty_shortcut(self):
        self.require_catalog()
        self.admit()
        for column, value, error in (
            ("receiver_scope_count", None, psycopg.errors.NotNullViolation),
            ("receiver_scope_count", -1, psycopg.errors.CheckViolation),
            ("receiver_scope_count", 1025, psycopg.errors.CheckViolation),
            ("receiver_scope_digest", None, psycopg.errors.NotNullViolation),
            ("receiver_scope_digest", "A" * 64, psycopg.errors.CheckViolation),
            ("receiver_scope_digest", "a" * 63, psycopg.errors.CheckViolation),
        ):
            with self.subTest(column=column, value=value), self.assertRaises(error):
                with self.connection.transaction():
                    self.connection.execute(psycopg.sql.SQL("UPDATE cpk_execution_requests SET {}=%s WHERE request_id='execution-a'").format(psycopg.sql.Identifier(column)), (value,))

    def test_sql_combined_key_accepts_1024_bytes_and_refuses_1025_without_truncation(self):
        self.require_catalog()
        self.admit()
        fixed = len("execution-aworkspace-adocker".encode())
        size = 1024 - fixed
        noise = "".join(sha256(str(i).encode()).hexdigest() for i in range(32))[:size]
        multibyte = "é" * (size // 2) + "x" * (size % 2)
        for node in (noise, multibyte):
            with self.subTest(kind="multibyte" if node == multibyte else "incompressible"):
                self.assertEqual(len(node.encode()) + fixed, 1024)
                with self.connection.transaction():
                    self.connection.execute("UPDATE cpk_execution_receiver_scopes SET node_id=%s WHERE request_id='execution-a'", (node,))
                    actual = self.connection.execute("SELECT node_id FROM cpk_execution_receiver_scopes WHERE request_id='execution-a'").fetchone()[0]
                    self.assertEqual(actual, node)
                    # This is SQL representation evidence, not original-scope
                    # derivation. Restore original rows before current reentry.
                    raise psycopg.Rollback()
                with self.assertRaises(psycopg.errors.CheckViolation):
                    with self.connection.transaction():
                        self.connection.execute("UPDATE cpk_execution_receiver_scopes SET node_id=%s WHERE request_id='execution-a'", (node + "x",))

    def test_current_reentry_preserves_coverage_and_executes_only_queries(self):
        self.require_catalog()
        self.admit()
        before = self.scope_header(), self.scope_rows()
        recording = _RecordingConnection(self.connection)
        install_schema(recording)
        self.assertEqual((self.scope_header(), self.scope_rows()), before)
        self.assertTrue(recording.calls)
        for query in recording.calls:
            statements = tuple(part.strip().upper() for part in query.split(";") if part.strip())
            for statement in statements:
                query_only = statement.startswith(("SELECT", "WITH"))
                catalog_lock = statement.startswith("LOCK TABLE ONLY ") and statement.endswith(" IN SHARE MODE")
                self.assertTrue(query_only or catalog_lock, statement[:160])
