import unittest

from control_plane_kit.stores import PostgresUnitOfWork, UnitOfWorkStateError


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


if __name__ == "__main__":
    unittest.main()
