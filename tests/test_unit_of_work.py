import os
import unittest

import psycopg

from control_plane_kit.graph import DeploymentGraph
from control_plane_kit.stores import (
    ActivityPlanRecord,
    GraphVersionRecord,
    OperationActionRecord,
    OperationSessionRecord,
    PostgresUnitOfWork,
    UnitOfWorkStateError,
    WorkspaceRecord,
)
from tests.postgres_case import PostgresStoreTestCase


class FakeConnection:
    def __init__(self) -> None:
        self.commits = 0
        self.rollbacks = 0
        self.closes = 0

    def execute(self, query, params=()):
        raise AssertionError("unit-of-work lifecycle tests do not execute SQL")

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1

    def close(self) -> None:
        self.closes += 1


class PostgresUnitOfWorkTests(unittest.TestCase):
    def test_explicit_commit_commits_once_and_closes(self):
        connection = FakeConnection()

        with PostgresUnitOfWork(lambda: connection) as unit_of_work:
            stores = unit_of_work.stores
            self.assertIs(stores.workspace._connection, connection)
            self.assertIs(stores.graph_topology._connection, connection)
            self.assertIs(stores.activity_history._connection, connection)
            unit_of_work.commit()

        self.assertEqual(connection.commits, 1)
        self.assertEqual(connection.rollbacks, 0)
        self.assertEqual(connection.closes, 1)

    def test_uncommitted_exit_rolls_back_and_closes(self):
        connection = FakeConnection()

        with PostgresUnitOfWork(lambda: connection):
            pass

        self.assertEqual(connection.commits, 0)
        self.assertEqual(connection.rollbacks, 1)
        self.assertEqual(connection.closes, 1)

    def test_exceptional_exit_rolls_back_and_preserves_exception(self):
        connection = FakeConnection()

        with self.assertRaisesRegex(ValueError, "late command failure"):
            with PostgresUnitOfWork(lambda: connection):
                raise ValueError("late command failure")

        self.assertEqual(connection.commits, 0)
        self.assertEqual(connection.rollbacks, 1)
        self.assertEqual(connection.closes, 1)

    def test_stores_and_lifecycle_methods_require_active_unfinished_unit(self):
        connection = FakeConnection()
        unit_of_work = PostgresUnitOfWork(lambda: connection)

        with self.assertRaises(UnitOfWorkStateError):
            _ = unit_of_work.stores

        with unit_of_work:
            unit_of_work.commit()
            with self.assertRaises(UnitOfWorkStateError):
                _ = unit_of_work.stores
            with self.assertRaises(UnitOfWorkStateError):
                unit_of_work.commit()

        with self.assertRaises(UnitOfWorkStateError):
            unit_of_work.rollback()


class PostgresUnitOfWorkIntegrationTests(PostgresStoreTestCase):
    def unit_of_work(self) -> PostgresUnitOfWork:
        database_url = os.environ["CPK_TEST_DATABASE_URL"]
        return PostgresUnitOfWork(lambda: psycopg.connect(database_url))

    def test_commit_publishes_all_participating_store_writes_together(self):
        with self.unit_of_work() as unit_of_work:
            stores = unit_of_work.stores
            stores.workspace.create(WorkspaceRecord(workspace_id="workspace-a", name="Demo"))
            stores.graph_topology.save(self.graph_record())
            stores.activity_history.add_session(self.session_record())
            stores.activity_history.add_action(self.action_record())
            stores.activity_history.add_plan(self.plan_record())

            self.assertEqual(self.row_count("cpk_workspaces"), 0)
            self.assertEqual(self.row_count("cpk_graph_versions"), 0)
            self.assertEqual(self.row_count("cpk_operation_sessions"), 0)
            unit_of_work.commit()

        self.assertEqual(self.row_count("cpk_workspaces"), 1)
        self.assertEqual(self.row_count("cpk_graph_versions"), 1)
        self.assertEqual(self.row_count("cpk_operation_sessions"), 1)
        self.assertEqual(self.row_count("cpk_operation_actions"), 1)
        self.assertEqual(self.row_count("cpk_activity_plans"), 1)

    def test_late_store_failure_rolls_back_every_earlier_write(self):
        with self.assertRaises(psycopg.errors.UniqueViolation):
            with self.unit_of_work() as unit_of_work:
                stores = unit_of_work.stores
                stores.workspace.create(WorkspaceRecord(workspace_id="workspace-a", name="Demo"))
                stores.graph_topology.save(self.graph_record())
                stores.activity_history.add_session(self.session_record())
                stores.activity_history.add_action(self.action_record())
                stores.activity_history.add_plan(self.plan_record())
                stores.activity_history.add_plan(self.plan_record())

        for table in (
            "cpk_workspaces",
            "cpk_graph_versions",
            "cpk_operation_sessions",
            "cpk_operation_actions",
            "cpk_activity_plans",
        ):
            self.assertEqual(self.row_count(table), 0, table)

    def row_count(self, table: str) -> int:
        allowed = {
            "cpk_workspaces",
            "cpk_graph_versions",
            "cpk_operation_sessions",
            "cpk_operation_actions",
            "cpk_activity_plans",
        }
        if table not in allowed:
            raise ValueError(f"unexpected test table {table!r}")
        return self.connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]

    @staticmethod
    def graph_record() -> GraphVersionRecord:
        return GraphVersionRecord.from_graph(
            graph_id="graph-a",
            workspace_id="workspace-a",
            version=1,
            graph=DeploymentGraph(name="desired"),
            created_by="jacob",
            created_at="2026-07-15T00:00:00Z",
        )

    @staticmethod
    def session_record() -> OperationSessionRecord:
        return OperationSessionRecord(
            session_id="session-a",
            workspace_id="workspace-a",
            actor_id="jacob",
            title="Swap API",
            status="open",
            created_at="2026-07-15T00:00:01Z",
        )

    @staticmethod
    def action_record() -> OperationActionRecord:
        return OperationActionRecord(
            action_id="action-a",
            session_id="session-a",
            ordinal=1,
            action_type="set_desired_graph",
            actor_id="jacob",
            created_at="2026-07-15T00:00:02Z",
        )

    @staticmethod
    def plan_record() -> ActivityPlanRecord:
        return ActivityPlanRecord(
            plan_id="plan-a",
            session_id="session-a",
            base_graph_id="graph-a",
            desired_graph_id="graph-a",
            status="planned",
            created_at="2026-07-15T00:00:03Z",
        )


if __name__ == "__main__":
    unittest.main()
