from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from types import SimpleNamespace
import unittest

import psycopg

from tests.graph_lineage_fixture import seed_identity_graphs

from control_plane_kit_core.planning import ActivityPlan, DEFAULT_ACTIVITY_PLAN_CODEC
from control_plane_kit_operations.postgres import PostgresStoreBundle, install_schema

from control_plane_kit_operations.postgres.activity_history import (
    PostgresActivityHistoryStore,
)
from control_plane_kit_operations.postgres.execution import PostgresExecutionStore
from control_plane_kit_operations.read_pages import (
    PlanReadScope,
    ReadCollection,
    ReadPageRequest,
    SessionReadScope,
    TemporalReadCursor,
    WorkspaceReadScope,
)


_INSTANT = "2026-08-12T12:00:00.000000Z"


class _Rows:
    def fetchall(self) -> tuple[tuple[object, ...], ...]:
        return ()


class _RecordingConnection:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[object, ...]]] = []

    def execute(self, query: str, parameters: tuple[object, ...]) -> _Rows:
        self.calls.append((query, parameters))
        return _Rows()


def _request(
    collection: ReadCollection,
    scope: WorkspaceReadScope | SessionReadScope | PlanReadScope,
    *,
    after_id: str | None = None,
) -> ReadPageRequest:
    cursor = (
        None
        if after_id is None
        else TemporalReadCursor(collection, scope, _INSTANT, after_id)
    )
    return ReadPageRequest(collection, scope, 3, cursor)


class TemporalPageSqlShapeTests(unittest.TestCase):
    def test_activity_and_open_session_pages_are_sql_bounded(self) -> None:
        cases = (
            (ReadCollection.ACTIVITY_SESSIONS, False),
            (ReadCollection.OPEN_SESSIONS, True),
        )
        for collection, open_only in cases:
            with self.subTest(collection=collection.value):
                connection = _RecordingConnection()
                scope = WorkspaceReadScope("workspace-a")

                PostgresActivityHistoryStore(connection).session_page(
                    _request(collection, scope, after_id="session-a")
                )

                query, parameters = connection.calls[0]
                normalized = " ".join(query.split())
                self.assertIn("(created_at, session_id) > (%s, %s)", normalized)
                self.assertIn("ORDER BY created_at ASC, session_id ASC", normalized)
                self.assertIn("LIMIT %s", normalized)
                self.assertEqual("status = 'open'" in normalized, open_only)
                self.assertEqual(
                    parameters,
                    ("workspace-a", datetime(2026, 8, 12, 12, tzinfo=timezone.utc), "session-a", 4),
                )

    def test_plan_page_is_parent_bounded_by_native_time_and_identity(self) -> None:
        connection = _RecordingConnection()
        scope = SessionReadScope("workspace-a", "session-a")

        PostgresActivityHistoryStore(connection).plan_page(
            _request(ReadCollection.SESSION_PLANS, scope, after_id="plan-a")
        )

        query, parameters = connection.calls[0]
        normalized = " ".join(query.split())
        self.assertIn("session_id = %s", normalized)
        self.assertIn("(created_at, plan_id) > (%s, %s)", normalized)
        self.assertIn("ORDER BY created_at ASC, plan_id ASC", normalized)
        self.assertEqual(
            parameters,
            ("session-a", datetime(2026, 8, 12, 12, tzinfo=timezone.utc), "plan-a", 4),
        )

    def test_session_approval_page_joins_decision_in_one_bounded_query(self) -> None:
        connection = _RecordingConnection()
        scope = SessionReadScope("workspace-a", "session-a")

        PostgresActivityHistoryStore(connection).approval_page(
            _request(ReadCollection.SESSION_APPROVALS, scope, after_id="approval-a")
        )

        self.assertEqual(len(connection.calls), 1)
        query, parameters = connection.calls[0]
        normalized = " ".join(query.split())
        self.assertIn("LEFT JOIN cpk_approval_decisions", normalized)
        self.assertIn("(request.requested_at, request.request_id) > (%s, %s)", normalized)
        self.assertIn("ORDER BY request.requested_at ASC, request.request_id ASC", normalized)
        self.assertEqual(
            parameters,
            ("session-a", datetime(2026, 8, 12, 12, tzinfo=timezone.utc), "approval-a", 4),
        )

    def test_pending_approval_page_is_one_workspace_anti_join(self) -> None:
        connection = _RecordingConnection()
        scope = WorkspaceReadScope("workspace-a")

        PostgresActivityHistoryStore(connection).pending_approval_page(
            _request(ReadCollection.PENDING_APPROVALS, scope, after_id="approval-a")
        )

        self.assertEqual(len(connection.calls), 1)
        query, parameters = connection.calls[0]
        normalized = " ".join(query.split())
        self.assertIn("JOIN cpk_operation_sessions", normalized)
        self.assertIn("LEFT JOIN cpk_approval_decisions", normalized)
        self.assertIn("decision.request_id IS NULL", normalized)
        self.assertIn("session.workspace_id = %s", normalized)
        self.assertIn("LIMIT %s", normalized)
        self.assertEqual(
            parameters,
            ("workspace-a", datetime(2026, 8, 12, 12, tzinfo=timezone.utc), "approval-a", 4),
        )

    def test_plan_run_page_proves_request_workspace_and_parent_in_one_query(self) -> None:
        connection = _RecordingConnection()
        scope = PlanReadScope("workspace-a", "plan-a")

        PostgresExecutionStore(connection).run_page(
            _request(ReadCollection.PLAN_RUNS, scope, after_id="run-a")
        )

        self.assertEqual(len(connection.calls), 1)
        query, parameters = connection.calls[0]
        normalized = " ".join(query.split())
        self.assertIn("JOIN cpk_execution_requests", normalized)
        self.assertIn("JOIN cpk_activity_plans", normalized)
        self.assertIn("JOIN cpk_operation_sessions", normalized)
        self.assertIn("request.workspace_id = %s", normalized)
        self.assertIn("run.plan_id = %s", normalized)
        self.assertIn("(run.created_at, run.run_id) > (%s, %s)", normalized)
        self.assertEqual(
            parameters,
            (
                "workspace-a",
                "plan-a",
                datetime(2026, 8, 12, 12, tzinfo=timezone.utc),
                "run-a",
                4,
            ),
        )


class ThinDetailProtocolTests(unittest.TestCase):
    def test_detail_methods_have_no_page_limit_parameter(self) -> None:
        from inspect import signature

        from control_plane_kit_operations.read_services import InstanceReadService

        for name in ("session_detail", "plan_detail", "approval_detail"):
            with self.subTest(name=name):
                self.assertNotIn("limit", signature(getattr(InstanceReadService, name)).parameters)

    def test_detail_projection_helpers_do_not_require_collection_stores(self) -> None:
        from control_plane_kit_operations import InstanceReadService

        session = SimpleNamespace(
            session_id="session-a",
            workspace_id="workspace-a",
            actor_id="operator-a",
            title="Deploy",
            status=SimpleNamespace(value="open"),
            created_at="2026-08-12T12:00:00Z",
            closed_at=None,
            metadata={},
        )

        service = InstanceReadService(
            workspace_store=SimpleNamespace(
                get=lambda workspace_id: SimpleNamespace(workspace_id=workspace_id)
            ),
            graph_topology_store=SimpleNamespace(),
            activity_history_store=SimpleNamespace(
                get_session=lambda session_id: session
            ),
        )
        descriptor = service.session_detail(
            "workspace-a",
            "session-a",
        ).descriptor()["session"]

        self.assertNotIn("actions", descriptor)
        self.assertNotIn("plans", descriptor)
        self.assertNotIn("approvals", descriptor)
        self.assertNotIn("runs", descriptor)
        self.assertNotIn("events", descriptor)


class FilteredTemporalMembershipTests(unittest.TestCase):
    def setUp(self) -> None:
        database_url = os.environ.get("CPK_OPERATIONS_TEST_DATABASE_URL")
        if not database_url:
            raise RuntimeError("CPK_OPERATIONS_TEST_DATABASE_URL is required")
        self.database_url = database_url
        self.connection = psycopg.connect(database_url, autocommit=True)
        install_schema(self.connection)
        self.connection.execute("TRUNCATE TABLE cpk_workspaces CASCADE")
        self._seed_truth()

    def tearDown(self) -> None:
        self.connection.close()

    def test_open_session_closure_changes_later_statement_membership_without_duplicates(self) -> None:
        request = ReadPageRequest(
            ReadCollection.OPEN_SESSIONS,
            WorkspaceReadScope("workspace-a"),
            1,
        )
        with psycopg.connect(self.database_url) as reader:
            first = PostgresActivityHistoryStore(reader).session_page(request)

        with psycopg.connect(self.database_url) as writer:
            writer.execute(
                "UPDATE cpk_operation_sessions SET status = 'closed', "
                "closed_at = '2026-08-12T12:05:00Z' WHERE session_id = 'session-b'"
            )

        with psycopg.connect(self.database_url) as reader:
            second = PostgresActivityHistoryStore(reader).session_page(
                ReadPageRequest(
                    ReadCollection.OPEN_SESSIONS,
                    WorkspaceReadScope("workspace-a"),
                    10,
                    first.next_cursor,
                )
            )
            fresh = PostgresActivityHistoryStore(reader).session_page(
                ReadPageRequest(
                    ReadCollection.OPEN_SESSIONS,
                    WorkspaceReadScope("workspace-a"),
                    10,
                )
            )

        self.assertEqual([item.session_id for item in first.items], ["session-a"])
        self.assertEqual([item.session_id for item in second.items], ["session-c"])
        self.assertEqual([item.session_id for item in fresh.items], ["session-a", "session-c"])

    def test_committed_inserts_on_both_sides_obey_strict_live_keyset_semantics(self) -> None:
        scope = WorkspaceReadScope("workspace-a")
        with psycopg.connect(self.database_url) as reader:
            first = PostgresActivityHistoryStore(reader).session_page(
                ReadPageRequest(ReadCollection.ACTIVITY_SESSIONS, scope, 1)
            )

        with psycopg.connect(self.database_url) as writer:
            writer.execute(
                """
                INSERT INTO cpk_operation_sessions
                  (session_id, workspace_id, actor_id, title, status, created_at)
                VALUES
                  ('session-0', 'workspace-a', 'operator-a', 'Before', 'open',
                   '2026-08-12T12:00:00Z'),
                  ('session-ab', 'workspace-a', 'operator-a', 'After', 'open',
                   '2026-08-12T12:00:00Z')
                """
            )

        with psycopg.connect(self.database_url) as reader:
            store = PostgresActivityHistoryStore(reader)
            second = store.session_page(
                ReadPageRequest(
                    ReadCollection.ACTIVITY_SESSIONS,
                    scope,
                    10,
                    first.next_cursor,
                )
            )
            fresh = store.session_page(
                ReadPageRequest(ReadCollection.ACTIVITY_SESSIONS, scope, 10)
            )

        self.assertEqual([item.session_id for item in first.items], ["session-a"])
        self.assertEqual(
            [item.session_id for item in second.items],
            ["session-ab", "session-b", "session-c"],
        )
        self.assertEqual(
            [item.session_id for item in fresh.items],
            ["session-0", "session-a", "session-ab", "session-b", "session-c"],
        )

    def test_pending_decision_changes_later_statement_membership_without_duplicates(self) -> None:
        request = ReadPageRequest(
            ReadCollection.PENDING_APPROVALS,
            WorkspaceReadScope("workspace-a"),
            1,
        )
        with psycopg.connect(self.database_url) as reader:
            first = PostgresActivityHistoryStore(reader).pending_approval_page(request)

        with psycopg.connect(self.database_url) as writer:
            writer.execute(
                """
                INSERT INTO cpk_approval_decisions
                  (decision_id, request_id, actor_id, decision, scope, decided_at)
                VALUES ('decision-b', 'approval-b', 'manager-a', 'approved',
                        'plan:approve', '2026-08-12T12:06:00Z')
                """
            )

        with psycopg.connect(self.database_url) as reader:
            store = PostgresActivityHistoryStore(reader)
            second = store.pending_approval_page(
                ReadPageRequest(
                    ReadCollection.PENDING_APPROVALS,
                    WorkspaceReadScope("workspace-a"),
                    10,
                    first.next_cursor,
                )
            )
            fresh = store.pending_approval_page(
                ReadPageRequest(
                    ReadCollection.PENDING_APPROVALS,
                    WorkspaceReadScope("workspace-a"),
                    10,
                )
            )

        self.assertEqual([item.request.request_id for item in first.items], ["approval-a"])
        self.assertEqual([item.request.request_id for item in second.items], ["approval-c"])
        self.assertEqual(
            [item.request.request_id for item in fresh.items],
            ["approval-a", "approval-c"],
        )

    def _seed_truth(self) -> None:
        self.connection.execute(
            """
            INSERT INTO cpk_workspaces (workspace_id, name, lifecycle)
            VALUES ('workspace-a', 'Workspace A', 'created');
            INSERT INTO cpk_operation_sessions
              (session_id, workspace_id, actor_id, title, status, created_at)
            VALUES
              ('session-a', 'workspace-a', 'operator-a', 'A', 'open',
               '2026-08-12T12:00:00Z'),
              ('session-b', 'workspace-a', 'operator-a', 'B', 'open',
               '2026-08-12T12:01:00Z'),
              ('session-c', 'workspace-a', 'operator-a', 'C', 'open',
               '2026-08-12T12:02:00Z')
            """
        )
        lineage = seed_identity_graphs(
            PostgresStoreBundle(self.connection),
            workspace_id="workspace-a",
            graph_ids=("graph-a", "graph-b"),
        )
        self.connection.execute(
            """
            INSERT INTO cpk_activity_plans
              (plan_id, session_id, base_graph_id, desired_graph_id,
               base_realized_projection_id, desired_realized_projection_id,
               status, created_at, payload)
            VALUES ('plan-a', 'session-a', 'graph-a', 'graph-b', %s, %s,
                    'planned', '2026-08-12T12:03:00Z', %s::jsonb)
            """,
            (
                lineage["graph-a"],
                lineage["graph-b"],
                json.dumps(DEFAULT_ACTIVITY_PLAN_CODEC.encode(ActivityPlan(()))),
            ),
        )
        self.connection.execute(
            """
            INSERT INTO cpk_approval_requests
              (request_id, session_id, plan_id, subject_kind, subject_payload,
               review_digest, requested_by, requested_at, required_scope,
               max_risk, destructive)
            VALUES
              ('approval-a', 'session-a', 'plan-a', 'activity-plan',
               '{"kind":"activity-plan","plan_id":"plan-a"}'::jsonb,
               encode(sha256(convert_to('activity-plan:plan-a', 'UTF8')), 'hex'),
               'operator-a', '2026-08-12T12:03:00Z', 'plan:approve', 'low', false),
              ('approval-b', 'session-a', 'plan-a', 'activity-plan',
               '{"kind":"activity-plan","plan_id":"plan-a"}'::jsonb,
               encode(sha256(convert_to('activity-plan:plan-a', 'UTF8')), 'hex'),
               'operator-a', '2026-08-12T12:04:00Z', 'plan:approve', 'low', false),
              ('approval-c', 'session-a', 'plan-a', 'activity-plan',
               '{"kind":"activity-plan","plan_id":"plan-a"}'::jsonb,
               encode(sha256(convert_to('activity-plan:plan-a', 'UTF8')), 'hex'),
               'operator-a', '2026-08-12T12:05:00Z', 'plan:approve', 'low', false)
            """
        )


if __name__ == "__main__":
    unittest.main()
