from __future__ import annotations

from datetime import datetime, timezone
import inspect
import os
import unittest
import uuid

import psycopg
from psycopg.types.json import Jsonb

from control_plane_kit_core.planning import ActivityPlan
from control_plane_kit_core.topology import DEFAULT_GRAPH_CODEC, DeploymentGraph
import control_plane_kit_operations.postgres as postgres
from control_plane_kit_operations.postgres import migration_runner
from control_plane_kit_operations.postgres import schema as schema_module
from control_plane_kit_operations.postgres.activity_history import (
    PostgresActivityHistoryStore,
)
from control_plane_kit_operations.records import (
    ActivityPlanRecord,
    ActivityPlanStatus,
    GraphVersionRecord,
    OperationsRecordError,
    RealizedGraphProjectionKind,
    RealizedGraphProjectionRecord,
)
from tests.graph_lineage_fixture import seed_historical_graph_lineage_constraints


_V17_IDENTITY = (17, "graph-lineage-compatibility")
_CATEGORICAL_ERROR = "graph lineage compatibility is not accepted"
_LINEAGE_CONSTRAINTS = {
    "cpk_realized_graph_projection_workspace_identity",
    "cpk_realized_graph_projection_source_identity",
    "cpk_workspaces_current_realized_projection_fk",
    "cpk_workspaces_desired_realized_projection_fk",
    "cpk_workspaces_current_projection_source_fk",
    "cpk_workspaces_desired_projection_source_fk",
    "cpk_workspaces_current_lineage_check",
    "cpk_workspaces_desired_lineage_check",
    "cpk_activity_plans_base_projection_source_fk",
    "cpk_activity_plans_desired_projection_source_fk",
    "cpk_workspaces_desired_graph_revision_check",
    "cpk_activity_plans_desired_graph_revision_check",
}
_V1_DEPENDENCIES = {
    "cpk_workspaces_pkey",
    "cpk_graph_versions_pkey",
    "cpk_graph_versions_workspace_identity",
    "cpk_realized_graph_projections_pkey",
    "cpk_realized_graph_projection_source",
    "cpk_realized_graph_projection_identity",
    "cpk_realized_graph_projection_kind_check",
    "cpk_realized_graph_projection_digest_check",
    "cpk_activity_plans_session_id_fkey",
}


class GraphLineageCompatibilityMigrationTests(unittest.TestCase):
    def setUp(self) -> None:
        database_url = os.environ.get("CPK_OPERATIONS_TEST_DATABASE_URL")
        if not database_url:
            raise RuntimeError(
                "CPK_OPERATIONS_TEST_DATABASE_URL is required; use Docker-first tests"
            )
        self.database_url = database_url
        self.schema = f"graph_lineage_{uuid.uuid4().hex}"
        self.admin = psycopg.connect(database_url, autocommit=True)
        self.admin.execute(f'CREATE SCHEMA "{self.schema}"')

    def tearDown(self) -> None:
        self.admin.execute(f'DROP SCHEMA IF EXISTS "{self.schema}_other" CASCADE')
        self.admin.execute(f'DROP SCHEMA IF EXISTS "{self.schema}" CASCADE')
        self.admin.close()

    def test_registry_appends_closed_three_step_v17_and_retires_live_helper(self) -> None:
        registry = postgres.POSTGRES_SCHEMA_MIGRATIONS

        self.assertEqual(registry.target_version, 17)
        self.assertEqual(
            (registry.migrations[-1].version, registry.migrations[-1].name),
            _V17_IDENTITY,
        )
        migration = registry.migrations[-1]
        self.assertEqual(len(migration.steps), 3)
        self.assertIs(type(migration.steps[0]), postgres.SqlMigrationStep)
        self.assertIs(type(migration.steps[1]), postgres.DeterministicBackfillStep)
        self.assertIs(type(migration.steps[2]), postgres.SqlMigrationStep)
        self.assertEqual(
            migration.steps[1].kind,
            postgres.SchemaBackfillKind.GRAPH_LINEAGE,
        )
        self.assertEqual(migration.steps[1].algorithm_version, 1)
        prepare = migration.steps[0].sql
        self.assertNotIn("ALTER TABLE", prepare)
        for relation in (
            "cpk_workspaces",
            "cpk_graph_versions",
            "cpk_realized_graph_projections",
            "cpk_activity_plans",
        ):
            self.assertIn(f"LOCK TABLE {relation} IN ACCESS EXCLUSIVE MODE", prepare)
        self.assertLess(prepare.index("cpk_workspaces"), prepare.index("cpk_graph_versions"))
        self.assertLess(
            prepare.index("cpk_graph_versions"),
            prepare.index("cpk_realized_graph_projections"),
        )
        self.assertLess(
            prepare.index("cpk_realized_graph_projections"),
            prepare.index("cpk_activity_plans"),
        )
        runner_source = inspect.getsource(migration_runner)
        self.assertNotIn("_backfill_graph_lineage", runner_source)
        self.assertNotIn("_GRAPH_LINEAGE_CONSTRAINTS", runner_source)
        self.assertNotIn(
            "ADD COLUMN IF NOT EXISTS current_realized_projection_id",
            getattr(schema_module, "_CURRENT_POSTGRES_SCHEMA"),
        )
        self.assertEqual(
            postgres.POSTGRES_SCHEMA_V1_SHA256,
            "fc9b5547fc51ec681130c41facea785dbd24649049417455b184ea05886beed8",
        )

    def test_missing_workspace_and_plan_paths_backfill_exact_identity(self) -> None:
        connection = self._connection()
        try:
            self._prepare_v16(connection)
            self._seed_workspace_graph_plan_truth(connection)
            expected_a = self._expected("graph-a", "workspace-a", 1)
            expected_b = self._expected("graph-b", "workspace-a", 2)

            postgres.install_postgres_schema(connection)

            self.assertEqual(
                connection.execute(
                    "SELECT current_realized_projection_id, "
                    "desired_realized_projection_id, desired_graph_revision "
                    "FROM cpk_workspaces WHERE workspace_id='workspace-a'"
                ).fetchone(),
                (expected_a.projection_id, expected_b.projection_id, 7),
            )
            self.assertEqual(
                connection.execute(
                    "SELECT base_realized_projection_id, "
                    "desired_realized_projection_id, desired_graph_revision "
                    "FROM cpk_activity_plans WHERE plan_id='plan-a'"
                ).fetchone(),
                (expected_a.projection_id, expected_b.projection_id, 9),
            )
            self.assertEqual(
                set(
                    connection.execute(
                        "SELECT projection_id FROM cpk_realized_graph_projections"
                    ).fetchall()
                ),
                {(expected_a.projection_id,), (expected_b.projection_id,)},
            )
            self.assertEqual(self._history(connection)[-1][:2], _V17_IDENTITY)
            self.assertEqual(
                connection.execute(
                    "SELECT column_name, is_nullable "
                    "FROM information_schema.columns "
                    "WHERE table_schema=current_schema() "
                    "AND table_name='cpk_activity_plans' "
                    "AND column_name IN ('base_realized_projection_id', "
                    "'desired_realized_projection_id') ORDER BY column_name"
                ).fetchall(),
                [
                    ("base_realized_projection_id", "NO"),
                    ("desired_realized_projection_id", "NO"),
                ],
            )
        finally:
            connection.close()

    def test_current_plan_store_rejects_incomplete_lineage_before_sql(self) -> None:
        connection = _NoSqlConnection()

        with self.assertRaisesRegex(
            OperationsRecordError,
            "^activity plan record requires complete graph lineage$",
        ):
            PostgresActivityHistoryStore(connection).add_plan(
                self._plan_record("plan-incomplete")
            )

        self.assertEqual(connection.calls, [])

    def test_current_plan_store_rejects_invalid_complete_lineage(self) -> None:
        for case in (
            "missing-session",
            "projection-source-mismatch",
            "cross-workspace",
        ):
            with self.subTest(case=case):
                self._reset_schema()
                connection = self._connection()
                try:
                    self._prepare_v16(connection)
                    self._seed_workspace_graph_plan_truth(connection)
                    if case == "cross-workspace":
                        connection.execute(
                            "INSERT INTO cpk_workspaces "
                            "(workspace_id, name, lifecycle) VALUES "
                            "('workspace-b', 'Workspace B', 'created')"
                        )
                        self._insert_graph(connection, "graph-c", "workspace-b", 1)
                        self._insert_graph(connection, "graph-d", "workspace-b", 2)
                        self._insert_projection(
                            connection,
                            self._expected("graph-c", "workspace-b", 1),
                        )
                        self._insert_projection(
                            connection,
                            self._expected("graph-d", "workspace-b", 2),
                        )
                    postgres.install_postgres_schema(connection)
                    base = self._expected("graph-a", "workspace-a", 1)
                    desired = self._expected("graph-b", "workspace-a", 2)
                    record = self._plan_record(
                        f"plan-{case}",
                        base_projection_id=base.projection_id,
                        desired_projection_id=desired.projection_id,
                    )
                    if case == "missing-session":
                        record = self._plan_record(
                            f"plan-{case}",
                            session_id="session-missing",
                            base_projection_id=base.projection_id,
                            desired_projection_id=desired.projection_id,
                        )
                    elif case == "projection-source-mismatch":
                        record = self._plan_record(
                            f"plan-{case}",
                            base_projection_id=desired.projection_id,
                            desired_projection_id=base.projection_id,
                        )
                    elif case == "cross-workspace":
                        foreign_base = self._expected("graph-c", "workspace-b", 1)
                        foreign_desired = self._expected("graph-d", "workspace-b", 2)
                        record = self._plan_record(
                            f"plan-{case}",
                            base_graph_id="graph-c",
                            desired_graph_id="graph-d",
                            base_projection_id=foreign_base.projection_id,
                            desired_projection_id=foreign_desired.projection_id,
                        )
                    before = connection.execute(
                        "SELECT count(*) FROM cpk_activity_plans"
                    ).fetchone()

                    with self.assertRaisesRegex(
                        OperationsRecordError,
                        "^activity plan record requires complete graph lineage$",
                    ):
                        PostgresActivityHistoryStore(connection).add_plan(record)

                    self.assertEqual(
                        connection.execute(
                            "SELECT count(*) FROM cpk_activity_plans"
                        ).fetchone(),
                        before,
                    )
                finally:
                    connection.close()

    def test_current_plan_store_accepts_exact_non_identity_lineage(self) -> None:
        connection = self._connection()
        try:
            self._prepare_v16(connection)
            self._seed_workspace_graph_plan_truth(connection)
            postgres.install_postgres_schema(connection)
            base = self._non_identity("graph-a", "workspace-a", "base")
            desired = self._non_identity("graph-b", "workspace-a", "desired")
            self._insert_projection(connection, base)
            self._insert_projection(connection, desired)
            record = self._plan_record(
                "plan-non-identity",
                base_projection_id=base.projection_id,
                desired_projection_id=desired.projection_id,
            )

            stored = PostgresActivityHistoryStore(connection).add_plan(record)

            self.assertEqual(stored, record)
            self.assertEqual(
                PostgresActivityHistoryStore(connection).get_plan(record.plan_id),
                record,
            )
        finally:
            connection.close()

    def test_exact_non_identity_lineage_survives_v17_and_reinstall(self) -> None:
        connection = self._connection()
        try:
            self._prepare_v16(connection)
            self._seed_workspace_graph_plan_truth(connection)
            base = self._non_identity("graph-a", "workspace-a", "base")
            desired = self._non_identity("graph-b", "workspace-a", "desired")
            self._insert_projection(connection, base)
            self._insert_projection(connection, desired)
            connection.execute(
                "UPDATE cpk_workspaces SET current_realized_projection_id=%s, "
                "desired_realized_projection_id=%s WHERE workspace_id='workspace-a'",
                (base.projection_id, desired.projection_id),
            )
            connection.execute(
                "UPDATE cpk_activity_plans SET base_realized_projection_id=%s, "
                "desired_realized_projection_id=%s WHERE plan_id='plan-a'",
                (base.projection_id, desired.projection_id),
            )

            postgres.install_postgres_schema(connection)
            postgres.install_postgres_schema(connection)

            self.assertEqual(
                connection.execute(
                    "SELECT current_realized_projection_id, "
                    "desired_realized_projection_id FROM cpk_workspaces "
                    "WHERE workspace_id='workspace-a'"
                ).fetchone(),
                (base.projection_id, desired.projection_id),
            )
            self.assertEqual(
                connection.execute(
                    "SELECT base_realized_projection_id, "
                    "desired_realized_projection_id FROM cpk_activity_plans "
                    "WHERE plan_id='plan-a'"
                ).fetchone(),
                (base.projection_id, desired.projection_id),
            )
        finally:
            connection.close()

    def test_current_verifier_accepts_only_exact_non_identity_projection(self) -> None:
        connection = self._connection()
        try:
            postgres.install_postgres_schema(connection)
            connection.execute(
                "INSERT INTO cpk_workspaces "
                "(workspace_id, name, lifecycle) VALUES "
                "('workspace-current', 'Current', 'created')"
            )
            self._insert_graph(connection, "graph-current", "workspace-current", 1)
            projection = self._non_identity(
                "graph-current",
                "workspace-current",
                "current",
            )
            self._insert_projection(connection, projection)
            connection.execute(
                "UPDATE cpk_workspaces SET current_graph_id='graph-current', "
                "desired_graph_id='graph-current', "
                "current_realized_projection_id=%s, "
                "desired_realized_projection_id=%s "
                "WHERE workspace_id='workspace-current'",
                (projection.projection_id, projection.projection_id),
            )

            postgres.install_postgres_schema(connection)

            self.assertEqual(
                connection.execute(
                    "SELECT count(*) FROM cpk_realized_graph_projections "
                    "WHERE source_authored_graph_id='graph-current' "
                    "AND projection_kind='identity'"
                ).fetchone(),
                (0,),
            )
        finally:
            connection.close()

    def test_current_plan_store_writes_complete_exact_lineage(self) -> None:
        connection = self._connection()
        try:
            self._prepare_v16(connection)
            self._seed_workspace_graph_plan_truth(connection)
            postgres.install_postgres_schema(connection)
            base = self._expected("graph-a", "workspace-a", 1)
            desired = self._expected("graph-b", "workspace-a", 2)
            record = self._plan_record(
                "plan-complete",
                base_projection_id=base.projection_id,
                desired_projection_id=desired.projection_id,
            )

            stored = PostgresActivityHistoryStore(connection).add_plan(record)

            self.assertEqual(stored, record)
            self.assertEqual(
                PostgresActivityHistoryStore(connection).get_plan("plan-complete"),
                record,
            )
        finally:
            connection.close()

    def test_current_database_rejects_direct_null_plan_lineage(self) -> None:
        connection = self._connection()
        try:
            self._prepare_v16(connection)
            self._seed_workspace_graph_plan_truth(connection)
            postgres.install_postgres_schema(connection)

            with self.assertRaises(psycopg.errors.NotNullViolation):
                connection.execute(
                    "INSERT INTO cpk_activity_plans "
                    "(plan_id, session_id, base_graph_id, desired_graph_id, status, "
                    "created_at, payload) VALUES "
                    "('plan-null', 'session-a', 'graph-a', 'graph-b', 'planned', "
                    "'2026-08-10T00:00:04Z', '{}'::jsonb)"
                )
        finally:
            connection.close()

    def test_exact_projection_is_reused_without_row_or_object_rewrite(self) -> None:
        connection = self._connection()
        try:
            self._prepare_v16(connection)
            self._seed_workspace_graph_plan_truth(connection)
            expected = self._expected("graph-a", "workspace-a", 1)
            self._insert_projection(connection, expected)
            before = connection.execute(
                "SELECT ctid::text, xmin::text, to_jsonb(projection) "
                "FROM cpk_realized_graph_projections AS projection "
                "WHERE projection_id=%s",
                (expected.projection_id,),
            ).fetchone()
            before_constraints = self._constraint_identities(connection)

            postgres.install_postgres_schema(connection)

            after = connection.execute(
                "SELECT ctid::text, xmin::text, to_jsonb(projection) "
                "FROM cpk_realized_graph_projections AS projection "
                "WHERE projection_id=%s",
                (expected.projection_id,),
            ).fetchone()
            self.assertEqual(after, before)
            after_constraints = self._constraint_identities(connection)
            for name, identity in before_constraints.items():
                with self.subTest(constraint=name):
                    self.assertEqual(after_constraints[name], identity)
        finally:
            connection.close()

    def test_invalid_retained_truth_fails_before_any_v17_mutation(self) -> None:
        cases = (
            "projection-collision",
            "missing-graph",
            "cross-workspace",
            "plan-session-workspace",
            "malformed-graph",
            "oversized-graph",
            "negative-workspace-revision",
            "negative-plan-revision",
            "missing-plan-session",
            "null-plan-session",
        )
        for case in cases:
            with self.subTest(case=case):
                self._reset_schema()
                connection = self._connection()
                try:
                    self._prepare_v16(connection)
                    self._seed_workspace_graph_plan_truth(connection)
                    self._make_invalid(connection, case)
                    before = self._snapshot(connection)

                    with self.assertRaisesRegex(
                        postgres.SchemaMigrationError,
                        f"^{_CATEGORICAL_ERROR}$",
                    ) as raised:
                        postgres.install_postgres_schema(connection)

                    self.assertIsNone(raised.exception.__cause__)
                    self.assertIsNone(raised.exception.__context__)
                    self.assertLessEqual(len(str(raised.exception)), 256)
                    self.assertEqual(self._snapshot(connection), before)
                    self.assertNotIn(17, {row[0] for row in self._history(connection)})
                finally:
                    connection.close()

    def test_keyset_batch_boundaries_have_no_total_row_cap(self) -> None:
        for count in (0, 1, 63, 64, 65, 129):
            with self.subTest(count=count):
                self._reset_schema()
                connection = self._connection()
                try:
                    self._prepare_v16(connection)
                    for index in range(count):
                        workspace = f"workspace-{index:03d}"
                        graph = f"graph-{index:03d}"
                        connection.execute(
                            "INSERT INTO cpk_workspaces "
                            "(workspace_id, name, lifecycle, current_graph_id) "
                            "VALUES (%s, %s, 'created', %s)",
                            (workspace, workspace, graph),
                        )
                        self._insert_graph(connection, graph, workspace, 1)

                    postgres.install_postgres_schema(connection)

                    self.assertEqual(
                        connection.execute(
                            "SELECT count(*) FROM cpk_realized_graph_projections"
                        ).fetchone(),
                        (count,),
                    )
                    self.assertEqual(
                        connection.execute(
                            "SELECT count(*) FROM cpk_workspaces "
                            "WHERE current_realized_projection_id IS NOT NULL"
                        ).fetchone(),
                        (count,),
                    )
                finally:
                    connection.close()

    def test_v1_identity_dependency_drift_rejects_without_rebuild(self) -> None:
        for constraint in sorted(_V1_DEPENDENCIES):
            with self.subTest(constraint=constraint):
                self._reset_schema()
                connection = self._connection()
                try:
                    self._prepare_v16(connection)
                    relation = connection.execute(
                        "SELECT relation.relname FROM pg_constraint AS constraints "
                        "JOIN pg_class AS relation ON relation.oid=constraints.conrelid "
                        "JOIN pg_namespace AS namespace "
                        "ON namespace.oid=relation.relnamespace "
                        "WHERE namespace.nspname=current_schema() "
                        "AND constraints.conname=%s",
                        (constraint,),
                    ).fetchone()[0]
                    connection.execute(
                        f"ALTER TABLE {relation} DROP CONSTRAINT {constraint} CASCADE"
                    )
                    before = self._snapshot(connection)

                    with self.assertRaises(postgres.SchemaMigrationError):
                        postgres.install_postgres_schema(connection)

                    self.assertEqual(self._snapshot(connection), before)
                    self.assertNotIn(constraint, self._all_constraint_names(connection))
                finally:
                    connection.close()

    def test_wrong_or_unvalidated_owned_constraint_rejects_before_effects(self) -> None:
        mutations = (
            (
                "cpk_workspaces",
                "cpk_workspaces_current_lineage_check",
                "CHECK (current_graph_id IS NULL)",
            ),
            (
                "cpk_activity_plans",
                "cpk_activity_plans_base_projection_source_fk",
                "FOREIGN KEY (base_realized_projection_id, base_graph_id) "
                "REFERENCES cpk_realized_graph_projections"
                "(projection_id, source_authored_graph_id) NOT VALID",
            ),
        )
        for table, constraint, definition in mutations:
            with self.subTest(constraint=constraint):
                self._reset_schema()
                connection = self._connection()
                try:
                    self._prepare_v16(connection)
                    seed_historical_graph_lineage_constraints(connection)
                    connection.execute(
                        f"ALTER TABLE {table} DROP CONSTRAINT {constraint}"
                    )
                    connection.execute(
                        f"ALTER TABLE {table} ADD CONSTRAINT {constraint} {definition}"
                    )
                    before = self._snapshot(connection)
                    with self.assertRaisesRegex(
                        postgres.SchemaMigrationError,
                        f"^{_CATEGORICAL_ERROR}$",
                    ):
                        postgres.install_postgres_schema(connection)
                    self.assertEqual(self._snapshot(connection), before)
                finally:
                    connection.close()

    def test_projection_identity_key_and_each_material_field_mismatch_reject(self) -> None:
        cases = (
            "identity-key",
            "digest",
            "descriptor",
            "created-by",
            "created-at",
        )
        for case in cases:
            with self.subTest(case=case):
                self._reset_schema()
                connection = self._connection()
                try:
                    self._prepare_v16(connection)
                    self._seed_workspace_graph_plan_truth(connection)
                    expected = self._expected("graph-a", "workspace-a", 1)
                    self._insert_projection(connection, expected)
                    if case == "identity-key":
                        connection.execute(
                            "UPDATE cpk_realized_graph_projections "
                            "SET projection_id='private-collision' "
                            "WHERE projection_id=%s",
                            (expected.projection_id,),
                        )
                    elif case == "digest":
                        connection.execute(
                            "UPDATE cpk_realized_graph_projections "
                            "SET projection_digest=%s WHERE projection_id=%s",
                            ("a" * 64, expected.projection_id),
                        )
                    elif case == "descriptor":
                        connection.execute(
                            "UPDATE cpk_realized_graph_projections "
                            "SET graph_descriptor=%s WHERE projection_id=%s",
                            (
                                Jsonb(
                                    DEFAULT_GRAPH_CODEC.encode(
                                        DeploymentGraph("private-different")
                                    )
                                ),
                                expected.projection_id,
                            ),
                        )
                    elif case == "created-by":
                        connection.execute(
                            "UPDATE cpk_realized_graph_projections "
                            "SET created_by='private-actor' WHERE projection_id=%s",
                            (expected.projection_id,),
                        )
                    else:
                        connection.execute(
                            "UPDATE cpk_realized_graph_projections "
                            "SET created_at='2026-08-10T00:01:01Z' "
                            "WHERE projection_id=%s",
                            (expected.projection_id,),
                        )
                    before = self._snapshot(connection)
                    with self.assertRaisesRegex(
                        postgres.SchemaMigrationError,
                        f"^{_CATEGORICAL_ERROR}$",
                    ):
                        postgres.install_postgres_schema(connection)
                    self.assertEqual(self._snapshot(connection), before)
                finally:
                    connection.close()

    def test_each_v17_phase_failure_restores_exact_v16_truth(self) -> None:
        for failure in ("prepare", "backfill", "final", "ledger", "verifier"):
            with self.subTest(failure=failure):
                self._reset_schema()
                connection = self._connection()
                try:
                    self._prepare_v16(connection)
                    self._seed_workspace_graph_plan_truth(connection)
                    before = self._snapshot(connection)
                    migration = postgres.POSTGRES_SCHEMA_MIGRATIONS.migrations[16]
                    failed = False
                    v17_ledger_written = False
                    omitted = object()

                    class FailingConnection:
                        @property
                        def autocommit(self):
                            return connection.autocommit

                        def transaction(self):
                            return connection.transaction()

                        def execute(self, query, params=omitted):
                            nonlocal failed, v17_ledger_written
                            should_fail = (
                                (failure == "prepare" and query == migration.steps[0].sql)
                                or (
                                    failure == "backfill"
                                    and type(query) is str
                                    and "WITH referenced(graph_id)" in query
                                )
                                or (failure == "final" and query == migration.steps[2].sql)
                                or (
                                    failure == "ledger"
                                    and type(query) is str
                                    and "INSERT INTO cpk_schema_migrations" in query
                                    and params is not omitted
                                    and params
                                    and params[0] == 17
                                )
                                or (
                                    failure == "verifier"
                                    and v17_ledger_written
                                    and type(query) is str
                                    and "SELECT version," in query
                                )
                            )
                            if should_fail and not failed:
                                failed = True
                                raise RuntimeError("private-provider-material")
                            result = (
                                connection.execute(query)
                                if params is omitted
                                else connection.execute(query, params)
                            )
                            if (
                                type(query) is str
                                and "INSERT INTO cpk_schema_migrations" in query
                                and params is not omitted
                                and params
                                and params[0] == 17
                            ):
                                v17_ledger_written = True
                            return result

                    with self.assertRaises(postgres.SchemaMigrationError) as raised:
                        postgres.install_postgres_schema(FailingConnection())
                    self.assertTrue(failed, str(raised.exception))
                    self.assertNotIn("private-provider-material", repr(raised.exception))
                    self.assertIsNone(raised.exception.__cause__)
                    self.assertIsNone(raised.exception.__context__)
                    self.assertEqual(self._snapshot(connection), before)
                finally:
                    connection.close()

    def test_installer_and_service_lock_orders_are_bounded_and_release(self) -> None:
        owner = self._connection(autocommit=False)
        contender = self._connection(autocommit=False)
        try:
            self._prepare_v16(owner)
            owner.execute(
                "INSERT INTO cpk_workspaces (workspace_id, name, lifecycle) "
                "VALUES ('workspace-a', 'Workspace A', 'created')"
            )
            owner.commit()

            postgres.install_postgres_schema(owner)
            contender.execute("SET LOCAL lock_timeout='150ms'")
            with self.assertRaises(psycopg.errors.LockNotAvailable):
                contender.execute(
                    "UPDATE cpk_workspaces SET name='Blocked' "
                    "WHERE workspace_id='workspace-a'"
                )
            contender.rollback()
            owner.rollback()

            contender.execute(
                "UPDATE cpk_workspaces SET name='Service first' "
                "WHERE workspace_id='workspace-a'"
            )
            owner.execute("SET LOCAL lock_timeout='150ms'")
            with self.assertRaises(postgres.SchemaMigrationError):
                postgres.install_postgres_schema(owner)
            owner.rollback()
            contender.rollback()

            postgres.install_postgres_schema(owner)
            owner.rollback()
        finally:
            owner.close()
            contender.close()

    def test_absent_columns_and_relation_owned_constraints_converge(self) -> None:
        connection = self._connection()
        try:
            self._prepare_v16(connection)
            self._drop_lineage_constraints(connection)
            connection.execute(
                "ALTER TABLE cpk_workspaces "
                "DROP COLUMN current_realized_projection_id, "
                "DROP COLUMN desired_realized_projection_id, "
                "DROP COLUMN desired_graph_revision"
            )
            connection.execute(
                "ALTER TABLE cpk_activity_plans "
                "DROP COLUMN base_realized_projection_id, "
                "DROP COLUMN desired_realized_projection_id, "
                "DROP COLUMN desired_graph_revision"
            )
            self.admin.execute(f'CREATE SCHEMA "{self.schema}_other"')
            for table, constraint in (
                (
                    "cpk_workspaces",
                    "cpk_workspaces_current_lineage_check",
                ),
                (
                    "cpk_activity_plans",
                    "cpk_activity_plans_desired_projection_source_fk",
                ),
            ):
                self.admin.execute(
                    f'CREATE TABLE "{self.schema}_other".{table} (value text)'
                )
                self.admin.execute(
                    f'ALTER TABLE "{self.schema}_other".{table} ADD CONSTRAINT '
                    f"{constraint} UNIQUE (value)"
                )
            lookalikes = self._lookalikes(connection)

            postgres.install_postgres_schema(connection)

            self.assertEqual(self._lookalikes(connection), lookalikes)
            self.assertEqual(set(self._constraint_identities(connection)), _LINEAGE_CONSTRAINTS)
            columns = self._lineage_columns(connection)
            self.assertEqual(
                columns,
                {
                    ("cpk_workspaces", "current_realized_projection_id"): (
                        "text",
                        "YES",
                        None,
                    ),
                    ("cpk_workspaces", "desired_realized_projection_id"): (
                        "text",
                        "YES",
                        None,
                    ),
                    ("cpk_workspaces", "desired_graph_revision"): (
                        "bigint",
                        "NO",
                        "0",
                    ),
                    ("cpk_activity_plans", "base_realized_projection_id"): (
                        "text",
                        "NO",
                        None,
                    ),
                    ("cpk_activity_plans", "desired_realized_projection_id"): (
                        "text",
                        "NO",
                        None,
                    ),
                    ("cpk_activity_plans", "desired_graph_revision"): (
                        "bigint",
                        "NO",
                        "0",
                    ),
                },
            )
        finally:
            connection.close()

    def test_partial_or_wrong_column_family_rejects_without_mutation(self) -> None:
        mutations = (
            "ALTER TABLE cpk_workspaces DROP COLUMN current_realized_projection_id",
            "ALTER TABLE cpk_workspaces ALTER COLUMN desired_graph_revision DROP DEFAULT",
            "ALTER TABLE cpk_activity_plans DROP COLUMN base_realized_projection_id",
            "ALTER TABLE cpk_activity_plans ALTER COLUMN desired_graph_revision DROP NOT NULL",
        )
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                self._reset_schema()
                connection = self._connection()
                try:
                    self._prepare_v16(connection)
                    self._drop_lineage_constraints(connection)
                    connection.execute(mutation)
                    before = self._snapshot(connection)
                    with self.assertRaisesRegex(
                        postgres.SchemaMigrationError,
                        f"^{_CATEGORICAL_ERROR}$",
                    ):
                        postgres.install_postgres_schema(connection)
                    self.assertEqual(self._snapshot(connection), before)
                finally:
                    connection.close()

    def test_already_v17_drift_is_rejected_twice_without_repair(self) -> None:
        for table, constraint in (
            ("cpk_workspaces", "cpk_workspaces_current_lineage_check"),
            ("cpk_activity_plans", "cpk_activity_plans_session_id_fkey"),
        ):
            with self.subTest(constraint=constraint):
                self._reset_schema()
                connection = self._connection()
                try:
                    postgres.install_postgres_schema(connection)
                    connection.execute(
                        f"ALTER TABLE {table} DROP CONSTRAINT {constraint}"
                    )
                    before = self._snapshot(connection)

                    for _attempt in range(2):
                        with self.assertRaisesRegex(
                            postgres.SchemaMigrationError,
                            "^graph lineage schema is not current$",
                        ):
                            postgres.install_postgres_schema(connection)
                        self.assertEqual(self._snapshot(connection), before)
                finally:
                    connection.close()

    def test_already_v17_null_plan_session_is_rejected_without_repair(self) -> None:
        connection = self._connection()
        try:
            self._prepare_v16(connection)
            self._seed_workspace_graph_plan_truth(connection)
            postgres.install_postgres_schema(connection)
            connection.execute(
                "ALTER TABLE cpk_activity_plans ALTER COLUMN session_id DROP NOT NULL"
            )
            connection.execute(
                "UPDATE cpk_activity_plans SET session_id=NULL WHERE plan_id='plan-a'"
            )
            before = self._snapshot(connection)

            with self.assertRaisesRegex(
                postgres.SchemaMigrationError,
                "^graph lineage schema is not current$",
            ):
                postgres.install_postgres_schema(connection)
            self.assertEqual(self._snapshot(connection), before)
        finally:
            connection.close()

    def test_already_v17_nullable_plan_lineage_is_rejected_without_repair(self) -> None:
        connection = self._connection()
        try:
            self._prepare_v16(connection)
            self._seed_workspace_graph_plan_truth(connection)
            postgres.install_postgres_schema(connection)
            connection.execute(
                "ALTER TABLE cpk_activity_plans "
                "ALTER COLUMN base_realized_projection_id DROP NOT NULL, "
                "ALTER COLUMN desired_realized_projection_id DROP NOT NULL"
            )
            before = self._snapshot(connection)

            for _attempt in range(2):
                with self.assertRaisesRegex(
                    postgres.SchemaMigrationError,
                    "^graph lineage schema is not current$",
                ):
                    postgres.install_postgres_schema(connection)
                self.assertEqual(self._snapshot(connection), before)
        finally:
            connection.close()

    def test_caller_rollback_restores_exact_v16_truth(self) -> None:
        connection = self._connection(autocommit=False)
        try:
            self._prepare_v16(connection)
            self._seed_workspace_graph_plan_truth(connection)
            connection.commit()
            before = self._snapshot(connection)

            postgres.install_postgres_schema(connection)
            self.assertEqual(self._history(connection)[-1][:2], _V17_IDENTITY)
            connection.rollback()

            self.assertEqual(self._snapshot(connection), before)
        finally:
            connection.close()

    def _prepare_v16(self, connection) -> None:
        connection.execute(postgres.POSTGRES_SCHEMA)
        for migration in postgres.POSTGRES_SCHEMA_MIGRATIONS.migrations[1:16]:
            migration_runner._apply_schema_migration(connection, migration)
        connection.execute(
            "CREATE TABLE cpk_schema_migrations ("
            "version integer NOT NULL PRIMARY KEY, "
            "name text NOT NULL, checksum_sha256 text NOT NULL, "
            "applied_at timestamptz NOT NULL DEFAULT clock_timestamp())"
        )
        for migration in postgres.POSTGRES_SCHEMA_MIGRATIONS.migrations[:16]:
            connection.execute(
                "INSERT INTO cpk_schema_migrations "
                "(version, name, checksum_sha256) VALUES (%s, %s, %s)",
                (migration.version, migration.name, migration.checksum_sha256),
            )

    def _seed_workspace_graph_plan_truth(self, connection) -> None:
        connection.execute(
            "INSERT INTO cpk_workspaces "
            "(workspace_id, name, lifecycle, current_graph_id, desired_graph_id, "
            "desired_graph_revision) VALUES "
            "('workspace-a', 'Workspace A', 'created', 'graph-a', 'graph-b', 7)"
        )
        self._insert_graph(connection, "graph-a", "workspace-a", 1)
        self._insert_graph(connection, "graph-b", "workspace-a", 2)
        connection.execute(
            "INSERT INTO cpk_operation_sessions "
            "(session_id, workspace_id, actor_id, title, status, created_at) VALUES "
            "('session-a', 'workspace-a', 'operator-a', 'Deploy', 'open', "
            "'2026-08-10T00:00:02Z')"
        )
        connection.execute(
            "INSERT INTO cpk_activity_plans "
            "(plan_id, session_id, base_graph_id, desired_graph_id, "
            "desired_graph_revision, status, created_at, payload) VALUES "
            "('plan-a', 'session-a', 'graph-a', 'graph-b', 9, 'planned', "
            "'2026-08-10T00:00:03Z', '{}'::jsonb)"
        )

    def _insert_graph(self, connection, graph_id: str, workspace_id: str, version: int) -> None:
        connection.execute(
            "INSERT INTO cpk_graph_versions "
            "(graph_id, workspace_id, version, graph_descriptor, created_by, created_at) "
            "VALUES (%s, %s, %s, %s, 'operator-a', %s)",
            (
                graph_id,
                workspace_id,
                version,
                Jsonb(DEFAULT_GRAPH_CODEC.encode(DeploymentGraph(graph_id))),
                datetime(2026, 8, 10, 0, 0, version, tzinfo=timezone.utc),
            ),
        )

    @staticmethod
    def _insert_projection(connection, record: RealizedGraphProjectionRecord) -> None:
        connection.execute(
            "INSERT INTO cpk_realized_graph_projections "
            "(projection_id, workspace_id, source_authored_graph_id, projection_kind, "
            "projection_key, projection_digest, graph_descriptor, created_by, created_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (
                record.projection_id,
                record.workspace_id,
                record.source_authored_graph_id,
                record.projection_kind.value,
                record.projection_key,
                record.projection_digest,
                Jsonb(record.graph_descriptor),
                record.created_by,
                record.created_at,
            ),
        )

    @staticmethod
    def _plan_record(
        plan_id: str,
        *,
        session_id: str = "session-a",
        base_graph_id: str = "graph-a",
        desired_graph_id: str = "graph-b",
        base_projection_id: str | None = None,
        desired_projection_id: str | None = None,
    ) -> ActivityPlanRecord:
        return ActivityPlanRecord(
            plan_id=plan_id,
            session_id=session_id,
            base_graph_id=base_graph_id,
            desired_graph_id=desired_graph_id,
            status=ActivityPlanStatus.PLANNED,
            created_at="2026-08-10T00:00:04Z",
            plan=ActivityPlan(()),
            base_realized_projection_id=base_projection_id,
            desired_realized_projection_id=desired_projection_id,
            desired_graph_revision=9,
        )

    @staticmethod
    def _expected(graph_id: str, workspace_id: str, version: int):
        return RealizedGraphProjectionRecord.identity_for_authored(
            authored_record=GraphVersionRecord(
                graph_id=graph_id,
                workspace_id=workspace_id,
                version=version,
                graph_descriptor=DEFAULT_GRAPH_CODEC.encode(DeploymentGraph(graph_id)),
                created_by="operator-a",
                created_at=f"2026-08-10T00:00:{version:02d}Z",
            )
        )

    @staticmethod
    def _non_identity(
        graph_id: str,
        workspace_id: str,
        projection_key: str,
    ) -> RealizedGraphProjectionRecord:
        return RealizedGraphProjectionRecord.from_graph(
            projection_id=f"projection-{projection_key}",
            workspace_id=workspace_id,
            source_authored_graph_id=graph_id,
            projection_kind=RealizedGraphProjectionKind.DELEGATION_VERIFIER,
            projection_key=projection_key,
            graph=DeploymentGraph(f"realized-{projection_key}"),
            created_by="operator-a",
            created_at="2026-08-10T00:00:05Z",
        )

    def _make_invalid(self, connection, case: str) -> None:
        if case == "projection-collision":
            expected = self._expected("graph-a", "workspace-a", 1)
            connection.execute(
                "INSERT INTO cpk_realized_graph_projections "
                "(projection_id, workspace_id, source_authored_graph_id, "
                "projection_kind, projection_key, projection_digest, graph_descriptor, "
                "created_by, created_at) VALUES "
                "(%s, 'workspace-a', 'graph-a', 'identity', 'identity', %s, %s, "
                "'operator-a', '2026-08-10T00:00:01Z')",
                (expected.projection_id, "a" * 64, Jsonb(expected.graph_descriptor)),
            )
        elif case == "missing-graph":
            connection.execute(
                "UPDATE cpk_workspaces SET current_graph_id='private-missing' "
                "WHERE workspace_id='workspace-a'"
            )
        elif case == "cross-workspace":
            connection.execute(
                "INSERT INTO cpk_workspaces (workspace_id, name, lifecycle) "
                "VALUES ('workspace-b', 'Workspace B', 'created')"
            )
            self._insert_graph(connection, "graph-c", "workspace-b", 1)
            connection.execute(
                "UPDATE cpk_workspaces SET current_graph_id='graph-c' "
                "WHERE workspace_id='workspace-a'"
            )
        elif case == "plan-session-workspace":
            connection.execute(
                "INSERT INTO cpk_workspaces (workspace_id, name, lifecycle) "
                "VALUES ('workspace-b', 'Workspace B', 'created')"
            )
            self._insert_graph(connection, "graph-c", "workspace-b", 1)
            connection.execute(
                "UPDATE cpk_activity_plans SET base_graph_id='graph-c' "
                "WHERE plan_id='plan-a'"
            )
        elif case == "malformed-graph":
            connection.execute(
                "UPDATE cpk_graph_versions SET graph_descriptor=%s WHERE graph_id='graph-a'",
                (Jsonb({"private": "malformed"}),),
            )
        elif case == "oversized-graph":
            connection.execute(
                "UPDATE cpk_graph_versions SET graph_descriptor=%s WHERE graph_id='graph-a'",
                (Jsonb({"private": "é" * 600_000}),),
            )
        elif case == "negative-workspace-revision":
            connection.execute(
                "UPDATE cpk_workspaces SET desired_graph_revision=-1 "
                "WHERE workspace_id='workspace-a'"
            )
        elif case == "negative-plan-revision":
            connection.execute(
                "UPDATE cpk_activity_plans SET desired_graph_revision=-1 "
                "WHERE plan_id='plan-a'"
            )
        elif case == "missing-plan-session":
            connection.execute(
                "ALTER TABLE cpk_activity_plans DROP CONSTRAINT "
                "cpk_activity_plans_session_id_fkey"
            )
            connection.execute(
                "UPDATE cpk_activity_plans SET session_id='private-missing' "
                "WHERE plan_id='plan-a'"
            )
        elif case == "null-plan-session":
            connection.execute(
                "ALTER TABLE cpk_activity_plans ALTER COLUMN session_id DROP NOT NULL"
            )
            connection.execute(
                "UPDATE cpk_activity_plans SET session_id=NULL WHERE plan_id='plan-a'"
            )
        else:
            raise AssertionError(case)

    @staticmethod
    def _drop_lineage_constraints(connection) -> None:
        rows = connection.execute(
            "SELECT relation.relname, constraints.conname "
            "FROM pg_constraint AS constraints "
            "JOIN pg_class AS relation ON relation.oid=constraints.conrelid "
            "JOIN pg_namespace AS namespace ON namespace.oid=relation.relnamespace "
            "WHERE namespace.nspname=current_schema() AND constraints.conname=ANY(%s)",
            (list(_LINEAGE_CONSTRAINTS),),
        ).fetchall()
        for relation, constraint in rows:
            connection.execute(f"ALTER TABLE {relation} DROP CONSTRAINT {constraint}")

    @staticmethod
    def _constraint_identities(connection):
        rows = connection.execute(
            "SELECT constraints.conname, constraints.oid, "
            "pg_get_constraintdef(constraints.oid, false), constraints.convalidated "
            "FROM pg_constraint AS constraints "
            "JOIN pg_class AS relation ON relation.oid=constraints.conrelid "
            "JOIN pg_namespace AS namespace ON namespace.oid=relation.relnamespace "
            "WHERE namespace.nspname=current_schema() AND constraints.conname=ANY(%s) "
            "ORDER BY constraints.conname",
            (list(_LINEAGE_CONSTRAINTS),),
        ).fetchall()
        return {row[0]: row[1:] for row in rows}

    @staticmethod
    def _all_constraint_names(connection):
        return {
            row[0]
            for row in connection.execute(
                "SELECT constraints.conname FROM pg_constraint AS constraints "
                "JOIN pg_class AS relation ON relation.oid=constraints.conrelid "
                "JOIN pg_namespace AS namespace ON namespace.oid=relation.relnamespace "
                "WHERE namespace.nspname=current_schema()"
            ).fetchall()
        }

    @staticmethod
    def _lineage_columns(connection):
        rows = connection.execute(
            "SELECT table_name, column_name, data_type, is_nullable, column_default "
            "FROM information_schema.columns WHERE table_schema=current_schema() "
            "AND (table_name, column_name) IN ("
            "('cpk_workspaces','current_realized_projection_id'),"
            "('cpk_workspaces','desired_realized_projection_id'),"
            "('cpk_workspaces','desired_graph_revision'),"
            "('cpk_activity_plans','base_realized_projection_id'),"
            "('cpk_activity_plans','desired_realized_projection_id'),"
            "('cpk_activity_plans','desired_graph_revision'))"
        ).fetchall()
        return {(row[0], row[1]): row[2:] for row in rows}

    def _lookalikes(self, connection):
        return tuple(
            connection.execute(
                "SELECT namespace.nspname, relation.relname, constraints.conname, "
                "pg_get_constraintdef(constraints.oid, false) "
                "FROM pg_constraint AS constraints "
                "JOIN pg_class AS relation ON relation.oid=constraints.conrelid "
                "JOIN pg_namespace AS namespace ON namespace.oid=relation.relnamespace "
                "WHERE namespace.nspname=%s "
                "ORDER BY namespace.nspname, relation.relname, constraints.conname",
                (f"{self.schema}_other",),
            ).fetchall()
        )

    @staticmethod
    def _history(connection):
        return tuple(
            connection.execute(
                "SELECT version, name, checksum_sha256, applied_at "
                "FROM cpk_schema_migrations ORDER BY version"
            ).fetchall()
        )

    def _snapshot(self, connection):
        return (
            self._history(connection),
            tuple(
                connection.execute(
                    "SELECT to_jsonb(workspace) FROM cpk_workspaces AS workspace "
                    "ORDER BY workspace_id"
                ).fetchall()
            ),
            tuple(
                connection.execute(
                    "SELECT to_jsonb(graph) FROM cpk_graph_versions AS graph "
                    "ORDER BY graph_id"
                ).fetchall()
            ),
            tuple(
                connection.execute(
                    "SELECT to_jsonb(projection) "
                    "FROM cpk_realized_graph_projections AS projection "
                    "ORDER BY projection_id"
                ).fetchall()
            ),
            tuple(
                connection.execute(
                    "SELECT to_jsonb(plan) FROM cpk_activity_plans AS plan "
                    "ORDER BY plan_id"
                ).fetchall()
            ),
            tuple(sorted(self._constraint_identities(connection).items())),
            tuple(sorted(self._lineage_columns(connection).items())),
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


class _NoSqlConnection:
    def __init__(self) -> None:
        self.calls: list[tuple[object, object]] = []

    def execute(self, query, parameters=()):
        self.calls.append((query, parameters))
        raise AssertionError("incomplete lineage must fail before SQL")


if __name__ == "__main__":
    unittest.main()
