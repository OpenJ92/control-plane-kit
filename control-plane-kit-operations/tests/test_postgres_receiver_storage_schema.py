"""#1897 C1–C3/B1–B5 exact schema delta and actual PostgreSQL integrity."""

import unittest

import psycopg
from psycopg import sql

from control_plane_kit_operations.postgres import install_schema
from control_plane_kit_operations.postgres.schema import SchemaInstallationError
from control_plane_kit_operations.postgres.current_schema_contract import CURRENT_POSTGRES_SCHEMA_CONTRACT
from tests.receiver_storage_fixture import ReceiverStorageFixture
from tests.receiver_storage_schema_contract import BIND, CHECKS, COLUMNS, FOREIGN_KEYS, INTRO, KEYS, NULLABLE


class PostgresReceiverStorageSchemaTests(ReceiverStorageFixture, unittest.TestCase):
    def test_exact_receiver_catalog_has_two_tables_and_only_seven_constraint_indexes(self):
        contract = CURRENT_POSTGRES_SCHEMA_CONTRACT
        relations = {item.name: item for item in contract.relations}
        for relation in (INTRO, BIND):
            self.assertIn(relation, relations, "#1897 receiver relation is missing")
            self.assertEqual(relations[relation].non_internal_triggers, 0)
            columns = [item for item in contract.columns if item.relation == relation]
            self.assertEqual({item.name for item in columns}, set(COLUMNS[relation]))
            for column in columns:
                self.assertEqual(column.formatted_type, "text")
                self.assertEqual(column.not_null, column.name not in NULLABLE)
                self.assertIsNone(column.default_expression)
        expected_names = {row[1] for row in KEYS + FOREIGN_KEYS}
        expected_names |= {relation + "_" + suffix for relation, suffixes in CHECKS.items() for suffix in suffixes}
        extra_support = {KEYS[-1][1], KEYS[-2][1]}
        selected = [item for item in contract.constraints if item.relation in (INTRO, BIND)
                    or item.name in extra_support]
        self.assertEqual({item.name for item in selected}, expected_names)
        by_name = {item.name: item for item in selected}
        for relation, name, kind, local, target, remote, deferred in KEYS + FOREIGN_KEYS:
            actual = by_name[name]
            self.assertEqual((actual.relation, actual.kind, actual.local_columns,
                              actual.referenced_relation, actual.referenced_columns),
                             (relation, kind, local, target, remote))
            self.assertEqual((actual.deferrable, actual.deferred), (deferred, deferred))
            self.assertTrue(actual.validated)
            if kind == "f":
                self.assertEqual((actual.update_action, actual.delete_action, actual.match_type), ("a", "a", "s"))
        indexes = [item for item in contract.indexes if item.relation in (INTRO, BIND)
                   or item.owning_constraint in extra_support]
        self.assertEqual({item.owning_constraint for item in indexes}, {row[1] for row in KEYS})
        self.assertEqual(len(indexes), 7)
        for item in indexes:
            self.assertTrue(item.unique)
            self.assertEqual(item.access_method, "btree")
            self.assertIsNone(item.predicate)
            self.assertEqual(item.include_entries, ())
        # The live installer must produce the same exact catalog, not just values.
        actual_names = self.connection.execute(
            "SELECT conname FROM pg_constraint WHERE connamespace=current_schema()::regnamespace "
            "AND (conrelid IN (%s::regclass,%s::regclass) OR conname=ANY(%s))",
            (INTRO, BIND, list(extra_support)),
        ).fetchall()
        self.assertEqual({row[0] for row in actual_names}, expected_names)

    def test_crossed_graph_projection_action_session_draft_and_scope_are_rejected(self):
        graph, projection, action, draft_id = self.seed(with_draft=True)
        with self.unit_of_work() as uow:
            uow.stores.graphs.lock_receiver_lifecycle("workspace-b")
            foreign_graph, foreign_projection = self.material(uow, workspace="workspace-b")
            foreign_action = self.action(uow, "workspace-b")
            uow.commit()
        before = self.truth()
        cases = (
            (INTRO, {"introducing_graph_id": foreign_graph.graph_id}),
            (INTRO, {"introducing_realized_projection_id": foreign_projection.projection_id}),
            (INTRO, {"introducing_action_id": foreign_action.action_id}),
            (INTRO, {"introducing_action_id": foreign_action.action_id,
                     "introducing_session_id": foreign_action.session_id}),
            (INTRO, {"introducing_draft_id": "missing-draft"}),
            (INTRO, {"first_accepted_action_id": foreign_action.action_id,
                     "first_accepted_session_id": foreign_action.session_id}),
            (INTRO, {"first_accepted_action_id": action.action_id,
                     "first_accepted_session_id": action.session_id,
                     "retired_action_id": foreign_action.action_id,
                     "retired_session_id": foreign_action.session_id}),
            (BIND, {"graph_id": foreign_graph.graph_id}),
            (BIND, {"realized_projection_id": foreign_projection.projection_id}),
            (BIND, {"runtime_id": "other-runtime"}),
            (BIND, {"node_id": "other-node"}),
            (BIND, {"provider_socket_name": "other-socket"}),
        )
        for relation, changes in cases:
            with self.subTest(relation=relation, fields=tuple(changes)):
                with self.assertRaises(psycopg.errors.ForeignKeyViolation):
                    with self.connection.transaction():
                        assignments = sql.SQL(", ").join(sql.SQL("{}=%s").format(sql.Identifier(key)) for key in changes)
                        self.connection.execute(sql.SQL("UPDATE {} SET {}").format(
                            sql.Identifier(relation), assignments), tuple(changes.values()))
                self.assertEqual(self.truth(), before)

    def test_receiver_digest_and_witness_checks_reject_invalid_committed_shapes(self):
        _, _, action, _ = self.seed()
        cases = [(INTRO, {"receiver_id": value}) for value in ("A" * 32, "a" * 31, "g" * 32)]
        cases += [(BIND, {field: value}) for field in ("selected_configuration_digest", "declaration_identity")
                  for value in ("A" * 64, "a" * 63, "z" * 64)]
        cases += [
            (INTRO, {"first_accepted_action_id": action.action_id}),
            (INTRO, {"first_accepted_session_id": action.session_id}),
            (INTRO, {"retired_action_id": action.action_id}),
            (INTRO, {"retired_session_id": action.session_id}),
            (INTRO, {"retired_action_id": action.action_id, "retired_session_id": action.session_id}),
            (INTRO, {"first_accepted_action_id": action.action_id, "first_accepted_session_id": action.session_id,
                     "retired_action_id": action.action_id, "retired_session_id": action.session_id}),
        ]
        for relation, changes in cases:
            with self.subTest(relation=relation, fields=tuple(changes)):
                assignments = sql.SQL(", ").join(sql.SQL("{}=%s").format(sql.Identifier(key)) for key in changes)
                with self.assertRaises(psycopg.errors.CheckViolation):
                    self.connection.execute(sql.SQL("UPDATE {} SET {}").format(
                        sql.Identifier(relation), assignments), tuple(changes.values()))

    def test_binding_digest_drift_is_rejected_by_query_only_current_validation(self):
        self.seed()
        self.connection.execute("UPDATE cpk_graph_receiver_bindings SET selected_configuration_digest=%s", ("f" * 64,))
        before = self.truth()
        with self.assertRaises(SchemaInstallationError) as caught:
            install_schema(self.connection)
        self.assert_bounded(caught.exception, "operations schema reset is required")
        self.assertEqual(self.truth(), before)
        self.assertEqual(self.connection.execute(
            "SELECT selected_configuration_digest FROM cpk_graph_receiver_bindings").fetchone(), ("f" * 64,))
