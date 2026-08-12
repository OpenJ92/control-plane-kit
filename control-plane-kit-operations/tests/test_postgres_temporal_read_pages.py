from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
import unittest

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
        from control_plane_kit_operations import read_services

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

        descriptor = read_services._session_summary_descriptor(session)

        self.assertNotIn("actions", descriptor)
        self.assertNotIn("plans", descriptor)
        self.assertNotIn("approvals", descriptor)
        self.assertNotIn("runs", descriptor)
        self.assertNotIn("events", descriptor)


if __name__ == "__main__":
    unittest.main()
