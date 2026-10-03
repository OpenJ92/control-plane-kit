"""#1899 request construction and observation over actual PostgreSQL rows.

No substitute context projector, lifecycle classifier, query result, or store.
Missing public API is asserted inside tests so the accepted base can collect.
"""

from importlib import import_module
from importlib.util import find_spec
import json

import psycopg

from control_plane_kit_core.operations import ControlPlaneServiceRole
from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_operations.cpk_server import CpkServerReadService, CpkServerApplicationError
from control_plane_kit_operations.postgres import PostgresUnitOfWork
from tests.draft_catalogue_fixture import CatalogueRequest, principal
from tests.test_receiver_execution_scope_transport import ValueObserver


ROUTE = "read.receiver-authoring-context"
READ_SCOPES = (PolicyScope.INSTANCE_WORKSPACE_READ, PolicyScope.DELEGATION_KEY_READ)


def body_bytes(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                     ensure_ascii=False, allow_nan=False).encode("utf-8")


class ContextObserver(ValueObserver):
    """Pass-through cursor measurement; barriers run only after real data reads."""

    def __init__(self, connection, after_data=None):
        super().__init__(connection, after_row=self.after_row)
        self.enabled = True
        self.after_data = after_data
        self.statements = []
        self.modes = []
        self.data_reads = 0
        self.rollbacks = 0
        self.closes = 0
        self.private_marker_seen = False
        self.observation_failures = []

    def observe(self, row, query):
        try:
            return super().observe(row, query)
        except AssertionError:
            # The public read intentionally sanitizes Exception. Keep this
            # evidence outside that boundary so an expected 409 cannot hide
            # an instrumentation failure or unmetered cursor value.
            self.observation_failures.append("cursor observation failed")
            raise

    def execute(self, query, params=()):
        text = str(query).lower()
        self.statements.append(text)
        if "select" in text and "cpk_" in text:
            self.data_reads += 1
            # Observe actual transaction mode, without changing query or rows.
            self.modes.append((
                self.connection.execute("SHOW transaction_isolation").fetchone()[0],
                self.connection.execute("SHOW transaction_read_only").fetchone()[0]))
        return super().execute(query, params)

    def after_row(self, observer, query, row):
        self.private_marker_seen |= any(isinstance(value, str) and "private-marker" in value for value in row)
        if self.after_data is not None and "cpk_" in str(query).lower():
            callback, self.after_data = self.after_data, None
            callback()

    def rollback(self):
        self.rollbacks += 1
        return self.connection.rollback()

    def close(self):
        self.closes += 1
        return self.connection.close()


class ReceiverAuthoringContextFixture:
    def authoring_api(self):
        module = "control_plane_kit_operations.read_services.receiver_authoring_context"
        self.assertIsNotNone(find_spec(module), "#1899 missing public receiver authoring context read")
        api = import_module(module)
        for name in ("ReceiverAuthoringContextQuery", "ReceiverAuthoringContext",
                     "ReceiverAuthoringContextReadService"):
            self.assertTrue(callable(getattr(api, name, None)), "#1899 missing public " + name)
        return api

    def context_request(self, *, surface="http", workspace="workspace-a", actor=None, **values):
        path = {"workspace_id": workspace}
        if surface == "mcp":
            values = {**path, **values}
            path = {}
        return CatalogueRequest(surface, ROUTE, ControlPlaneServiceRole.READS,
                                path, values, actor or principal(workspace, READ_SCOPES))

    def context_read(self, *, factory=None, **values):
        return CpkServerReadService(factory or self.unit_of_work).handle(self.context_request(**values))

    def direct_context(self, *, factory=None, actor=None, **values):
        api = self.authoring_api()
        return api.ReceiverAuthoringContextReadService(factory or self.unit_of_work).read(
            api.ReceiverAuthoringContextQuery(workspace_id="workspace-a", **values),
            context=(actor or principal(scopes=READ_SCOPES)).command_context("workspace-a"))

    def assert_context_refused(self, status, **values):
        with self.assertRaises(CpkServerApplicationError) as caught:
            self.context_read(**values)
        error = caught.exception
        self.assertEqual(error.status, status)
        self.assertLessEqual(len(body_bytes(error.descriptor())), 512)
        self.assertIsNone(error.__cause__)
        self.assertIsNone(error.__context__)
        for marker in ("SELECT", "INSERT", "cpk_", "private-marker", "submitted-marker"):
            self.assertNotIn(marker, str(error))
        return str(error)

    def measured_factory(self, *, after_data=None):
        observed = []

        def factory():
            options = "-c lock_timeout=3000 -c statement_timeout=10000"
            if hasattr(self, "schema"):
                options += " -c search_path=" + self.schema
            connection = psycopg.connect(self.database_url, options=options)
            self.addCleanup(connection.close)
            observer = ContextObserver(connection, after_data)
            observed.append(observer)
            return PostgresUnitOfWork(lambda: observer)

        return factory, observed

    def assert_measured_snapshot(self, observed):
        self.assertEqual(len(observed), 1, "one context must own exactly one transaction")
        observer = observed[0]
        self.assertEqual(observer.observation_failures, [], "read swallowed a cursor observation failure")
        self.assertTrue(observer.modes)
        self.assertEqual(set(observer.modes), {("repeatable read", "on")})
        self.assertTrue(observer.connection.closed)
        self.assertEqual(observer.closes, 1)
        self.assertLessEqual(observer.data_reads, 1024)
        self.assertLessEqual(observer.rows, 4096)
        self.assertLessEqual(observer.value_bytes, 8_388_608)
        self.assertLessEqual(observer.largest_cell, 1_048_576)
        statements = " ".join(observer.statements)
        self.assertNotIn("pg_advisory", statements)
        self.assertNotIn("for update", statements)
        self.assertNotIn("cpk_delegation_signing_keys", statements)
        # Recorded first acceptance is read as graph attribution, not re-proved
        # by traversing the execution machine or consulting provider health.
        for table in ("cpk_activity_runs", "cpk_effect_attempt", "cpk_execution_receiver_scopes"):
            self.assertNotIn(table, statements)
