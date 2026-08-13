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

from control_plane_kit_core.delegation_keys import DelegationKeyPurpose
from control_plane_kit_core.public_ingress import IngressAuthorityReference
from control_plane_kit_core.runtime_authority import (
    RuntimeAuthorityAccessDelivery,
    RuntimeAuthorityAccessDeliveryKind,
    RuntimeAuthorityDeliverySecretReference,
    RuntimeAuthorityReference,
)
from control_plane_kit_core.secrets import SecretReference
from control_plane_kit_core.types import RuntimeKind
from control_plane_kit_operations.ingress_authorities import (
    CloudflareZoneIngressAuthority,
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
from control_plane_kit_operations.runtime_authorities import (
    RemoteDockerTlsAuthority,
)


_COUNT = 201
_LIMIT = 100
_INSTANT = datetime(2026, 8, 12, 12, tzinfo=timezone.utc)
_CURSOR_INSTANT = "2026-08-12T12:00:00.000000Z"
_REPLACEMENT_INSTANT = "2026-08-12T12:00:01.000000Z"
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

            connection.execute(
                """
                INSERT INTO cpk_operation_sessions
                  (session_id, workspace_id, actor_id, title, status, created_at)
                VALUES
                  ('open-session-0000', %s, 'operator', 'Before', 'open', %s),
                  ('open-session-0100a', %s, 'operator', 'After', 'open', %s)
                """,
                (
                    handles.open_workspace_id,
                    _INSTANT,
                    handles.open_workspace_id,
                    _INSTANT,
                ),
            )
            open_after_insert = stores.activity_history.session_page(
                ReadPageRequest(
                    ReadCollection.OPEN_SESSIONS,
                    open_scope,
                    _LIMIT,
                    open_first.next_cursor,
                )
            )
            self.assertEqual(open_after_insert.items[0].session_id, "open-session-0100a")
            self.assertNotIn(
                "open-session-0000",
                [item.session_id for item in open_after_insert.items],
            )
            open_fresh = stores.activity_history.session_page(
                ReadPageRequest(ReadCollection.OPEN_SESSIONS, open_scope, _LIMIT)
            )
            self.assertEqual(open_fresh.items[0].session_id, "open-session-0000")

            plan_scope = SessionReadScope(
                handles.plans_workspace_id,
                handles.plans_session_id,
            )
            plan_first = stores.activity_history.plan_page(
                ReadPageRequest(ReadCollection.SESSION_PLANS, plan_scope, _LIMIT)
            )
            for plan_id in ("plan-0000", "plan-0100a"):
                connection.execute(
                    """
                    INSERT INTO cpk_activity_plans
                      (plan_id, session_id, base_graph_id, desired_graph_id,
                       base_realized_projection_id,
                       desired_realized_projection_id, desired_graph_revision,
                       status, created_at, payload)
                    SELECT %s, session_id, base_graph_id, desired_graph_id,
                           base_realized_projection_id,
                           desired_realized_projection_id,
                           desired_graph_revision, status, created_at, payload
                    FROM cpk_activity_plans
                    WHERE plan_id = 'plan-0001'
                    """,
                    (plan_id,),
                )
            plan_continued = stores.activity_history.plan_page(
                ReadPageRequest(
                    ReadCollection.SESSION_PLANS,
                    plan_scope,
                    _LIMIT,
                    plan_first.next_cursor,
                )
            )
            self.assertEqual(plan_continued.items[0].plan_id, "plan-0100a")
            self.assertNotIn("plan-0000", [item.plan_id for item in plan_continued.items])
            plan_fresh = stores.activity_history.plan_page(
                ReadPageRequest(ReadCollection.SESSION_PLANS, plan_scope, _LIMIT)
            )
            self.assertEqual(plan_fresh.items[0].plan_id, "plan-0000")

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

            run_scope = PlanReadScope(
                handles.runs_workspace_id,
                handles.runs_plan_id,
            )
            run_first = stores.execution.run_page(
                ReadPageRequest(ReadCollection.PLAN_RUNS, run_scope, _LIMIT)
            )
            self._insert_run(connection, handles, "run-0000")
            self._insert_run(connection, handles, "run-0100a")
            connection.execute(
                """
                UPDATE cpk_activity_runs
                SET status = 'failed', settled_at = NULL
                WHERE run_id = 'run-0050'
                """
            )
            run_continued = stores.execution.run_page(
                ReadPageRequest(
                    ReadCollection.PLAN_RUNS,
                    run_scope,
                    _LIMIT,
                    run_first.next_cursor,
                )
            )
            self.assertEqual(run_continued.items[0].run_id, "run-0100a")
            self.assertNotIn("run-0000", [item.run_id for item in run_continued.items])
            self.assertNotIn("run-0050", [item.run_id for item in run_continued.items])
            run_fresh = stores.execution.run_page(
                ReadPageRequest(ReadCollection.PLAN_RUNS, run_scope, _LIMIT)
            )
            self.assertEqual(run_fresh.items[0].run_id, "run-0000")
            changed_run = next(item for item in run_fresh.items if item.run_id == "run-0050")
            self.assertEqual(changed_run.status.value, "failed")

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
            action_residue = connection.execute(
                "SELECT count(*) FROM cpk_operation_actions "
                "WHERE session_id = %s AND action_id = 'action-duplicate'",
                (handles.actions_session_id,),
            ).fetchone()[0]
            self.assertEqual(action_residue, 0)
            self.assertEqual(
                stores.activity_history.action_page(
                    ReadPageRequest(ReadCollection.SESSION_ACTIONS, action_scope, 1)
                ).items[0].action_id,
                "action-0001",
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
            event_residue = connection.execute(
                "SELECT count(*) FROM cpk_activity_events "
                "WHERE run_id = %s AND event_id = 'event-duplicate'",
                (handles.events_run_id,),
            ).fetchone()[0]
            self.assertEqual(event_residue, 0)
            self.assertEqual(
                stores.execution.event_page(
                    ReadPageRequest(ReadCollection.RUN_EVENTS, event_scope, 1)
                ).items[0].event_id,
                "event-0001",
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
            connection.execute(
                """
                INSERT INTO cpk_observations
                  (observation_id, workspace_id, subject_id, status,
                   observed_at, evidence, freshness)
                VALUES
                  ('observation-before', %s, 'subject-0000', 'healthy', %s,
                   '{}'::jsonb, 'fresh'),
                  ('observation-after', %s, 'subject-0100a', 'healthy', %s,
                   '{}'::jsonb, 'fresh')
                """,
                (
                    handles.observations_workspace_id,
                    _INSTANT,
                    handles.observations_workspace_id,
                    _INSTANT,
                ),
            )
            observation_after_insert = stores.observed_state.latest_page(
                ReadPageRequest(
                    ReadCollection.LATEST_OBSERVATIONS,
                    scope,
                    _LIMIT,
                    first.next_cursor,
                )
            )
            self.assertEqual(
                observation_after_insert.items[0].subject_id,
                "subject-0100a",
            )
            self.assertNotIn(
                "subject-0000",
                [item.subject_id for item in observation_after_insert.items],
            )
            observation_fresh = stores.observed_state.latest_page(
                ReadPageRequest(ReadCollection.LATEST_OBSERVATIONS, scope, _LIMIT)
            )
            self.assertEqual(observation_fresh.items[0].subject_id, "subject-0000")

            runtime_workspace = handles.runtime_authorities_workspace_id
            runtime_scope = WorkspaceReadScope(runtime_workspace)
            runtime_ref = RuntimeAuthorityReference("runtime-authority-0050")
            runtime_first = stores.runtime_authorities.active_page(
                ReadPageRequest(
                    ReadCollection.RUNTIME_AUTHORITIES,
                    runtime_scope,
                    _LIMIT,
                )
            )
            stores.runtime_authorities.revoke(runtime_workspace, runtime_ref)
            runtime_replacement = stores.runtime_authorities.register(
                workspace_id=runtime_workspace,
                authority_ref=runtime_ref,
                runtime_kind=RuntimeKind.DOCKER,
                authority=self._remote_runtime_authority("runtime-authority"),
                admitted_by="operator",
                admitted_at=_REPLACEMENT_INSTANT,
            )
            runtime_continued = stores.runtime_authorities.active_page(
                ReadPageRequest(
                    ReadCollection.RUNTIME_AUTHORITIES,
                    runtime_scope,
                    _LIMIT,
                    runtime_first.next_cursor,
                )
            )
            self.assertNotIn(
                runtime_ref.reference_id,
                [item.authority_ref.reference_id for item in runtime_continued.items],
            )
            runtime_fresh = stores.runtime_authorities.active_page(
                ReadPageRequest(
                    ReadCollection.RUNTIME_AUTHORITIES,
                    runtime_scope,
                    _LIMIT,
                )
            )
            self.assertEqual(
                next(
                    item for item in runtime_fresh.items
                    if item.authority_ref == runtime_ref
                ).registration_id,
                runtime_replacement.registration_id,
            )

            delivery_workspace = handles.runtime_deliveries_workspace_id
            delivery_scope = WorkspaceReadScope(delivery_workspace)
            delivery_ref = RuntimeAuthorityReference("runtime-delivery-0050")
            delivery_first = stores.runtime_authority_deliveries.active_page(
                ReadPageRequest(
                    ReadCollection.RUNTIME_AUTHORITY_DELIVERIES,
                    delivery_scope,
                    _LIMIT,
                )
            )
            stores.runtime_authorities.register(
                workspace_id=delivery_workspace,
                authority_ref=delivery_ref,
                runtime_kind=RuntimeKind.DOCKER,
                authority=self._remote_runtime_authority("runtime-delivery"),
                admitted_by="operator",
                admitted_at=_REPLACEMENT_INSTANT,
            )
            stores.runtime_authority_deliveries.revoke(
                delivery_workspace,
                delivery_ref,
            )
            delivery_replacement = stores.runtime_authority_deliveries.register(
                workspace_id=delivery_workspace,
                delivery=self._remote_runtime_delivery(delivery_ref),
                admitted_by="operator",
                admitted_at=_REPLACEMENT_INSTANT,
            )
            delivery_continued = stores.runtime_authority_deliveries.active_page(
                ReadPageRequest(
                    ReadCollection.RUNTIME_AUTHORITY_DELIVERIES,
                    delivery_scope,
                    _LIMIT,
                    delivery_first.next_cursor,
                )
            )
            self.assertNotIn(
                delivery_ref.reference_id,
                [item.authority_ref.reference_id for item in delivery_continued.items],
            )
            delivery_fresh = stores.runtime_authority_deliveries.active_page(
                ReadPageRequest(
                    ReadCollection.RUNTIME_AUTHORITY_DELIVERIES,
                    delivery_scope,
                    _LIMIT,
                )
            )
            self.assertEqual(
                next(
                    item for item in delivery_fresh.items
                    if item.authority_ref == delivery_ref
                ).delivery_id,
                delivery_replacement.delivery_id,
            )

            ingress_workspace = handles.ingress_authorities_workspace_id
            ingress_scope = WorkspaceReadScope(ingress_workspace)
            ingress_ref = IngressAuthorityReference("ingress-authority-0050")
            ingress_first = stores.ingress_authorities.active_page(
                ReadPageRequest(
                    ReadCollection.INGRESS_AUTHORITIES,
                    ingress_scope,
                    _LIMIT,
                )
            )
            stores.ingress_authorities.revoke(ingress_workspace, ingress_ref)
            ingress_replacement = stores.ingress_authorities.register(
                workspace_id=ingress_workspace,
                authority_ref=ingress_ref,
                authority=CloudflareZoneIngressAuthority(
                    account_id="synthetic-replacement-account",
                    zone_id="synthetic-replacement-zone",
                    zone_name="invalid.test",
                    api_token_ref=SecretReference(
                        "secret://synthetic/cloudflare/replacement-token"
                    ),
                    allowed_hostname_pattern="replacement-*.invalid.test",
                    generated_secret_provider_registration_id=(
                        "synthetic-replacement-provider"
                    ),
                    generated_secret_reference_prefix=SecretReference(
                        "secret://synthetic/replacement-ingress"
                    ),
                ),
                admitted_by="operator",
                admitted_at=_REPLACEMENT_INSTANT,
            )
            ingress_continued = stores.ingress_authorities.active_page(
                ReadPageRequest(
                    ReadCollection.INGRESS_AUTHORITIES,
                    ingress_scope,
                    _LIMIT,
                    ingress_first.next_cursor,
                )
            )
            self.assertNotIn(
                ingress_ref.reference_id,
                [item.authority_ref.reference_id for item in ingress_continued.items],
            )
            ingress_fresh = stores.ingress_authorities.active_page(
                ReadPageRequest(
                    ReadCollection.INGRESS_AUTHORITIES,
                    ingress_scope,
                    _LIMIT,
                )
            )
            self.assertEqual(
                next(
                    item for item in ingress_fresh.items
                    if item.authority_ref == ingress_ref
                ).registration_id,
                ingress_replacement.registration_id,
            )

            provider_scope = WorkspaceReadScope(handles.secret_providers_workspace_id)
            provider_first = stores.secret_providers.active_page(
                ReadPageRequest(ReadCollection.SECRET_PROVIDERS, provider_scope, _LIMIT)
            )
            connection.execute(
                """
                UPDATE cpk_secret_providers
                SET status = 'revoked', revoked_by = 'operator', revoked_at = %s
                WHERE workspace_id = %s AND provider_id = 'provider-0050'
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
                "provider-0050",
                [item.provider_id.value for item in provider_continued.items],
            )
            connection.execute(
                """
                INSERT INTO cpk_secret_providers
                  (registration_id, workspace_id, provider_id, provider_kind,
                   display_name, endpoint_reference, credential_reference,
                   allowed_reference_prefixes, allowed_intents, admitted_by,
                   admitted_at, status, supersedes_registration_id, metadata)
                SELECT 'provider-registration-replacement', workspace_id,
                       provider_id, provider_kind, display_name,
                       endpoint_reference, credential_reference,
                       allowed_reference_prefixes, allowed_intents, admitted_by,
                       admitted_at, 'active', registration_id, metadata
                FROM cpk_secret_providers
                WHERE workspace_id = %s AND provider_id = 'provider-0050'
                  AND status = 'revoked'
                """,
                (handles.secret_providers_workspace_id,),
            )
            provider_after_reregister = stores.secret_providers.active_page(
                ReadPageRequest(
                    ReadCollection.SECRET_PROVIDERS,
                    provider_scope,
                    _LIMIT,
                    provider_first.next_cursor,
                )
            )
            self.assertNotIn(
                "provider-0050",
                [item.provider_id.value for item in provider_after_reregister.items],
            )
            provider_fresh = stores.secret_providers.active_page(
                ReadPageRequest(ReadCollection.SECRET_PROVIDERS, provider_scope, _LIMIT)
            )
            provider_replacement = next(
                item for item in provider_fresh.items
                if item.provider_id.value == "provider-0050"
            )
            self.assertEqual(
                provider_replacement.registration_id,
                "provider-registration-replacement",
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
            connection.execute(
                """
                INSERT INTO cpk_secret_references
                  (registration_id, workspace_id, secret_reference,
                   provider_registration_id, allowed_intents, admitted_by,
                   admitted_at, status, metadata)
                VALUES
                  ('reference-0000', %s,
                   'secret://synthetic/reference/value-before',
                   'reference-parent-provider', '["postgres.password"]'::jsonb,
                   'operator', %s, 'active', '{}'::jsonb),
                  ('reference-0100a', %s,
                   'secret://synthetic/reference/value-after',
                   'reference-parent-provider', '["postgres.password"]'::jsonb,
                   'operator', %s, 'active', '{}'::jsonb)
                """,
                (
                    handles.secret_references_workspace_id,
                    _INSTANT,
                    handles.secret_references_workspace_id,
                    _INSTANT,
                ),
            )
            reference_after_insert = stores.secret_references.active_page(
                ReadPageRequest(
                    ReadCollection.SECRET_REFERENCES,
                    reference_scope,
                    _LIMIT,
                    reference_first.next_cursor,
                )
            )
            self.assertEqual(
                reference_after_insert.items[0].registration_id,
                "reference-0100a",
            )
            self.assertNotIn(
                "reference-0000",
                [item.registration_id for item in reference_after_insert.items],
            )
            reference_fresh = stores.secret_references.active_page(
                ReadPageRequest(
                    ReadCollection.SECRET_REFERENCES,
                    reference_scope,
                    _LIMIT,
                )
            )
            self.assertEqual(reference_fresh.items[0].registration_id, "reference-0000")

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
            self.assertEqual(
                [(item.purpose.value, item.issuer, item.key_id) for item in fresh.items],
                [
                    ("gateway-probe", "issuer", f"key-{value:04d}")
                    for value in range(1, _LIMIT + 1)
                ],
            )

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
    def _remote_runtime_authority(label: str) -> RemoteDockerTlsAuthority:
        return RemoteDockerTlsAuthority(
            endpoint=f"tcp://{label}.invalid:2376",
            ca_certificate=SecretReference(
                f"secret://synthetic/{label}/ca-certificate"
            ),
            client_certificate=SecretReference(
                f"secret://synthetic/{label}/client-certificate"
            ),
            client_key=SecretReference(
                f"secret://synthetic/{label}/client-key"
            ),
        )

    @staticmethod
    def _remote_runtime_delivery(
        authority_ref: RuntimeAuthorityReference,
    ) -> RuntimeAuthorityAccessDelivery:
        return RuntimeAuthorityAccessDelivery(
            authority_ref=authority_ref,
            delivery_kind=(
                RuntimeAuthorityAccessDeliveryKind.REMOTE_DOCKER_TLS_SECRET_FILES
            ),
            secret_references=(
                RuntimeAuthorityDeliverySecretReference(
                    "ca-cert",
                    SecretReference("secret://synthetic/runtime-delivery/ca"),
                ),
                RuntimeAuthorityDeliverySecretReference(
                    "client-cert",
                    SecretReference("secret://synthetic/runtime-delivery/cert"),
                ),
                RuntimeAuthorityDeliverySecretReference(
                    "client-key",
                    SecretReference("secret://synthetic/runtime-delivery/key"),
                ),
            ),
        )

    @staticmethod
    def _insert_run(connection, handles, run_id: str) -> None:
        approval_id = f"{run_id}-approval"
        decision_id = f"{run_id}-decision"
        execution_id = f"{run_id}-execution"
        connection.execute(
            """
            INSERT INTO cpk_approval_requests
              (request_id, session_id, plan_id, subject_kind, subject_payload,
               review_digest, requested_by, requested_at, required_scope,
               max_risk, destructive)
            VALUES (
              %s, %s, %s, 'activity-plan',
              jsonb_build_object('kind', 'activity-plan', 'plan_id', %s::text),
              encode(
                sha256(convert_to('activity-plan:' || %s::text, 'UTF8')),
                'hex'
              ),
              'operator', %s, 'plan:approve', 'low', false
            )
            """,
            (
                approval_id,
                handles.runs_session_id,
                handles.runs_plan_id,
                handles.runs_plan_id,
                handles.runs_plan_id,
                _INSTANT,
            ),
        )
        connection.execute(
            """
            INSERT INTO cpk_approval_decisions
              (decision_id, request_id, actor_id, decision, scope, decided_at)
            VALUES (%s, %s, 'reviewer', 'approved', 'plan:approve', %s)
            """,
            (decision_id, approval_id, _INSTANT),
        )
        connection.execute(
            """
            INSERT INTO cpk_execution_requests
              (request_id, workspace_id, session_id, plan_id, status,
               requested_by, requested_at, approval_request_id,
               approval_decision_id, idempotency_key, intent_fingerprint)
            VALUES (%s, %s, %s, %s, 'cancelled', 'operator', %s, %s, %s,
                    %s, %s)
            """,
            (
                execution_id,
                handles.runs_workspace_id,
                handles.runs_session_id,
                handles.runs_plan_id,
                _INSTANT,
                approval_id,
                decision_id,
                execution_id,
                f"{run_id}-fingerprint",
            ),
        )
        connection.execute(
            """
            INSERT INTO cpk_activity_runs
              (run_id, plan_id, request_id, attempt, status, created_at,
               started_at, settled_at, metadata)
            VALUES (%s, %s, %s, 1, 'succeeded', %s, %s, %s, '{}'::jsonb)
            """,
            (
                run_id,
                handles.runs_plan_id,
                execution_id,
                _INSTANT,
                _INSTANT,
                _INSTANT,
            ),
        )

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
                    DelegationKeyPurpose.GATEWAY_PROBE,
                    delegation_expected[-1][1],
                    delegation_expected[-1][2],
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
