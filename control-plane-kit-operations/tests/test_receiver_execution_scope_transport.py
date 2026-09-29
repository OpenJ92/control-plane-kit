"""#1902 real cursor transport observation with unchanged rows and SQL."""

from dataclasses import replace
import os
import unittest

import psycopg

from control_plane_kit_core.planning import ActivityId, ActivityPlan, NodeTarget, PlannedActivity, StartNode
from control_plane_kit_operations.postgres import PostgresUnitOfWork
from control_plane_kit_operations.records import GraphVersionRecord
from tests.receiver_execution_scope_fixture import ReceiverExecutionScopeFixture


class ValueObserver:
    """Observe frozen scalar-text/bytea lengths; never supply or change rows."""
    def __init__(self, connection):
        self.connection = connection
        self.enabled = False
        self.value_bytes = 0
        self.largest_cell = 0
        self.rows = 0

    def __getattr__(self, name):
        return getattr(self.connection, name)

    def execute(self, query, params=()):
        return ObservedCursor(self, self.connection.execute(query, params))

    def observe(self, row):
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
        return row


class ObservedCursor:
    def __init__(self, observer, cursor):
        self.observer = observer
        self.cursor = cursor

    def __getattr__(self, name):
        return getattr(self.cursor, name)

    def fetchone(self):
        return self.observer.observe(self.cursor.fetchone())

    def fetchall(self):
        return [self.observer.observe(row) for row in self.cursor.fetchall()]

    def fetchmany(self, size=None):
        rows = self.cursor.fetchmany() if size is None else self.cursor.fetchmany(size)
        return [self.observer.observe(row) for row in rows]

    def __iter__(self):
        for row in self.cursor:
            yield self.observer.observe(row)


class ReceiverExecutionScopeTransportTests(ReceiverExecutionScopeFixture, unittest.TestCase):
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

    def measured_read(self):
        module = self.require_scopes()
        observer = ValueObserver(psycopg.connect(os.environ["CPK_OPERATIONS_TEST_DATABASE_URL"]))
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
