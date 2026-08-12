from __future__ import annotations

import os
import unittest

import psycopg

from control_plane_kit_core.delegation_keys import DelegationKeyPurpose
from control_plane_kit_operations.postgres import install_schema
from control_plane_kit_operations.postgres.activity_history import (
    PostgresActivityHistoryStore,
)
from control_plane_kit_operations.postgres.delegation_signing_key_store import (
    DelegationSigningKeyStore,
)
from control_plane_kit_operations.postgres.execution import PostgresExecutionStore
from control_plane_kit_operations.postgres.gateway_probe_store import GatewayProbeStore
from control_plane_kit_operations.postgres.observed_state import (
    PostgresObservedStateStore,
)
from control_plane_kit_operations.postgres.secret_provider_store import (
    SecretReferenceStore,
)
from control_plane_kit_operations.postgres import current_schema_contract
from control_plane_kit_operations.postgres import schema
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


_EXPECTED_QUERY_PATH_INDEXES = {
    "cpk_operation_sessions_workspace_timeline": (
        "cpk_operation_sessions",
        ("workspace_id", "created_at", "session_id"),
        None,
    ),
    "cpk_operation_sessions_open_timeline": (
        "cpk_operation_sessions",
        ("workspace_id", "created_at", "session_id"),
        "(status = 'open'::text)",
    ),
    "cpk_activity_plans_session_timeline": (
        "cpk_activity_plans",
        ("session_id", "created_at", "plan_id"),
        None,
    ),
    "cpk_approval_requests_session_timeline": (
        "cpk_approval_requests",
        ("session_id", "requested_at", "request_id"),
        None,
    ),
    "cpk_approval_requests_pending_timeline": (
        "cpk_approval_requests",
        ("requested_at", "request_id"),
        None,
    ),
    "cpk_activity_runs_plan_timeline": (
        "cpk_activity_runs",
        ("plan_id", "created_at", "run_id"),
        None,
    ),
    "cpk_secret_references_active_registration": (
        "cpk_secret_references",
        ("workspace_id", "registration_id"),
        "(status = 'active'::text)",
    ),
}


class QueryPathIndexContractTests(unittest.TestCase):
    def test_current_schema_owns_exact_query_path_indexes(self) -> None:
        contract = current_schema_contract.CURRENT_POSTGRES_SCHEMA_CONTRACT
        indexes = {value.name: value for value in contract.indexes}

        self.assertEqual(len(contract.indexes), 94)
        for name, (relation, keys, predicate) in _EXPECTED_QUERY_PATH_INDEXES.items():
            with self.subTest(index=name):
                value = indexes[name]
                self.assertEqual(value.relation, relation)
                self.assertEqual(value.key_entries, keys)
                self.assertEqual(value.predicate, predicate)
                self.assertFalse(value.unique)
                self.assertEqual(value.include_entries, ())
                self.assertEqual(value.access_method, "btree")

    def test_direct_schema_and_semantic_contract_agree_exactly(self) -> None:
        sql = schema._CURRENT_SCHEMA_SQL

        for name, (relation, keys, predicate) in _EXPECTED_QUERY_PATH_INDEXES.items():
            with self.subTest(index=name):
                declaration = (
                    f"CREATE INDEX {name} ON {relation} USING btree "
                    f"({', '.join(keys)})"
                )
                if predicate is not None:
                    declaration += f" WHERE {predicate}"
                self.assertEqual(sql.count(declaration + ";"), 1)

        pending = _EXPECTED_QUERY_PATH_INDEXES[
            "cpk_approval_requests_pending_timeline"
        ]
        self.assertEqual(pending[1], ("requested_at", "request_id"))
        self.assertNotIn(
            "cpk_approval_requests_pending_timeline ON "
            "cpk_approval_requests USING btree "
            "(requested_at, request_id, session_id)",
            sql,
        )


class _NoRows:
    def fetchall(self) -> tuple[object, ...]:
        return ()


class _ObservingConnection:
    def __init__(self) -> None:
        self.statement: str | None = None
        self.parameters: tuple[object, ...] | None = None

    def execute(
        self,
        statement: str,
        parameters: tuple[object, ...] = (),
    ) -> _NoRows:
        self.statement = statement
        self.parameters = parameters
        return _NoRows()


def _plan_nodes(plan: dict[str, object]):
    yield plan
    for child in plan.get("Plans", ()):  # type: ignore[union-attr]
        yield from _plan_nodes(child)


class QueryPathPlannerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        database_url = os.environ.get("CPK_OPERATIONS_TEST_DATABASE_URL")
        if not database_url:
            raise RuntimeError("CPK_OPERATIONS_TEST_DATABASE_URL is required")
        cls.connection = psycopg.connect(database_url, autocommit=True)
        install_schema(cls.connection)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.connection.close()

    def setUp(self) -> None:
        self.connection.execute("TRUNCATE TABLE cpk_workspaces CASCADE")

    def test_temporal_and_identity_pages_use_exact_production_statements(self) -> None:
        cases = (
            (
                "sessions",
                self._seed_sessions,
                lambda connection: PostgresActivityHistoryStore(connection).session_page(
                    ReadPageRequest(
                        ReadCollection.ACTIVITY_SESSIONS,
                        WorkspaceReadScope("workspace-target"),
                        100,
                        TemporalReadCursor(
                            ReadCollection.ACTIVITY_SESSIONS,
                            WorkspaceReadScope("workspace-target"),
                            "2026-08-12T00:01:40.000000Z",
                            "session-100",
                        ),
                    )
                ),
                {"cpk_operation_sessions_workspace_timeline"},
                ("workspace_id", "created_at", "session_id"),
            ),
            (
                "open-sessions",
                self._seed_sessions,
                lambda connection: PostgresActivityHistoryStore(connection).session_page(
                    ReadPageRequest(
                        ReadCollection.OPEN_SESSIONS,
                        WorkspaceReadScope("workspace-target"),
                        100,
                        TemporalReadCursor(
                            ReadCollection.OPEN_SESSIONS,
                            WorkspaceReadScope("workspace-target"),
                            "2026-08-12T00:01:40.000000Z",
                            "session-100",
                        ),
                    )
                ),
                {"cpk_operation_sessions_open_timeline"},
                ("workspace_id", "created_at", "session_id"),
            ),
            (
                "plans",
                self._seed_plans,
                lambda connection: PostgresActivityHistoryStore(connection).plan_page(
                    ReadPageRequest(
                        ReadCollection.SESSION_PLANS,
                        SessionReadScope("workspace-target", "session-target"),
                        100,
                        TemporalReadCursor(
                            ReadCollection.SESSION_PLANS,
                            SessionReadScope("workspace-target", "session-target"),
                            "2026-08-12T00:01:40.000000Z",
                            "plan-100",
                        ),
                    )
                ),
                {"cpk_activity_plans_session_timeline"},
                ("session_id", "created_at", "plan_id"),
            ),
            (
                "session-approvals",
                self._seed_session_approvals,
                lambda connection: PostgresActivityHistoryStore(
                    connection
                ).approval_page(
                    ReadPageRequest(
                        ReadCollection.SESSION_APPROVALS,
                        SessionReadScope("workspace-target", "session-target"),
                        100,
                        TemporalReadCursor(
                            ReadCollection.SESSION_APPROVALS,
                            SessionReadScope("workspace-target", "session-target"),
                            "2026-08-12T02:46:40.000000Z",
                            "approval-target-100",
                        ),
                    )
                ),
                {"cpk_approval_requests_session_timeline"},
                ("session_id", "requested_at", "request_id"),
            ),
            (
                "plan-runs",
                self._seed_runs,
                lambda connection: PostgresExecutionStore(connection).run_page(
                    ReadPageRequest(
                        ReadCollection.PLAN_RUNS,
                        PlanReadScope("workspace-target", "plan-target"),
                        100,
                        TemporalReadCursor(
                            ReadCollection.PLAN_RUNS,
                            PlanReadScope("workspace-target", "plan-target"),
                            "2026-08-12T00:01:40.000000Z",
                            "run-100",
                        ),
                    )
                ),
                {"cpk_activity_runs_plan_timeline"},
                ("plan_id", "created_at", "run_id"),
            ),
            (
                "secret-references",
                self._seed_secret_references,
                lambda connection: SecretReferenceStore(connection).active_page(
                    ReadPageRequest(
                        ReadCollection.SECRET_REFERENCES,
                        WorkspaceReadScope("workspace-target"),
                        100,
                        IdentityReadCursor(
                            ReadCollection.SECRET_REFERENCES,
                            WorkspaceReadScope("workspace-target"),
                            "registration-00010000-target",
                        ),
                    )
                ),
                {"cpk_secret_references_active_registration"},
                ("workspace_id", "registration_id"),
            ),
        )

        for name, seed, invoke, expected_indexes, qualification_fields in cases:
            with self.subTest(case=name):
                self.connection.execute("TRUNCATE TABLE cpk_workspaces CASCADE")
                seed()
                observed = _ObservingConnection()
                page = invoke(observed)
                self.assertEqual(page.items, ())
                plan = self._explain_observed(observed)
                self.assertLessEqual(plan["Actual Rows"], 101)
                self.assertLessEqual(
                    expected_indexes,
                    {
                        node["Index Name"]
                        for node in _plan_nodes(plan)
                        if "Index Name" in node
                    },
                )
                self._assert_plan_qualifications(plan, qualification_fields)

    def test_pending_approvals_adapt_between_sparse_and_dense_tenants(self) -> None:
        self._seed_pending_approvals(target_count=201, foreign_count=20_000)
        sparse = self._pending_plan()
        sparse_indexes = {
            node["Index Name"]
            for node in _plan_nodes(sparse)
            if "Index Name" in node
        }
        self.assertLessEqual(
            {
                "cpk_operation_sessions_workspace_timeline",
                "cpk_approval_requests_session_timeline",
            },
            sparse_indexes,
        )
        self.assertNotIn(
            "cpk_approval_requests_pending_timeline",
            sparse_indexes,
        )
        self.assertLessEqual(sparse["Actual Rows"], 101)

        self.connection.execute("TRUNCATE TABLE cpk_workspaces CASCADE")
        self._seed_pending_approvals(target_count=20_000, foreign_count=20_000)
        dense = self._pending_plan()
        dense_indexes = {
            node["Index Name"]
            for node in _plan_nodes(dense)
            if "Index Name" in node
        }
        self.assertIn(
            "cpk_approval_requests_pending_timeline",
            dense_indexes,
        )
        self.assertLessEqual(dense["Actual Rows"], 101)

    def test_existing_indexes_remain_control_witnesses(self) -> None:
        cases = (
            (
                "session-actions",
                self._seed_actions,
                lambda connection: PostgresActivityHistoryStore(
                    connection
                ).action_page(
                    ReadPageRequest(
                        ReadCollection.SESSION_ACTIONS,
                        SessionReadScope("workspace-target", "session-target"),
                        100,
                        OrdinalReadCursor(
                            ReadCollection.SESSION_ACTIONS,
                            SessionReadScope("workspace-target", "session-target"),
                            100,
                            "action-100",
                        ),
                    )
                ),
                "cpk_operation_actions_session_id_ordinal_key",
                ("session_id", "ordinal", "action_id"),
            ),
            (
                "run-events",
                self._seed_events,
                lambda connection: PostgresExecutionStore(connection).event_page(
                    ReadPageRequest(
                        ReadCollection.RUN_EVENTS,
                        RunReadScope("workspace-target", "run-target"),
                        100,
                        OrdinalReadCursor(
                            ReadCollection.RUN_EVENTS,
                            RunReadScope("workspace-target", "run-target"),
                            100,
                            "event-100",
                        ),
                    )
                ),
                "cpk_activity_events_run_id_ordinal_key",
                ("run_id", "ordinal", "event_id"),
            ),
            (
                "latest-observations",
                self._seed_observations,
                lambda connection: PostgresObservedStateStore(
                    connection
                ).latest_page(
                    ReadPageRequest(
                        ReadCollection.LATEST_OBSERVATIONS,
                        WorkspaceReadScope("workspace-target"),
                        100,
                        IdentityReadCursor(
                            ReadCollection.LATEST_OBSERVATIONS,
                            WorkspaceReadScope("workspace-target"),
                            "subject-000100",
                        ),
                    )
                ),
                "cpk_observations_latest_subject",
                ("workspace_id", "subject_id"),
            ),
            (
                "delegation-signing-keys",
                self._seed_delegation_keys,
                lambda connection: DelegationSigningKeyStore(
                    connection
                ).workspace_page(
                    ReadPageRequest(
                        ReadCollection.DELEGATION_SIGNING_KEYS,
                        WorkspaceReadScope("workspace-target"),
                        100,
                        DelegationKeyReadCursor(
                            ReadCollection.DELEGATION_SIGNING_KEYS,
                            WorkspaceReadScope("workspace-target"),
                            DelegationKeyPurpose.GATEWAY_PROBE,
                            "issuer-0",
                            "key-000100",
                        ),
                    )
                ),
                "cpk_delegation_signing_keys_workspace_id_purpose_issuer_key_key",
                ("workspace_id", "purpose", "issuer", "key_id"),
            ),
            (
                "gateway-probes",
                self._seed_gateway_probes,
                lambda connection: GatewayProbeStore(connection).page(
                    ReadPageRequest(
                        ReadCollection.GATEWAY_PROBES,
                        WorkspaceReadScope("workspace-target"),
                        100,
                        EpochReadCursor(
                            ReadCollection.GATEWAY_PROBES,
                            WorkspaceReadScope("workspace-target"),
                            9000,
                            "probe-9000",
                        ),
                    )
                ),
                "cpk_gateway_probe_workspace_timeline",
                ("workspace_id", "issued_at", "probe_id"),
            ),
        )

        for name, seed, invoke, expected_index, qualification_fields in cases:
            with self.subTest(case=name):
                self.connection.execute("TRUNCATE TABLE cpk_workspaces CASCADE")
                seed()
                observed = _ObservingConnection()
                page = invoke(observed)
                self.assertEqual(page.items, ())
                plan = self._explain_observed(observed)
                self.assertLessEqual(plan["Actual Rows"], 101)
                self.assertIn(
                    expected_index,
                    {
                        node["Index Name"]
                        for node in _plan_nodes(plan)
                        if "Index Name" in node
                    },
                )
                self._assert_plan_qualifications(plan, qualification_fields)

    def _pending_plan(self) -> dict[str, object]:
        observed = _ObservingConnection()
        page = PostgresActivityHistoryStore(observed).pending_approval_page(
            ReadPageRequest(
                ReadCollection.PENDING_APPROVALS,
                WorkspaceReadScope("workspace-target"),
                100,
            )
        )
        self.assertEqual(page.items, ())
        return self._explain_observed(observed)

    def _explain_observed(
        self,
        observed: _ObservingConnection,
    ) -> dict[str, object]:
        self.assertIsNotNone(observed.statement)
        self.assertIsNotNone(observed.parameters)
        normalized = " ".join(observed.statement.split())
        self.assertIn("LIMIT %s", normalized)
        self.assertEqual(observed.parameters[-1], 101)
        row = self.connection.execute(
            "EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + observed.statement,
            observed.parameters,
        ).fetchone()
        self.assertIsNotNone(row)
        return row[0][0]["Plan"]

    def _assert_plan_qualifications(
        self,
        plan: dict[str, object],
        fields: tuple[str, ...],
    ) -> None:
        condition_text = " ".join(
            str(node[key])
            for node in _plan_nodes(plan)
            for key in ("Index Cond", "Recheck Cond", "Filter")
            if key in node
        )
        for field in fields:
            with self.subTest(qualification=field):
                self.assertIn(field, condition_text)

    def _workspace(self, workspace_id: str) -> None:
        self.connection.execute(
            """
            INSERT INTO cpk_workspaces (workspace_id, name, lifecycle)
            VALUES (%s, %s, 'running')
            """,
            (workspace_id, workspace_id),
        )

    def _analyze(self, *relations: str) -> None:
        for relation in relations:
            self.connection.execute(f"ANALYZE {relation}")

    def _seed_sessions(self) -> None:
        self._workspace("workspace-target")
        self.connection.execute(
            """
            INSERT INTO cpk_operation_sessions
              (session_id, workspace_id, actor_id, title, status, created_at)
            SELECT 'session-' || value, 'workspace-target', 'operator', 'Session',
                   'open', '2026-08-12T00:00:00Z'::timestamptz
                           + value * interval '1 second'
            FROM generate_series(1, 10000) AS value
            """
        )
        self._analyze("cpk_operation_sessions")

    def _seed_plans(self) -> None:
        self.connection.execute("SET session_replication_role = replica")
        try:
            self.connection.execute(
                """
                INSERT INTO cpk_activity_plans
                  (plan_id, session_id, base_graph_id, desired_graph_id,
                   base_realized_projection_id, desired_realized_projection_id,
                   status, created_at, payload)
                SELECT 'plan-' || value, 'session-target', 'graph-a', 'graph-b',
                       'projection-a', 'projection-b', 'planned',
                       '2026-08-12T00:00:00Z'::timestamptz
                         + value * interval '1 second',
                       '{}'::jsonb
                FROM generate_series(1, 10000) AS value
                """
            )
        finally:
            self.connection.execute("SET session_replication_role = origin")
        self._analyze("cpk_activity_plans")

    def _seed_session_approvals(self) -> None:
        self._workspace("workspace-target")
        self._workspace("workspace-foreign")
        self.connection.execute(
            """
            INSERT INTO cpk_operation_sessions
              (session_id, workspace_id, actor_id, title, status, created_at)
            VALUES ('session-target', 'workspace-target', 'operator', 'Target',
                    'open', '2026-08-12T00:00:00Z');
            INSERT INTO cpk_operation_sessions
              (session_id, workspace_id, actor_id, title, status, created_at)
            SELECT 'session-foreign-' || value, 'workspace-foreign', 'operator',
                   'Foreign', 'open', '2026-08-12T00:00:00Z'::timestamptz
                     + value * interval '1 second'
            FROM generate_series(1, 1000) AS value
            """
        )
        self.connection.execute("SET session_replication_role = replica")
        try:
            self._insert_approvals(
                prefix="foreign",
                session_expression="'session-foreign-' || (((value - 1) % 1000) + 1)",
                count=20_000,
                offset_seconds=1,
            )
            self._insert_approvals(
                prefix="target",
                session_expression="'session-target'",
                count=201,
                offset_seconds=100,
            )
        finally:
            self.connection.execute("SET session_replication_role = origin")
        self._analyze(
            "cpk_operation_sessions",
            "cpk_approval_requests",
            "cpk_approval_decisions",
        )

    def _seed_pending_approvals(
        self,
        *,
        target_count: int,
        foreign_count: int,
    ) -> None:
        self._workspace("workspace-target")
        self._workspace("workspace-foreign")
        if target_count == 201:
            self.connection.execute(
                """
                INSERT INTO cpk_operation_sessions
                  (session_id, workspace_id, actor_id, title, status, created_at)
                VALUES ('session-target', 'workspace-target', 'operator', 'Target',
                        'open', '2026-08-12T00:00:00Z')
                """
            )
            target_session_expression = "'session-target'"
        else:
            self.connection.execute(
                """
                INSERT INTO cpk_operation_sessions
                  (session_id, workspace_id, actor_id, title, status, created_at)
                SELECT 'session-target-' || value, 'workspace-target', 'operator',
                       'Target', 'open', '2026-08-12T00:00:00Z'::timestamptz
                         + value * interval '1 second'
                FROM generate_series(1, 1000) AS value
                """
            )
            target_session_expression = (
                "'session-target-' || (((value - 1) % 1000) + 1)"
            )
        self.connection.execute(
            """
            INSERT INTO cpk_operation_sessions
              (session_id, workspace_id, actor_id, title, status, created_at)
            SELECT 'session-foreign-' || value, 'workspace-foreign', 'operator',
                   'Foreign', 'open', '2026-08-12T00:00:00Z'::timestamptz
                     + value * interval '1 second'
            FROM generate_series(1, 1000) AS value
            """
        )
        self.connection.execute("SET session_replication_role = replica")
        try:
            self._insert_approvals(
                prefix="foreign",
                session_expression="'session-foreign-' || (((value - 1) % 1000) + 1)",
                count=foreign_count,
                offset_seconds=0,
            )
            target_scale = 1000 if target_count == 201 else 1
            self._insert_approvals(
                prefix="target",
                session_expression=target_session_expression,
                count=target_count,
                offset_seconds=target_scale,
            )
        finally:
            self.connection.execute("SET session_replication_role = origin")
        self._analyze(
            "cpk_operation_sessions",
            "cpk_approval_requests",
            "cpk_approval_decisions",
        )

    def _insert_approvals(
        self,
        *,
        prefix: str,
        session_expression: str,
        count: int,
        offset_seconds: int,
    ) -> None:
        self.connection.execute(
            f"""
            INSERT INTO cpk_approval_requests
              (request_id, session_id, plan_id, subject_kind, subject_payload,
               review_digest, requested_by, requested_at, required_scope,
               max_risk, destructive)
            SELECT 'approval-{prefix}-' || value,
                   {session_expression},
                   'plan-{prefix}-' || value,
                   'activity-plan',
                   jsonb_build_object(
                     'kind', 'activity-plan',
                     'plan_id', 'plan-{prefix}-' || value
                   ),
                   encode(
                     sha256(
                       convert_to('activity-plan:plan-{prefix}-' || value, 'UTF8')
                     ),
                     'hex'
                   ),
                   'operator',
                   '2026-08-12T00:00:00Z'::timestamptz
                     + (value * {offset_seconds or 1}) * interval '1 second',
                   'plan:approve', 'low', false
            FROM generate_series(1, {count}) AS value
            """
        )

    def _seed_runs(self) -> None:
        self.connection.execute("SET session_replication_role = replica")
        try:
            self.connection.execute(
                """
                INSERT INTO cpk_operation_sessions
                  (session_id, workspace_id, actor_id, title, status, created_at)
                VALUES ('session-target', 'workspace-target', 'operator', 'Target',
                        'open', '2026-08-12T00:00:00Z');
                INSERT INTO cpk_activity_plans
                  (plan_id, session_id, base_graph_id, desired_graph_id,
                   base_realized_projection_id, desired_realized_projection_id,
                   status, created_at, payload)
                VALUES ('plan-target', 'session-target', 'graph-a', 'graph-b',
                        'projection-a', 'projection-b', 'planned',
                        '2026-08-12T00:00:00Z', '{}'::jsonb);
                INSERT INTO cpk_execution_requests
                  (request_id, workspace_id, session_id, plan_id, status,
                   requested_by, requested_at, approval_request_id,
                   approval_decision_id, idempotency_key, intent_fingerprint)
                SELECT 'execution-' || value, 'workspace-target', 'session-target',
                       'plan-target', 'cancelled', 'operator',
                       '2026-08-12T00:00:00Z'::timestamptz
                         + value * interval '1 second',
                       'approval-' || value, 'decision-' || value,
                       'execution-' || value, 'fingerprint-' || value
                FROM generate_series(1, 10000) AS value;
                INSERT INTO cpk_activity_runs
                  (run_id, plan_id, request_id, attempt, status, created_at,
                   started_at, settled_at, metadata)
                SELECT 'run-' || value, 'plan-target', 'execution-' || value, 1,
                       'succeeded',
                       '2026-08-12T00:00:00Z'::timestamptz
                         + value * interval '1 second',
                       '2026-08-12T00:00:00Z', '2026-08-12T00:00:01Z',
                       '{}'::jsonb
                FROM generate_series(1, 10000) AS value
                """
            )
        finally:
            self.connection.execute("SET session_replication_role = origin")
        self._analyze(
            "cpk_operation_sessions",
            "cpk_activity_plans",
            "cpk_execution_requests",
            "cpk_activity_runs",
        )

    def _seed_secret_references(self) -> None:
        self.connection.execute("SET session_replication_role = replica")
        try:
            self.connection.execute(
                """
                INSERT INTO cpk_secret_references
                  (registration_id, workspace_id, secret_reference,
                   provider_registration_id, allowed_intents, admitted_by,
                   admitted_at, status)
                SELECT 'registration-' || lpad(value::text, 8, '0') || '-foreign',
                       'workspace-foreign',
                       'secret://local/foreign-' || value,
                       'provider-foreign', '[]'::jsonb, 'operator',
                       '2026-08-12T00:00:00Z'::timestamptz
                         + value * interval '1 second',
                       'active'
                FROM generate_series(1, 20000) AS value;
                INSERT INTO cpk_secret_references
                  (registration_id, workspace_id, secret_reference,
                   provider_registration_id, allowed_intents, admitted_by,
                   admitted_at, status)
                SELECT 'registration-' || lpad((value * 100)::text, 8, '0')
                         || '-target',
                       'workspace-target',
                       'secret://local/target-' || value,
                       'provider-target', '[]'::jsonb, 'operator',
                       '2026-08-12T00:00:00Z'::timestamptz
                         + value * interval '1 second',
                       'active'
                FROM generate_series(1, 201) AS value
                """
            )
        finally:
            self.connection.execute("SET session_replication_role = origin")
        self._analyze("cpk_secret_references")

    def _seed_actions(self) -> None:
        self.connection.execute("SET session_replication_role = replica")
        try:
            self.connection.execute(
                """
                INSERT INTO cpk_operation_actions
                  (action_id, session_id, ordinal, action_type, actor_id,
                   payload, created_at)
                SELECT 'action-' || value, 'session-target', value,
                       'record-operation-action', 'operator', '{}'::jsonb,
                       '2026-08-12T00:00:00Z'::timestamptz
                         + value * interval '1 second'
                FROM generate_series(1, 10000) AS value
                """
            )
        finally:
            self.connection.execute("SET session_replication_role = origin")
        self._analyze("cpk_operation_actions")

    def _seed_events(self) -> None:
        self.connection.execute("SET session_replication_role = replica")
        try:
            self.connection.execute(
                """
                INSERT INTO cpk_activity_events
                  (event_id, run_id, ordinal, event_type, occurred_at, payload)
                SELECT 'event-' || value, 'run-target', value, 'run_started',
                       '2026-08-12T00:00:00Z'::timestamptz
                         + value * interval '1 second',
                       '{}'::jsonb
                FROM generate_series(1, 10000) AS value
                """
            )
        finally:
            self.connection.execute("SET session_replication_role = origin")
        self._analyze("cpk_activity_events")

    def _seed_observations(self) -> None:
        self.connection.execute("SET session_replication_role = replica")
        try:
            self.connection.execute(
                """
                INSERT INTO cpk_observations
                  (observation_id, workspace_id, subject_id, status, observed_at,
                   evidence, freshness)
                SELECT 'observation-' || subject || '-' || revision,
                       'workspace-target',
                       'subject-' || lpad(subject::text, 6, '0'),
                       'healthy',
                       '2026-08-12T00:00:00Z'::timestamptz
                         + revision * interval '1 second',
                       '{}'::jsonb, 'fresh'
                FROM generate_series(1, 5000) AS subject
                CROSS JOIN generate_series(1, 3) AS revision
                """
            )
        finally:
            self.connection.execute("SET session_replication_role = origin")
        self._analyze("cpk_observations")

    def _seed_delegation_keys(self) -> None:
        self.connection.execute("SET session_replication_role = replica")
        try:
            self.connection.execute(
                """
                INSERT INTO cpk_delegation_signing_keys
                  (registration_id, workspace_id, purpose, issuer, key_id,
                   algorithm, public_key_pem, public_fingerprint_sha256,
                   private_key_reference, admitted_by, admitted_at, status)
                SELECT 'dkey_' || encode(
                         sha256(convert_to('key-' || value, 'UTF8')), 'hex'
                       ),
                       'workspace-target', 'gateway-probe',
                       'issuer-' || ((value - 1) % 100),
                       'key-' || lpad(value::text, 6, '0'), 'ed25519',
                       'public-key',
                       encode(sha256(convert_to('public-' || value, 'UTF8')), 'hex'),
                       'secret://local/private-' || value,
                       'operator', '2026-08-12T00:00:00Z', 'verify-only'
                FROM generate_series(1, 10000) AS value
                """
            )
        finally:
            self.connection.execute("SET session_replication_role = origin")
        self._analyze("cpk_delegation_signing_keys")

    def _seed_gateway_probes(self) -> None:
        self.connection.execute("SET session_replication_role = replica")
        try:
            self.connection.execute(
                """
                INSERT INTO cpk_gateway_probe_attempts
                  (probe_id, workspace_id, request_id, actor_id, current_graph_id,
                   gateway_node_id, gateway_runtime_id, access_path, probe_kind,
                   target_id, request_digest, issuer, key_id, audience, grant_jti,
                   issued_at, expires_at, status, requested_at, intent_fingerprint,
                   evidence)
                SELECT 'probe-' || value, 'workspace-target',
                       'request-' || value, 'operator', 'graph-target',
                       'gateway-node', 'gateway-runtime', 'runtime-private',
                       'http-status', 'target-' || value,
                       encode(sha256(convert_to('request-' || value, 'UTF8')), 'hex'),
                       'issuer', 'key', 'gateway', 'grant-' || value,
                       value, value + 300, 'intended',
                       '2026-08-12T00:00:00Z'::timestamptz
                         + value * interval '1 second',
                       'fingerprint-' || value, '{}'::jsonb
                FROM generate_series(1, 10000) AS value
                """
            )
        finally:
            self.connection.execute("SET session_replication_role = origin")
        self._analyze("cpk_gateway_probe_attempts")


if __name__ == "__main__":
    unittest.main()
