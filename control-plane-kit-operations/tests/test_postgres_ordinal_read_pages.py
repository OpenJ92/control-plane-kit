from __future__ import annotations

from datetime import datetime, timezone
import os
from types import SimpleNamespace
import unittest

import psycopg

from tests.graph_lineage_fixture import seed_identity_graphs

from control_plane_kit_core.operations.commands import OperatorCommandKind
from control_plane_kit_core.operations.lifecycle import ActivityEventKind
from control_plane_kit_operations.postgres import (
    PostgresStoreBundle,
    PostgresUnitOfWork,
    install_schema,
)
from control_plane_kit_operations.postgres.activity_history import (
    PostgresActivityHistoryStore,
)
from control_plane_kit_operations.postgres.execution import PostgresExecutionStore
from control_plane_kit_operations.read_pages import (
    OrdinalReadCursor,
    ReadCollection,
    ReadPageRequest,
    RunReadScope,
    SessionReadScope,
)
from control_plane_kit_operations.records import (
    ActivityEventRecord,
    BoundedEvidence,
    OperationActionRecord,
)
from control_plane_kit_operations.read_services import InstanceReadService, ReadModelError


class _Rows:
    def __init__(self, rows: tuple[tuple[object, ...], ...]) -> None:
        self._rows = rows

    def fetchall(self) -> tuple[tuple[object, ...], ...]:
        return self._rows


class _RecordingConnection:
    def __init__(self, rows: tuple[tuple[object, ...], ...]) -> None:
        self.rows = rows
        self.calls: list[tuple[str, tuple[object, ...]]] = []

    def execute(self, query: str, parameters: tuple[object, ...]) -> _Rows:
        self.calls.append((query, parameters))
        return _Rows(self.rows)


def _action_row(action_id: str, ordinal: int) -> tuple[object, ...]:
    return (
        action_id,
        "session-a",
        ordinal,
        OperatorCommandKind.SET_DESIRED_GRAPH.value,
        "operator-a",
        {"note": action_id},
        datetime(2026, 8, 12, 12, ordinal, tzinfo=timezone.utc),
        None,
        None,
    )


def _event_row(event_id: str, ordinal: int) -> tuple[object, ...]:
    return (
        event_id,
        "run-a",
        ordinal,
        ActivityEventKind.RUN_STARTED.value,
        datetime(2026, 8, 12, 13, ordinal, tzinfo=timezone.utc),
        {"evidence": {"note": event_id}},
    )


class OrdinalPageSqlShapeTests(unittest.TestCase):
    def test_action_page_uses_strict_tuple_seek_and_limit_plus_one(self) -> None:
        connection = _RecordingConnection(
            (_action_row("action-b", 2), _action_row("action-c", 3))
        )
        request = ReadPageRequest(
            ReadCollection.SESSION_ACTIONS,
            SessionReadScope("workspace-a", "session-a"),
            1,
            OrdinalReadCursor(
                ReadCollection.SESSION_ACTIONS,
                SessionReadScope("workspace-a", "session-a"),
                1,
                "action-a",
            ),
        )

        page = PostgresActivityHistoryStore(connection).action_page(request)

        query, parameters = connection.calls[0]
        normalized = " ".join(query.split())
        self.assertIn("(ordinal, action_id) > (%s, %s)", normalized)
        self.assertIn("ORDER BY ordinal ASC, action_id ASC", normalized)
        self.assertIn("LIMIT %s", normalized)
        self.assertEqual(parameters, ("session-a", 1, "action-a", 2))
        self.assertEqual([item.action_id for item in page.items], ["action-b"])
        self.assertEqual(page.next_cursor.item_id, "action-b")

    def test_event_page_uses_strict_tuple_seek_and_limit_plus_one(self) -> None:
        connection = _RecordingConnection(
            (_event_row("event-b", 2), _event_row("event-c", 3))
        )
        request = ReadPageRequest(
            ReadCollection.RUN_EVENTS,
            RunReadScope("workspace-a", "run-a"),
            1,
            OrdinalReadCursor(
                ReadCollection.RUN_EVENTS,
                RunReadScope("workspace-a", "run-a"),
                1,
                "event-a",
            ),
        )

        page = PostgresExecutionStore(connection).event_page(request)

        query, parameters = connection.calls[0]
        normalized = " ".join(query.split())
        self.assertIn("(ordinal, event_id) > (%s, %s)", normalized)
        self.assertIn("ORDER BY ordinal ASC, event_id ASC", normalized)
        self.assertIn("LIMIT %s", normalized)
        self.assertEqual(parameters, ("run-a", 1, "event-a", 2))
        self.assertEqual([item.event_id for item in page.items], ["event-b"])
        self.assertEqual(page.next_cursor.item_id, "event-b")


class CommittedOrdinalAppendTests(unittest.TestCase):
    def setUp(self) -> None:
        database_url = os.environ.get("CPK_OPERATIONS_TEST_DATABASE_URL")
        if not database_url:
            raise RuntimeError(
                "CPK_OPERATIONS_TEST_DATABASE_URL is required. Run "
                "./control-plane-kit-operations/test.sh so Docker starts Postgres."
            )
        self.database_url = database_url
        self.connection = psycopg.connect(database_url, autocommit=True)
        install_schema(self.connection)
        self.connection.execute("TRUNCATE TABLE cpk_workspaces CASCADE")
        self._seed_parent_truth()

    def tearDown(self) -> None:
        self.connection.close()

    def unit_of_work(self) -> PostgresUnitOfWork:
        return PostgresUnitOfWork(lambda: psycopg.connect(self.database_url))

    def test_committed_action_append_is_visible_after_prior_cursor(self) -> None:
        first_request = ReadPageRequest(
            ReadCollection.SESSION_ACTIONS,
            SessionReadScope("workspace-a", "session-a"),
            2,
        )
        with self.unit_of_work() as reader:
            first = reader.stores.activity_history.action_page(first_request)
            reader.commit()

        with self.unit_of_work() as writer:
            ordinal = writer.stores.activity_history.next_action_ordinal("session-a")
            writer.stores.activity_history.add_action(self._action(ordinal))
            writer.commit()

        with self.unit_of_work() as reader:
            second = reader.stores.activity_history.action_page(
                ReadPageRequest(
                    ReadCollection.SESSION_ACTIONS,
                    SessionReadScope("workspace-a", "session-a"),
                    2,
                    first.next_cursor,
                )
            )
            reader.commit()

        self.assertEqual([item.ordinal for item in first.items], [1, 2])
        self.assertEqual([item.ordinal for item in second.items], [3, 4])
        self.assertEqual(first.next_cursor.ordinal, 2)
        self.assertIsNone(second.next_cursor)

    def test_committed_event_append_is_visible_after_prior_cursor(self) -> None:
        first_request = ReadPageRequest(
            ReadCollection.RUN_EVENTS,
            RunReadScope("workspace-a", "run-a"),
            2,
        )
        with self.unit_of_work() as reader:
            first = reader.stores.execution.event_page(first_request)
            reader.commit()

        with self.unit_of_work() as writer:
            ordinal = writer.stores.execution.next_event_ordinal("run-a")
            writer.stores.execution.add_event(self._event(ordinal))
            writer.commit()

        with self.unit_of_work() as reader:
            second = reader.stores.execution.event_page(
                ReadPageRequest(
                    ReadCollection.RUN_EVENTS,
                    RunReadScope("workspace-a", "run-a"),
                    2,
                    first.next_cursor,
                )
            )
            reader.commit()

        self.assertEqual([item.ordinal for item in first.items], [1, 2])
        self.assertEqual([item.ordinal for item in second.items], [3, 4])
        self.assertEqual(first.next_cursor.ordinal, 2)
        self.assertIsNone(second.next_cursor)

    def test_read_service_exposes_separate_redacted_pages_and_thin_parents(self) -> None:
        service = self._service()

        actions = service.session_actions(
            ReadPageRequest(
                ReadCollection.SESSION_ACTIONS,
                SessionReadScope("workspace-a", "session-a"),
                2,
            )
        ).descriptor()
        events = service.run_events(
            ReadPageRequest(
                ReadCollection.RUN_EVENTS,
                RunReadScope("workspace-a", "run-a"),
                2,
            )
        ).descriptor()
        timeline = service.activity_timeline("workspace-a").descriptor()

        self.assertEqual(set(actions), {"workspace_id", "kind", "limit", "items", "next_cursor"})
        self.assertEqual(set(events), {"workspace_id", "kind", "limit", "items", "next_cursor"})
        self.assertEqual(actions["items"][0]["payload"]["api_token"], "<redacted>")
        self.assertEqual(events["items"][0]["evidence"]["note"], "event-1")
        session = timeline["sessions"][0]
        self.assertNotIn("actions", session)
        self.assertNotIn("events", session["plans"][0]["runs"][0])

    def test_run_event_page_rejects_foreign_request_before_event_query(self) -> None:
        delegate = PostgresStoreBundle(self.connection).execution
        queries: list[str] = []

        class ForeignExecutionStore:
            def get_run(self, run_id):
                return delegate.get_run(run_id)

            def get_request(self, request_id):
                return SimpleNamespace(
                    identity=SimpleNamespace(workspace_id="workspace-b")
                )

            def event_page(self, request):
                queries.append(request.scope.run_id)
                return delegate.event_page(request)

        store = ForeignExecutionStore()
        service = self._service(execution_store=store)

        with self.assertRaisesRegex(ReadModelError, "missing run in workspace"):
            service.run_events(
                ReadPageRequest(
                    ReadCollection.RUN_EVENTS,
                    RunReadScope("workspace-a", "run-a"),
                    2,
                )
            )

        self.assertEqual(queries, [])

    def _service(self, *, execution_store=None) -> InstanceReadService:
        stores = PostgresStoreBundle(self.connection)
        return InstanceReadService(
            workspace_store=stores.workspaces,
            graph_topology_store=stores.graphs,
            activity_history_store=stores.activity_history,
            execution_store=execution_store or stores.execution,
            observed_state_store=stores.observed_state,
        )

    def _seed_parent_truth(self) -> None:
        self.connection.execute(
            """
            INSERT INTO cpk_workspaces (workspace_id, name, lifecycle)
            VALUES ('workspace-a', 'Workspace A', 'created');
            INSERT INTO cpk_operation_sessions
              (session_id, workspace_id, actor_id, title, status, created_at)
            VALUES ('session-a', 'workspace-a', 'operator-a', 'Deploy', 'open',
                    '2026-08-12T12:00:00Z');
            """
        )
        with self.unit_of_work() as unit_of_work:
            lineage = seed_identity_graphs(
                unit_of_work.stores,
                workspace_id="workspace-a",
                graph_ids=("graph-current", "graph-desired"),
            )
            for ordinal in range(1, 4):
                unit_of_work.stores.activity_history.add_action(self._action(ordinal))
            unit_of_work.commit()
        self.connection.execute(
            """
            INSERT INTO cpk_activity_plans
              (plan_id, session_id, base_graph_id, desired_graph_id,
               base_realized_projection_id, desired_realized_projection_id,
               status, created_at, payload)
            VALUES ('plan-a', 'session-a', 'graph-current', 'graph-desired',
                    %s, %s, 'planned', '2026-08-12T12:10:00Z', '{}'::jsonb);
            """,
            (lineage["graph-current"], lineage["graph-desired"]),
        )
        self.connection.execute(
            """
            INSERT INTO cpk_approval_requests
              (request_id, session_id, plan_id, subject_kind, subject_payload,
               review_digest, requested_by, requested_at,
               required_scope, max_risk, destructive)
            VALUES ('approval-a', 'session-a', 'plan-a', 'activity-plan',
                    '{"kind":"activity-plan","plan_id":"plan-a"}'::jsonb,
                    encode(sha256(convert_to('activity-plan:plan-a', 'UTF8')), 'hex'),
                    'operator-a', '2026-08-12T12:11:00Z',
                    'plan:approve', 'low', false);
            """
        )
        self.connection.execute(
            """
            INSERT INTO cpk_approval_decisions
              (decision_id, request_id, actor_id, decision, scope, decided_at)
            VALUES ('decision-a', 'approval-a', 'manager-a', 'approved',
                    'plan:approve', '2026-08-12T12:12:00Z');
            """
        )
        self.connection.execute(
            """
            INSERT INTO cpk_execution_requests
              (request_id, workspace_id, session_id, plan_id, status,
               requested_by, requested_at, approval_request_id,
               approval_decision_id, idempotency_key, intent_fingerprint)
            VALUES ('request-a', 'workspace-a', 'session-a', 'plan-a', 'queued',
                    'operator-a', '2026-08-12T12:13:00Z', 'approval-a',
                    'decision-a', 'execute-a', 'fingerprint-a');
            """
        )
        self.connection.execute(
            """
            INSERT INTO cpk_activity_runs
              (run_id, plan_id, request_id, attempt, status, created_at, metadata)
            VALUES ('run-a', 'plan-a', 'request-a', 1, 'claimed',
                    '2026-08-12T12:14:00Z', '{}'::jsonb);
            """
        )
        with self.unit_of_work() as unit_of_work:
            for ordinal in range(1, 4):
                unit_of_work.stores.execution.add_event(self._event(ordinal))
            unit_of_work.commit()

    @staticmethod
    def _action(ordinal: int) -> OperationActionRecord:
        return OperationActionRecord(
            action_id=f"action-{ordinal}",
            session_id="session-a",
            ordinal=ordinal,
            action_type=OperatorCommandKind.SET_DESIRED_GRAPH,
            actor_id="operator-a",
            payload={
                "api_token": "do-not-disclose",
                "note": f"action-{ordinal}",
            },
            created_at=f"2026-08-12T12:{ordinal:02d}:00Z",
        )

    @staticmethod
    def _event(ordinal: int) -> ActivityEventRecord:
        return ActivityEventRecord(
            event_id=f"event-{ordinal}",
            run_id="run-a",
            ordinal=ordinal,
            kind=ActivityEventKind.RUN_STARTED,
            occurred_at=f"2026-08-12T13:{ordinal:02d}:00Z",
            evidence=BoundedEvidence.from_mapping(
                {
                    "note": f"event-{ordinal}",
                }
            ),
        )


if __name__ == "__main__":
    unittest.main()
