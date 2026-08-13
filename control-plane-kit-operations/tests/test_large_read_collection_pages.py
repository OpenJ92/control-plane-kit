from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import os
from typing import Callable, Iterator
import unittest
import uuid

import psycopg

from tests.large_read_history_fixture import (
    LargeReadHistoryHandles,
    seed_large_read_history,
)

from control_plane_kit_operations.postgres import PostgresStoreBundle, install_schema
from control_plane_kit_operations.read_pages import (
    DelegationKeyReadCursor,
    EpochReadCursor,
    IdentityReadCursor,
    OrdinalReadCursor,
    PlanReadScope,
    ReadCollection,
    ReadPageRequest,
    RunReadScope,
    SessionReadScope,
    TemporalReadCursor,
    WorkspaceReadScope,
)


_COUNT = 201
_LIMIT = 100
_INSTANT = datetime(2026, 8, 12, 12, tzinfo=timezone.utc)
_CURSOR_INSTANT = "2026-08-12T12:00:00.000000Z"
_EPOCH = 1_786_534_400


@dataclass(frozen=True, slots=True)
class _CollectionCase:
    collection: ReadCollection
    scope: object
    fetch: Callable[[ReadPageRequest], object]
    identity: Callable[[object], object]
    expected: tuple[object, ...]
    final_cursor: object


class LargeReadCollectionPageTests(unittest.TestCase):
    @contextmanager
    def _seeded(self) -> Iterator[tuple[object, LargeReadHistoryHandles]]:
        database_url = os.environ.get("CPK_OPERATIONS_TEST_DATABASE_URL")
        if not database_url:
            raise RuntimeError(
                "CPK_OPERATIONS_TEST_DATABASE_URL is required; use Docker-first tests"
            )
        schema = f"large_read_pages_{uuid.uuid4().hex}"
        connection = psycopg.connect(database_url, autocommit=True)
        try:
            connection.execute(f'CREATE SCHEMA "{schema}"')
            connection.execute(f'SET search_path TO "{schema}"')
            install_schema(connection)
            handles = seed_large_read_history(connection, selected_count=_COUNT)
            yield connection, handles
        finally:
            connection.execute("SET search_path TO public")
            connection.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
            connection.close()

    def test_all_sixteen_collections_cross_two_maximum_pages_exactly(self) -> None:
        with self._seeded() as (connection, handles):
            cases = self._cases(connection, handles)
            self.assertEqual(len(cases), 16)
            self.assertEqual({case.collection for case in cases}, set(ReadCollection))

            for case in cases:
                with self.subTest(collection=case.collection.value):
                    cursor = None
                    actual: list[object] = []
                    pages = []
                    for expected_size in (_LIMIT, _LIMIT, 1):
                        page = case.fetch(
                            ReadPageRequest(
                                case.collection,
                                case.scope,
                                _LIMIT,
                                cursor,
                            )
                        )
                        pages.append(page)
                        self.assertEqual(len(page.items), expected_size)
                        actual.extend(case.identity(item) for item in page.items)
                        cursor = page.next_cursor

                    self.assertEqual(tuple(actual), case.expected)
                    self.assertEqual(len(actual), len(set(actual)))
                    self.assertIsNotNone(pages[0].next_cursor)
                    self.assertIsNotNone(pages[1].next_cursor)
                    self.assertIsNone(pages[2].next_cursor)

                    empty = case.fetch(
                        ReadPageRequest(
                            case.collection,
                            case.scope,
                            _LIMIT,
                            case.final_cursor,
                        )
                    )
                    self.assertEqual(empty.items, ())
                    self.assertIsNone(empty.next_cursor)

    def test_temporal_live_changes_keep_strict_cursor_membership(self) -> None:
        with self._seeded() as (connection, handles):
            stores = PostgresStoreBundle(connection)
            scope = WorkspaceReadScope(handles.activity_workspace_id)
            first = stores.activity_history.session_page(
                ReadPageRequest(ReadCollection.ACTIVITY_SESSIONS, scope, _LIMIT)
            )
            connection.execute(
                """
                INSERT INTO cpk_operation_sessions
                  (session_id, workspace_id, actor_id, title, status, created_at)
                VALUES
                  ('activity-session-0000', %s, 'operator', 'Before', 'open', %s),
                  ('activity-session-0100a', %s, 'operator', 'After', 'open', %s)
                """,
                (handles.activity_workspace_id, _INSTANT,
                 handles.activity_workspace_id, _INSTANT),
            )
            continued = stores.activity_history.session_page(
                ReadPageRequest(
                    ReadCollection.ACTIVITY_SESSIONS,
                    scope,
                    _LIMIT,
                    first.next_cursor,
                )
            )
            fresh = stores.activity_history.session_page(
                ReadPageRequest(ReadCollection.ACTIVITY_SESSIONS, scope, _LIMIT)
            )
            continued_ids = [item.session_id for item in continued.items]
            self.assertEqual(continued_ids[0], "activity-session-0100a")
            self.assertNotIn("activity-session-0000", continued_ids)
            self.assertEqual(fresh.items[0].session_id, "activity-session-0000")

            open_scope = WorkspaceReadScope(handles.open_workspace_id)
            open_first = stores.activity_history.session_page(
                ReadPageRequest(ReadCollection.OPEN_SESSIONS, open_scope, _LIMIT)
            )
            connection.execute(
                """
                UPDATE cpk_operation_sessions
                SET status = 'closed', closed_at = %s
                WHERE workspace_id = %s AND session_id = 'open-session-0150'
                """,
                (_INSTANT, handles.open_workspace_id),
            )
            open_continued = stores.activity_history.session_page(
                ReadPageRequest(
                    ReadCollection.OPEN_SESSIONS,
                    open_scope,
                    _LIMIT,
                    open_first.next_cursor,
                )
            )
            self.assertNotIn(
                "open-session-0150",
                [item.session_id for item in open_continued.items],
            )

            approval_scope = SessionReadScope(
                handles.approvals_workspace_id,
                handles.approvals_session_id,
            )
            approval_first = stores.activity_history.approval_page(
                ReadPageRequest(
                    ReadCollection.SESSION_APPROVALS,
                    approval_scope,
                    _LIMIT,
                )
            )
            connection.execute(
                """
                INSERT INTO cpk_approval_decisions
                  (decision_id, request_id, actor_id, decision, scope, decided_at)
                VALUES ('approval-decision-0050', 'approval-request-0050',
                        'reviewer', 'approved', 'plan:approve', %s)
                """,
                (_INSTANT,),
            )
            approval_continued = stores.activity_history.approval_page(
                ReadPageRequest(
                    ReadCollection.SESSION_APPROVALS,
                    approval_scope,
                    _LIMIT,
                    approval_first.next_cursor,
                )
            )
            self.assertNotIn(
                "approval-request-0050",
                [item.request.request_id for item in approval_continued.items],
            )
            approval_fresh = stores.activity_history.approval_page(
                ReadPageRequest(
                    ReadCollection.SESSION_APPROVALS,
                    approval_scope,
                    _LIMIT,
                )
            )
            changed = next(
                item
                for item in approval_fresh.items
                if item.request.request_id == "approval-request-0050"
            )
            self.assertIsNotNone(changed.decision)

            pending_scope = WorkspaceReadScope(handles.pending_workspace_id)
            pending_first = stores.activity_history.pending_approval_page(
                ReadPageRequest(
                    ReadCollection.PENDING_APPROVALS,
                    pending_scope,
                    _LIMIT,
                )
            )
            connection.execute(
                """
                INSERT INTO cpk_approval_decisions
                  (decision_id, request_id, actor_id, decision, scope, decided_at)
                VALUES ('pending-decision-0150', 'pending-request-0150',
                        'reviewer', 'approved', 'plan:approve', %s)
                """,
                (_INSTANT,),
            )
            pending_continued = stores.activity_history.pending_approval_page(
                ReadPageRequest(
                    ReadCollection.PENDING_APPROVALS,
                    pending_scope,
                    _LIMIT,
                    pending_first.next_cursor,
                )
            )
            self.assertNotIn(
                "pending-request-0150",
                [item.request.request_id for item in pending_continued.items],
            )

    def test_ordinal_append_and_duplicate_positions_are_distinct_laws(self) -> None:
        with self._seeded() as (connection, handles):
            stores = PostgresStoreBundle(connection)
            action_scope = SessionReadScope(
                handles.actions_workspace_id,
                handles.actions_session_id,
            )
            first = stores.activity_history.action_page(
                ReadPageRequest(ReadCollection.SESSION_ACTIONS, action_scope, _LIMIT)
            )
            connection.execute(
                """
                INSERT INTO cpk_operation_actions
                  (action_id, session_id, ordinal, action_type, actor_id,
                   payload, created_at)
                VALUES ('action-0202', %s, 202, 'record-operation-action',
                        'operator', '{}'::jsonb, %s)
                """,
                (handles.actions_session_id, _INSTANT),
            )
            values = self._remaining_ids(
                stores.activity_history.action_page,
                ReadCollection.SESSION_ACTIONS,
                action_scope,
                first.next_cursor,
                lambda item: item.action_id,
            )
            self.assertEqual(values[-1], "action-0202")
            with self.assertRaises(psycopg.errors.UniqueViolation):
                with connection.transaction():
                    connection.execute(
                        """
                        INSERT INTO cpk_operation_actions
                          (action_id, session_id, ordinal, action_type, actor_id,
                           payload, created_at)
                        VALUES ('action-duplicate', %s, 202,
                                'record-operation-action', 'operator',
                                '{}'::jsonb, %s)
                        """,
                        (handles.actions_session_id, _INSTANT),
                    )

            event_scope = RunReadScope(
                handles.events_workspace_id,
                handles.events_run_id,
            )
            event_first = stores.execution.event_page(
                ReadPageRequest(ReadCollection.RUN_EVENTS, event_scope, _LIMIT)
            )
            connection.execute(
                """
                INSERT INTO cpk_activity_events
                  (event_id, run_id, ordinal, event_type, occurred_at, payload)
                VALUES ('event-0202', %s, 202, 'run_started', %s, '{}'::jsonb)
                """,
                (handles.events_run_id, _INSTANT),
            )
            event_values = self._remaining_ids(
                stores.execution.event_page,
                ReadCollection.RUN_EVENTS,
                event_scope,
                event_first.next_cursor,
                lambda item: item.event_id,
            )
            self.assertEqual(event_values[-1], "event-0202")
            with self.assertRaises(psycopg.errors.UniqueViolation):
                with connection.transaction():
                    connection.execute(
                        """
                        INSERT INTO cpk_activity_events
                          (event_id, run_id, ordinal, event_type, occurred_at, payload)
                        VALUES ('event-duplicate', %s, 202, 'run_started', %s,
                                '{}'::jsonb)
                        """,
                        (handles.events_run_id, _INSTANT),
                    )

    def test_current_identity_views_evaluate_each_statement(self) -> None:
        with self._seeded() as (connection, handles):
            stores = PostgresStoreBundle(connection)
            scope = WorkspaceReadScope(handles.observations_workspace_id)
            first = stores.observed_state.latest_page(
                ReadPageRequest(ReadCollection.LATEST_OBSERVATIONS, scope, _LIMIT)
            )
            connection.execute(
                """
                INSERT INTO cpk_observations
                  (observation_id, workspace_id, subject_id, status,
                   observed_at, evidence, freshness)
                VALUES ('observation-replacement', %s, 'subject-0050',
                        'healthy', %s + interval '1 second', '{}'::jsonb, 'fresh')
                """,
                (handles.observations_workspace_id, _INSTANT),
            )
            continued = stores.observed_state.latest_page(
                ReadPageRequest(
                    ReadCollection.LATEST_OBSERVATIONS,
                    scope,
                    _LIMIT,
                    first.next_cursor,
                )
            )
            self.assertNotIn(
                "subject-0050",
                [item.subject_id for item in continued.items],
            )
            fresh = stores.observed_state.latest_page(
                ReadPageRequest(ReadCollection.LATEST_OBSERVATIONS, scope, _LIMIT)
            )
            replacement = next(
                item for item in fresh.items if item.subject_id == "subject-0050"
            )
            self.assertEqual(replacement.observation_id, "observation-replacement")

            provider_scope = WorkspaceReadScope(handles.secret_providers_workspace_id)
            provider_first = stores.secret_providers.active_page(
                ReadPageRequest(ReadCollection.SECRET_PROVIDERS, provider_scope, _LIMIT)
            )
            connection.execute(
                """
                UPDATE cpk_secret_providers
                SET status = 'revoked', revoked_by = 'operator', revoked_at = %s
                WHERE workspace_id = %s AND provider_id = 'provider-0150'
                """,
                (_INSTANT, handles.secret_providers_workspace_id),
            )
            provider_continued = stores.secret_providers.active_page(
                ReadPageRequest(
                    ReadCollection.SECRET_PROVIDERS,
                    provider_scope,
                    _LIMIT,
                    provider_first.next_cursor,
                )
            )
            self.assertNotIn(
                "provider-0150",
                [item.provider_id.value for item in provider_continued.items],
            )

            reference_scope = WorkspaceReadScope(handles.secret_references_workspace_id)
            reference_first = stores.secret_references.active_page(
                ReadPageRequest(
                    ReadCollection.SECRET_REFERENCES,
                    reference_scope,
                    _LIMIT,
                )
            )
            connection.execute(
                """
                UPDATE cpk_secret_references
                SET status = 'revoked', revoked_by = 'operator', revoked_at = %s
                WHERE workspace_id = %s AND registration_id = 'reference-0150'
                """,
                (_INSTANT, handles.secret_references_workspace_id),
            )
            reference_continued = stores.secret_references.active_page(
                ReadPageRequest(
                    ReadCollection.SECRET_REFERENCES,
                    reference_scope,
                    _LIMIT,
                    reference_first.next_cursor,
                )
            )
            self.assertNotIn(
                "reference-0150",
                [item.registration_id for item in reference_continued.items],
            )

    def test_delegation_lifecycle_preserves_full_tuple_position(self) -> None:
        with self._seeded() as (connection, handles):
            stores = PostgresStoreBundle(connection)
            scope = WorkspaceReadScope(handles.delegation_keys_workspace_id)
            first = stores.delegation_signing_keys.workspace_page(
                ReadPageRequest(
                    ReadCollection.DELEGATION_SIGNING_KEYS,
                    scope,
                    _LIMIT,
                )
            )
            connection.execute(
                """
                UPDATE cpk_delegation_signing_keys
                SET status = 'retired', retired_by = 'operator', retired_at = %s
                WHERE workspace_id = %s AND key_id = 'key-0050'
                """,
                (_INSTANT, handles.delegation_keys_workspace_id),
            )
            continued = stores.delegation_signing_keys.workspace_page(
                ReadPageRequest(
                    ReadCollection.DELEGATION_SIGNING_KEYS,
                    scope,
                    _LIMIT,
                    first.next_cursor,
                )
            )
            self.assertNotIn("key-0050", [item.key_id for item in continued.items])
            fresh = stores.delegation_signing_keys.workspace_page(
                ReadPageRequest(
                    ReadCollection.DELEGATION_SIGNING_KEYS,
                    scope,
                    _LIMIT,
                )
            )
            changed = next(item for item in fresh.items if item.key_id == "key-0050")
            self.assertEqual(changed.status.value, "retired")

    def test_gateway_probe_head_and_tail_follow_descending_cursor(self) -> None:
        with self._seeded() as (connection, handles):
            stores = PostgresStoreBundle(connection)
            scope = WorkspaceReadScope(handles.gateway_probes_workspace_id)
            first = stores.gateway_probes.page(
                ReadPageRequest(ReadCollection.GATEWAY_PROBES, scope, _LIMIT)
            )
            connection.execute(
                """
                INSERT INTO cpk_gateway_probe_attempts
                  (probe_id, workspace_id, request_id, actor_id, current_graph_id,
                   gateway_node_id, gateway_runtime_id, access_path, probe_kind,
                   target_id, request_digest, issuer, key_id, audience, grant_jti,
                   issued_at, expires_at, status, requested_at,
                   intent_fingerprint, evidence)
                VALUES
                  ('probe-new-head', %s, 'request-new-head', 'operator',
                   'probe-graph', 'gateway', 'runtime', 'runtime-private',
                   'http-status', 'target', repeat('a', 64), 'issuer', 'key',
                   'audience', 'grant-new-head', %s + 1, %s + 301, 'intended',
                   %s, 'fingerprint-new-head', '{}'::jsonb),
                  ('probe-new-tail', %s, 'request-new-tail', 'operator',
                   'probe-graph', 'gateway', 'runtime', 'runtime-private',
                   'http-status', 'target', repeat('b', 64), 'issuer', 'key',
                   'audience', 'grant-new-tail', %s - 1, %s + 299, 'intended',
                   %s, 'fingerprint-new-tail', '{}'::jsonb)
                """,
                (
                    handles.gateway_probes_workspace_id,
                    _EPOCH,
                    _EPOCH,
                    _INSTANT,
                    handles.gateway_probes_workspace_id,
                    _EPOCH,
                    _EPOCH,
                    _INSTANT,
                ),
            )
            continued_ids = self._remaining_ids(
                stores.gateway_probes.page,
                ReadCollection.GATEWAY_PROBES,
                scope,
                first.next_cursor,
                lambda item: item.probe_id,
            )
            self.assertNotIn("probe-new-head", continued_ids)
            self.assertIn("probe-new-tail", continued_ids)
            fresh = stores.gateway_probes.page(
                ReadPageRequest(ReadCollection.GATEWAY_PROBES, scope, _LIMIT)
            )
            self.assertEqual(fresh.items[0].probe_id, "probe-new-head")

    @staticmethod
    def _remaining_ids(fetch, collection, scope, cursor, identity):
        values = []
        while cursor is not None:
            page = fetch(ReadPageRequest(collection, scope, _LIMIT, cursor))
            values.extend(identity(item) for item in page.items)
            cursor = page.next_cursor
        return values

    @staticmethod
    def _cases(connection, handles: LargeReadHistoryHandles) -> tuple[_CollectionCase, ...]:
        stores = PostgresStoreBundle(connection)

        def temporal(collection, scope, fetch, prefix, identity):
            expected = tuple(f"{prefix}-{value:04d}" for value in range(1, _COUNT + 1))
            return _CollectionCase(
                collection,
                scope,
                fetch,
                identity,
                expected,
                TemporalReadCursor(
                    collection,
                    scope,
                    _CURSOR_INSTANT,
                    expected[-1],
                ),
            )

        def ordinal(collection, scope, fetch, prefix, identity):
            expected = tuple(f"{prefix}-{value:04d}" for value in range(1, _COUNT + 1))
            return _CollectionCase(
                collection,
                scope,
                fetch,
                identity,
                expected,
                OrdinalReadCursor(collection, scope, _COUNT, expected[-1]),
            )

        def identity_case(collection, workspace_id, fetch, prefix, identity):
            scope = WorkspaceReadScope(workspace_id)
            expected = tuple(f"{prefix}-{value:04d}" for value in range(1, _COUNT + 1))
            return _CollectionCase(
                collection,
                scope,
                fetch,
                identity,
                expected,
                IdentityReadCursor(collection, scope, expected[-1]),
            )

        cases = [
            temporal(ReadCollection.ACTIVITY_SESSIONS,
                     WorkspaceReadScope(handles.activity_workspace_id),
                     stores.activity_history.session_page, "activity-session",
                     lambda item: item.session_id),
            temporal(ReadCollection.OPEN_SESSIONS,
                     WorkspaceReadScope(handles.open_workspace_id),
                     stores.activity_history.session_page, "open-session",
                     lambda item: item.session_id),
            ordinal(ReadCollection.SESSION_ACTIONS,
                    SessionReadScope(handles.actions_workspace_id, handles.actions_session_id),
                    stores.activity_history.action_page, "action",
                    lambda item: item.action_id),
            temporal(ReadCollection.SESSION_PLANS,
                     SessionReadScope(handles.plans_workspace_id, handles.plans_session_id),
                     stores.activity_history.plan_page, "plan",
                     lambda item: item.plan_id),
            temporal(ReadCollection.SESSION_APPROVALS,
                     SessionReadScope(handles.approvals_workspace_id, handles.approvals_session_id),
                     stores.activity_history.approval_page, "approval-request",
                     lambda item: item.request.request_id),
            temporal(ReadCollection.PENDING_APPROVALS,
                     WorkspaceReadScope(handles.pending_workspace_id),
                     stores.activity_history.pending_approval_page, "pending-request",
                     lambda item: item.request.request_id),
            temporal(ReadCollection.PLAN_RUNS,
                     PlanReadScope(handles.runs_workspace_id, handles.runs_plan_id),
                     stores.execution.run_page, "run",
                     lambda item: item.run_id),
            ordinal(ReadCollection.RUN_EVENTS,
                    RunReadScope(handles.events_workspace_id, handles.events_run_id),
                    stores.execution.event_page, "event",
                    lambda item: item.event_id),
            identity_case(ReadCollection.LATEST_OBSERVATIONS,
                          handles.observations_workspace_id,
                          stores.observed_state.latest_page, "subject",
                          lambda item: item.subject_id),
            identity_case(ReadCollection.RUNTIME_AUTHORITIES,
                          handles.runtime_authorities_workspace_id,
                          stores.runtime_authorities.active_page, "runtime-authority",
                          lambda item: item.authority_ref.reference_id),
            identity_case(ReadCollection.RUNTIME_AUTHORITY_DELIVERIES,
                          handles.runtime_deliveries_workspace_id,
                          stores.runtime_authority_deliveries.active_page,
                          "runtime-delivery", lambda item: item.authority_ref.reference_id),
            identity_case(ReadCollection.INGRESS_AUTHORITIES,
                          handles.ingress_authorities_workspace_id,
                          stores.ingress_authorities.active_page, "ingress-authority",
                          lambda item: item.authority_ref.reference_id),
            identity_case(ReadCollection.SECRET_PROVIDERS,
                          handles.secret_providers_workspace_id,
                          stores.secret_providers.active_page, "provider",
                          lambda item: item.provider_id.value),
            identity_case(ReadCollection.SECRET_REFERENCES,
                          handles.secret_references_workspace_id,
                          stores.secret_references.active_page, "reference",
                          lambda item: item.registration_id),
        ]
        delegation_scope = WorkspaceReadScope(handles.delegation_keys_workspace_id)
        delegation_expected = tuple(
            ("gateway-probe", "issuer", f"key-{value:04d}")
            for value in range(1, _COUNT + 1)
        )
        cases.append(
            _CollectionCase(
                ReadCollection.DELEGATION_SIGNING_KEYS,
                delegation_scope,
                stores.delegation_signing_keys.workspace_page,
                lambda item: (item.purpose.value, item.issuer, item.key_id),
                delegation_expected,
                DelegationKeyReadCursor(
                    ReadCollection.DELEGATION_SIGNING_KEYS,
                    delegation_scope,
                    *delegation_expected[-1],
                ),
            )
        )
        probe_scope = WorkspaceReadScope(handles.gateway_probes_workspace_id)
        probe_expected = tuple(
            f"probe-{value:04d}" for value in range(_COUNT, 0, -1)
        )
        cases.append(
            _CollectionCase(
                ReadCollection.GATEWAY_PROBES,
                probe_scope,
                stores.gateway_probes.page,
                lambda item: item.probe_id,
                probe_expected,
                EpochReadCursor(
                    ReadCollection.GATEWAY_PROBES,
                    probe_scope,
                    _EPOCH,
                    probe_expected[-1],
                ),
            )
        )
        return tuple(cases)


if __name__ == "__main__":
    unittest.main()
