"""#1902 real cursor transport observation with unchanged rows and SQL."""

from dataclasses import replace
import os
import unittest

import psycopg
from psycopg.types.json import Jsonb

from control_plane_kit_core.planning import ActivityId, ActivityPlan, NodeTarget, PlannedActivity, StartNode
from control_plane_kit_operations.postgres import PostgresUnitOfWork
from control_plane_kit_operations.records import GraphVersionRecord
from tests.receiver_execution_scope_fixture import ReceiverExecutionScopeFixture


class ValueObserver:
    """Observe frozen scalar-text/bytea lengths; never supply or change rows."""
    def __init__(self, connection, after_row=None):
        self.connection = connection
        self.enabled = False
        self.value_bytes = 0
        self.largest_cell = 0
        self.rows = 0
        self.after_row = after_row

    def __getattr__(self, name):
        return getattr(self.connection, name)

    def execute(self, query, params=()):
        return ObservedCursor(self, self.connection.execute(query, params), query)

    def observe(self, row, query):
        if row is None or not self.enabled:
            return row
        self.rows += 1
        for value in row:
            if value is None:
                length = 0
            elif isinstance(value, str):
                length = len(value.encode("utf-8"))
            elif isinstance(value, (bytes, memoryview)):
                length = len(value)
            elif type(value) is bool:
                length = 1  # PostgreSQL scalar t/f, not Python repr.
            elif type(value) is int:
                length = len(str(value).encode("ascii"))
            else:
                raise AssertionError("bounded reader must project explicit text/bytea or fixed probe scalars")
            self.value_bytes += length
            self.largest_cell = max(self.largest_cell, length)
        if self.after_row is not None:
            self.after_row(self, query, row)
        return row


class ObservedCursor:
    def __init__(self, observer, cursor, query):
        self.observer = observer
        self.cursor = cursor
        self.query = query

    def __getattr__(self, name):
        return getattr(self.cursor, name)

    def fetchone(self):
        return self.observer.observe(self.cursor.fetchone(), self.query)

    def fetchall(self):
        return [self.observer.observe(row, self.query) for row in self.cursor.fetchall()]

    def fetchmany(self, size=None):
        rows = self.cursor.fetchmany() if size is None else self.cursor.fetchmany(size)
        return [self.observer.observe(row, self.query) for row in rows]

    def __iter__(self):
        for row in self.cursor:
            yield self.observer.observe(row, self.query)


class ReceiverExecutionScopeTransportTests(ReceiverExecutionScopeFixture, unittest.TestCase):
    def test_growth_after_length_probe_returns_bounded_unavailable(self):
        self.require_scopes()
        self.admit()
        injected = []

        def grow_original(observer, query, row):
            text = str(query).lower()
            if (not injected and "cpk_activity_plans" in text and "octet_length" in text
                    and row and all(value is None or type(value) is int for value in row)):
                # Fault injection on the SAME real transaction after its size
                # observation, before guarded retrieval. It changes DB truth,
                # never the returned row, decoder, limit or reservation.
                observer.connection.execute(
                    "UPDATE cpk_activity_plans SET payload=jsonb_build_object('growth',repeat('x',1048577)) WHERE plan_id='plan-a'")
                injected.append(True)

        result, observer = self.measured_read(after_row=grow_original)
        self.assertEqual(injected, [True], "the required original-plan length probe was not observed")
        self.assertEqual(result.disposition, "unavailable")
        self.assertLessEqual(observer.largest_cell, 1024 * 1024)
        # The owning UoW rolled the injected corruption back.
        self.assertEqual(self.measured_read()[0].disposition, "conflict")

    def test_cancel_action_payload_at_64k_fits_and_one_over_is_guarded_before_transport(self):
        self.require_scopes()
        self.admit()
        cancelled = self.cancel(self.claim())
        original = dict(cancelled.action.payload)
        # OperationActionRecord permits additional retained payload fields;
        # the exact cancellation identity/time fields remain unchanged. This
        # is a read-boundary fixture, not a fabricated cancellation receipt.
        base_size = self.connection.execute(
            "SELECT octet_length((payload || jsonb_build_object('capacity_padding',''))::text) FROM cpk_operation_actions WHERE action_id=%s",
            (cancelled.action.action_id,)).fetchone()[0]
        for excess in (0, 1):
            value = {**original, "capacity_padding": "x" * (65536 - base_size + excess)}
            self.connection.execute("UPDATE cpk_operation_actions SET payload=%s WHERE action_id=%s",
                                    (Jsonb(value), cancelled.action.action_id))
            try:
                self.assertEqual(self.connection.execute("SELECT octet_length(payload::text) FROM cpk_operation_actions WHERE action_id=%s",
                    (cancelled.action.action_id,)).fetchone()[0], 65536 + excess)
                result, observer = self.measured_read()
                self.assertEqual(result.disposition, "unavailable" if excess else "requires-fresh-gate-closure")
                self.assertLessEqual(observer.largest_cell, 65536)
            finally:
                self.connection.execute("UPDATE cpk_operation_actions SET payload=%s WHERE action_id=%s",
                                        (Jsonb(original), cancelled.action.action_id))

    def test_oversized_original_plan_or_graph_never_reaches_python_as_a_full_cell(self):
        self.require_scopes()
        self.admit()
        _, _, _, desired = self.source()
        originals = (
            ("cpk_activity_plans", "payload", "plan_id", "plan-a"),
            ("cpk_realized_graph_projections", "graph_descriptor", "projection_id", desired.projection_id),
        )
        for table, column, identity, value in originals:
            select = psycopg.sql.SQL("SELECT {} FROM {} WHERE {}=%s").format(
                psycopg.sql.Identifier(column), psycopg.sql.Identifier(table), psycopg.sql.Identifier(identity))
            update = psycopg.sql.SQL("UPDATE {} SET {}=%s WHERE {}=%s").format(
                psycopg.sql.Identifier(table), psycopg.sql.Identifier(column), psycopg.sql.Identifier(identity))
            original = self.connection.execute(select, (value,)).fetchone()[0]
            with self.subTest(table=table):
                self.assertEqual(self.measured_read()[0].disposition, "conflict")
                self.connection.execute(update, (Jsonb({"oversized": "x" * (1024 * 1024 + 1)}), value))
                try:
                    result, observer = self.measured_read()
                    self.assertEqual(result.disposition, "unavailable")
                    self.assertLessEqual(observer.largest_cell, 1024 * 1024)
                finally:
                    self.connection.execute(update, (Jsonb(original), value))

    def seed_large_original(self, number):
        graph = self.graph()
        node = graph.node("app")
        # Distinct original identities defeat legitimate immutable-source
        # caching across candidates; each individually legal document is large.
        graph = replace(graph, nodes={"app": replace(node, metadata={"capacity_padding": "x" * 940_000})})
        graph_id = f"large-graph-{number}"
        with self.unit_of_work() as uow:
            uow.stores.graphs.save(GraphVersionRecord.from_graph(
                graph_id=graph_id, workspace_id="workspace-a",
                version=uow.stores.graphs.next_version_for_workspace("workspace-a"), graph=graph,
                created_by="operator-a", created_at="2026-07-22T12:00:30Z"))
            uow.stores.workspaces.set_desired_graph("workspace-a", graph_id)
            uow.commit()
        plan = ActivityPlan((PlannedActivity(ActivityId("start"), StartNode(NodeTarget("app"))),))
        self.seed_plan_truth(plan_id=f"large-plan-{number}", approval_request_id=f"large-approval-{number}",
            approval_decision_id=f"large-decision-{number}", plan=plan, desired_graph_id=graph_id)
        self.admission_service(f"large-request-{number}", f"large-action-{number}").execute(
            self.command(plan_id=f"large-plan-{number}", approval_request_id=f"large-approval-{number}", key=f"large-{number}"))

    def measured_read(self, *, after_row=None):
        module = self.require_scopes()
        observer = ValueObserver(psycopg.connect(os.environ["CPK_OPERATIONS_TEST_DATABASE_URL"]), after_row=after_row)
        with PostgresUnitOfWork(lambda: observer) as uow:
            guard = uow.stores.graphs.lock_receiver_lifecycle("workspace-a")
            observer.enabled = True
            evidence = uow.stores.execution.receiver_scope_evidence("workspace-a",
                (module.ExecutionReceiverScope("docker", "app"),), guard)
            observer.enabled = False
            result = module.classify_receiver_scope_evidence(evidence)
        self.assertGreater(observer.rows, 0)
        self.assertLessEqual(observer.value_bytes, 16 * 1024 * 1024)
        self.assertLessEqual(observer.largest_cell, 1024 * 1024)
        return result, observer

    def test_distinct_near_cap_originals_refuse_before_aggregate_transport_overflow(self):
        self.require_scopes()
        for number in range(3):
            self.seed_large_original(number)
        result, observer = self.measured_read()
        self.assertEqual(result.disposition, "conflict")
        self.assertEqual(len(result.request_ids), 3)
        self.assertGreater(observer.value_bytes, 3 * 940_000)
        for number in range(3, 20):
            self.seed_large_original(number)
        # Even one fetch of each DISTINCT necessary original descriptor exceeds
        # 16 MiB, with just20 requests,20 scopes and zero runs/effects/events.
        self.assertGreater(20 * 940_000, 16 * 1024 * 1024)
        result, _ = self.measured_read()
        self.assertEqual(result.disposition, "capacity")
