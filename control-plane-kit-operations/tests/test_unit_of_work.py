from __future__ import annotations

import os
import unittest

import psycopg

from control_plane_kit_operations.postgres import install_schema
from control_plane_kit_operations.postgres.unit_of_work import (
    PostgresUnitOfWork,
    UnitOfWorkStateError,
)


class FakeConnection:
    def __init__(self, *, commit_error: BaseException | None = None) -> None:
        self.commits = 0
        self.rollbacks = 0
        self.closes = 0
        self.commit_error = commit_error

    def execute(self, query: str, params: tuple[object, ...] = ()) -> object:
        raise AssertionError("unit-of-work lifecycle tests do not execute SQL")

    def commit(self) -> None:
        self.commits += 1
        if self.commit_error is not None:
            raise self.commit_error

    def rollback(self) -> None:
        self.rollbacks += 1

    def close(self) -> None:
        self.closes += 1


class PostgresUnitOfWorkTests(unittest.TestCase):
    def test_explicit_commit_commits_once_and_closes(self) -> None:
        connection = FakeConnection()

        with PostgresUnitOfWork(lambda: connection) as unit_of_work:
            stores = unit_of_work.stores
            self.assertIs(stores.connection, connection)
            self.assertFalse(hasattr(stores, "commit"))
            self.assertFalse(hasattr(stores, "rollback"))
            unit_of_work.commit()
            self.assertEqual(connection.commits, 0)
            self.assertIs(unit_of_work.stores, stores)

        self.assertEqual(connection.commits, 1)
        self.assertEqual(connection.rollbacks, 0)
        self.assertEqual(connection.closes, 1)

    def test_uncommitted_and_exceptional_exits_roll_back_and_close(self) -> None:
        uncommitted = FakeConnection()
        with PostgresUnitOfWork(lambda: uncommitted):
            pass
        self.assertEqual(uncommitted.commits, 0)
        self.assertEqual(uncommitted.rollbacks, 1)
        self.assertEqual(uncommitted.closes, 1)

        exceptional = FakeConnection()
        with self.assertRaisesRegex(ValueError, "late command failure"):
            with PostgresUnitOfWork(lambda: exceptional):
                raise ValueError("late command failure")
        self.assertEqual(exceptional.commits, 0)
        self.assertEqual(exceptional.rollbacks, 1)
        self.assertEqual(exceptional.closes, 1)

    def test_exception_after_commit_request_still_rolls_back(self) -> None:
        connection = FakeConnection()

        with self.assertRaisesRegex(ValueError, "failure after commit request"):
            with PostgresUnitOfWork(lambda: connection) as unit_of_work:
                unit_of_work.commit()
                raise ValueError("failure after commit request")

        self.assertEqual(connection.commits, 0)
        self.assertEqual(connection.rollbacks, 1)
        self.assertEqual(connection.closes, 1)

    def test_physical_commit_failure_rolls_back_closes_and_propagates(self) -> None:
        connection = FakeConnection(commit_error=OSError("commit failed"))

        with self.assertRaisesRegex(OSError, "commit failed"):
            with PostgresUnitOfWork(lambda: connection) as unit_of_work:
                unit_of_work.commit()

        self.assertEqual(connection.commits, 1)
        self.assertEqual(connection.rollbacks, 1)
        self.assertEqual(connection.closes, 1)

    def test_stores_and_lifecycle_methods_require_active_unfinished_unit(self) -> None:
        connection = FakeConnection()
        unit_of_work = PostgresUnitOfWork(lambda: connection)

        with self.assertRaises(UnitOfWorkStateError):
            _ = unit_of_work.stores

        with unit_of_work:
            unit_of_work.commit()
            _ = unit_of_work.stores
            with self.assertRaises(UnitOfWorkStateError):
                unit_of_work.commit()

        with self.assertRaises(UnitOfWorkStateError):
            unit_of_work.rollback()


class PostgresUnitOfWorkIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        database_url = os.environ.get("CPK_OPERATIONS_TEST_DATABASE_URL")
        if not database_url:
            raise RuntimeError(
                "CPK_OPERATIONS_TEST_DATABASE_URL is required. Run "
                "./control-plane-kit-operations/test.sh so Docker starts Postgres."
            )
        self.connection = psycopg.connect(database_url, autocommit=True)
        install_schema(self.connection)
        self.connection.execute("TRUNCATE TABLE cpk_workspaces CASCADE")

    def tearDown(self) -> None:
        self.connection.close()

    def unit_of_work(self) -> PostgresUnitOfWork:
        database_url = os.environ["CPK_OPERATIONS_TEST_DATABASE_URL"]
        return PostgresUnitOfWork(lambda: psycopg.connect(database_url))

    def test_commit_publishes_all_shared_connection_writes_together(self) -> None:
        with self.unit_of_work() as unit_of_work:
            unit_of_work.stores.connection.execute(
                """
                INSERT INTO cpk_workspaces (workspace_id, name, lifecycle)
                VALUES ('workspace-a', 'Demo', 'created')
                """
            )
            self.assertEqual(self._row_count("cpk_workspaces"), 0)
            unit_of_work.commit()
            self.assertEqual(self._row_count("cpk_workspaces"), 0)

        self.assertEqual(self._row_count("cpk_workspaces"), 1)

    def test_late_write_failure_rolls_back_every_earlier_write(self) -> None:
        with self.assertRaises(psycopg.errors.UniqueViolation):
            with self.unit_of_work() as unit_of_work:
                unit_of_work.stores.connection.execute(
                    """
                    INSERT INTO cpk_workspaces (workspace_id, name, lifecycle)
                    VALUES ('workspace-a', 'Demo', 'created')
                    """
                )
                unit_of_work.stores.connection.execute(
                    """
                    INSERT INTO cpk_workspaces (workspace_id, name, lifecycle)
                    VALUES ('workspace-a', 'Duplicate', 'created')
                    """
                )

        self.assertEqual(self._row_count("cpk_workspaces"), 0)

    def test_exception_after_commit_request_rolls_back_real_postgres_writes(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "late application failure"):
            with self.unit_of_work() as unit_of_work:
                unit_of_work.stores.connection.execute(
                    """
                    INSERT INTO cpk_workspaces (workspace_id, name, lifecycle)
                    VALUES ('workspace-a', 'Demo', 'created')
                    """
                )
                unit_of_work.commit()
                raise RuntimeError("late application failure")

        self.assertEqual(self._row_count("cpk_workspaces"), 0)

    def _row_count(self, table: str) -> int:
        if table != "cpk_workspaces":
            raise ValueError(f"unexpected test table {table!r}")
        return self.connection.execute("SELECT count(*) FROM cpk_workspaces").fetchone()[0]


if __name__ == "__main__":
    unittest.main()
