"""Postgres-backed test fixture for store and workflow tests."""

from __future__ import annotations

import os
import unittest

import psycopg

from control_plane_kit.stores import PostgresStoreBundle, install_schema


TRUNCATE_SCHEMA = """
TRUNCATE TABLE
  cpk_activity_events,
  cpk_activity_runs,
  cpk_approval_decisions,
  cpk_approval_requests,
  cpk_activity_plans,
  cpk_operation_actions,
  cpk_operation_sessions,
  cpk_graph_versions,
  cpk_workspaces,
  cpk_observations,
  cpk_instances,
  cpk_secret_references
CASCADE;
"""


class PostgresStoreTestCase(unittest.TestCase):
    """Base class for tests that exercise the durable Postgres stores."""

    connection: psycopg.Connection
    stores: PostgresStoreBundle

    @classmethod
    def setUpClass(cls) -> None:
        database_url = os.environ.get("CPK_TEST_DATABASE_URL")
        if not database_url:
            raise RuntimeError(
                "CPK_TEST_DATABASE_URL is required. Run ./test.sh so Docker starts Postgres."
            )
        cls.connection = psycopg.connect(database_url, autocommit=True)
        install_schema(cls.connection)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.connection.close()

    def setUp(self) -> None:
        self.connection.execute(TRUNCATE_SCHEMA)
        self.stores = PostgresStoreBundle(self.connection)
