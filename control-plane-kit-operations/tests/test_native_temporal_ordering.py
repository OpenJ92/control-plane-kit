from __future__ import annotations

import os
import unittest
import uuid

import psycopg

from tests.graph_lineage_fixture import seed_identity_graphs
import control_plane_kit_operations.postgres as postgres
from control_plane_kit_core.approval_subjects import ActivityPlanApprovalSubject
from control_plane_kit_core.operations.lifecycle import ActivityRunStatus
from control_plane_kit_core.planning import ActivityPlan, RiskLevel
from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_core.public_ingress import IngressAuthorityReference
from control_plane_kit_core.runtime_authority import (
    RuntimeAuthorityAccessDelivery,
    RuntimeAuthorityAccessDeliveryKind,
    RuntimeAuthorityReference,
)
from control_plane_kit_core.secrets import SecretReference
from control_plane_kit_core.types import RuntimeKind
from control_plane_kit_operations.ingress_authorities import (
    CloudflareZoneIngressAuthority,
)
from control_plane_kit_operations.postgres.activity_history import (
    PostgresActivityHistoryStore,
)
from control_plane_kit_operations.postgres.execution import PostgresExecutionStore
from control_plane_kit_operations.postgres.observed_state import (
    PostgresObservedStateStore,
)
from control_plane_kit_operations.postgres.ingress_authority_store import (
    IngressAuthorityStore,
)
from control_plane_kit_operations.postgres.runtime_authority_store import (
    RuntimeAuthorityDeliveryStore,
    RuntimeAuthorityStore,
)
from control_plane_kit_operations.postgres.stores import PostgresStoreBundle
from control_plane_kit_operations.records import (
    ActivityPlanRecord,
    ActivityPlanStatus,
    ApprovalRequestRecord,
    ObservationFreshness,
    ObservationRecord,
    ObservationStatus,
    OperationSessionRecord,
    OperationSessionStatus,
)
from control_plane_kit_operations.runtime_authorities import (
    LocalDockerSocketAuthority,
)


_EARLIER = "2026-08-07T06:00:00Z"
_LATER = "2026-08-07T06:00:00.000001Z"


class NativeTemporalOrderingTests(unittest.TestCase):
    def setUp(self) -> None:
        database_url = os.environ.get("CPK_OPERATIONS_TEST_DATABASE_URL")
        if not database_url:
            raise RuntimeError(
                "CPK_OPERATIONS_TEST_DATABASE_URL is required; use Docker-first tests"
            )
        self.schema = f"native_time_{uuid.uuid4().hex}"
        self.connection = psycopg.connect(database_url, autocommit=True)
        self.connection.execute(f'CREATE SCHEMA "{self.schema}"')
        self.connection.execute(f'SET search_path TO "{self.schema}"')

    def tearDown(self) -> None:
        self.connection.execute("SET search_path TO public")
        self.connection.execute(f'DROP SCHEMA IF EXISTS "{self.schema}" CASCADE')
        self.connection.close()

    def test_v2_changes_text_order_into_native_instant_order(self) -> None:
        self.connection.execute(postgres.POSTGRES_SCHEMA)
        self.connection.execute(
            """
            INSERT INTO cpk_workspaces (workspace_id, name, lifecycle)
            VALUES ('workspace-a', 'Workspace A', 'created');
            INSERT INTO cpk_operation_sessions
              (session_id, workspace_id, actor_id, title, status, created_at)
            VALUES
              ('session-micro', 'workspace-a', 'operator-a', 'Micro', 'open',
               '2026-08-07T06:00:00.000001Z'),
              ('session-second', 'workspace-a', 'operator-a', 'Second', 'open',
               '2026-08-07T06:00:00Z');
            """
        )

        self.assertEqual(
            [row[0] for row in self.connection.execute(
                "SELECT session_id FROM cpk_operation_sessions ORDER BY created_at"
            ).fetchall()],
            ["session-micro", "session-second"],
        )

        postgres.install_postgres_schema(self.connection)
        self.connection.execute("SET TIME ZONE 'America/New_York'")
        records = PostgresActivityHistoryStore(
            self.connection
        ).sessions_for_workspace("workspace-a")

        self.assertEqual(
            [(record.session_id, record.created_at) for record in records],
            [
                ("session-second", _EARLIER),
                ("session-micro", _LATER),
            ],
        )

    def test_v2_public_selectors_use_native_time_then_documented_identity(self) -> None:
        postgres.install_postgres_schema(self.connection)
        self.connection.execute("SET TIME ZONE 'Asia/Tokyo'")
        self.connection.execute(
            """
            INSERT INTO cpk_workspaces (workspace_id, name, lifecycle)
            VALUES ('workspace-a', 'Workspace A', 'created')
            """
        )
        history = PostgresActivityHistoryStore(self.connection)
        lineage = seed_identity_graphs(
            PostgresStoreBundle(self.connection),
            workspace_id="workspace-a",
            graph_ids=("graph-a", "graph-b"),
        )

        for identity, timestamp in (
            ("session-earlier", _EARLIER),
            ("session-later-a", _LATER),
            ("session-later-z", _LATER),
        ):
            history.add_session(
                OperationSessionRecord(
                    session_id=identity,
                    workspace_id="workspace-a",
                    actor_id="operator-a",
                    title=identity,
                    status=OperationSessionStatus.OPEN,
                    created_at=timestamp,
                )
            )
        self.assertEqual(
            [
                (record.session_id, record.created_at)
                for record in history.sessions_for_workspace("workspace-a")
            ],
            [
                ("session-earlier", _EARLIER),
                ("session-later-a", _LATER),
                ("session-later-z", _LATER),
            ],
        )

        for identity, timestamp in (
            ("plan-earlier", _EARLIER),
            ("plan-later-a", _LATER),
            ("plan-later-z", _LATER),
        ):
            history.add_plan(
                ActivityPlanRecord(
                    plan_id=identity,
                    session_id="session-earlier",
                    base_graph_id="graph-a",
                    desired_graph_id="graph-b",
                    status=ActivityPlanStatus.PLANNED,
                    created_at=timestamp,
                    plan=ActivityPlan(()),
                    base_realized_projection_id=lineage["graph-a"],
                    desired_realized_projection_id=lineage["graph-b"],
                )
            )
        self.assertEqual(
            [
                (record.plan_id, record.created_at)
                for record in history.plans_for_session("session-earlier")
            ],
            [
                ("plan-earlier", _EARLIER),
                ("plan-later-a", _LATER),
                ("plan-later-z", _LATER),
            ],
        )

        for identity, timestamp in (
            ("approval-earlier", _EARLIER),
            ("approval-later-a", _LATER),
            ("approval-later-z", _LATER),
        ):
            history.add_approval_request(
                ApprovalRequestRecord(
                    request_id=identity,
                    session_id="session-earlier",
                    subject=ActivityPlanApprovalSubject("plan-earlier"),
                    requested_by="operator-a",
                    requested_at=timestamp,
                    required_scope=PolicyScope.PLAN_APPROVE,
                    max_risk=RiskLevel.INFORMATIONAL,
                    destructive=False,
                )
            )
        self.assertEqual(
            [
                (record.request_id, record.requested_at)
                for record in history.approval_requests_for_session("session-earlier")
            ],
            [
                ("approval-earlier", _EARLIER),
                ("approval-later-a", _LATER),
                ("approval-later-z", _LATER),
            ],
        )

        self._seed_runs()
        runs = PostgresExecutionStore(self.connection).runs_for_plan("plan-earlier")
        self.assertEqual(
            [(record.run_id, record.created_at) for record in runs],
            [
                ("run-earlier", _EARLIER),
                ("run-later-a", _LATER),
                ("run-later-z", _LATER),
            ],
        )

        observed = PostgresObservedStateStore(self.connection)
        for identity, timestamp in (
            ("observation-earlier", _EARLIER),
            ("observation-later-a", _LATER),
            ("observation-later-z", _LATER),
        ):
            observed.put(
                ObservationRecord(
                    observation_id=identity,
                    workspace_id="workspace-a",
                    subject_id="runtime-a",
                    status=ObservationStatus.HEALTHY,
                    observed_at=timestamp,
                    freshness=ObservationFreshness.FRESH,
                )
            )
        latest = observed.latest("workspace-a", "runtime-a")
        self.assertEqual(
            (latest.observation_id, latest.observed_at),
            ("observation-later-z", _LATER),
        )
        self.assertEqual(
            [
                (record.observation_id, record.observed_at)
                for record in observed.latest_for_workspace("workspace-a")
            ],
            [("observation-later-z", _LATER)],
        )
        self.assertEqual(
            [
                (record.observation_id, record.observed_at)
                for record in observed.history("workspace-a", "runtime-a")
            ],
            [
                ("observation-earlier", _EARLIER),
                ("observation-later-a", _LATER),
                ("observation-later-z", _LATER),
            ],
        )
        self.assertNotIn(
            ".000000Z",
            repr(
                tuple(
                    record.observed_at
                    for record in observed.history("workspace-a", "runtime-a")
                )
            ),
        )

    def test_v3_authority_gets_apply_status_time_and_identity_precedence(self) -> None:
        postgres.install_postgres_schema(self.connection)
        self.connection.execute("SET TIME ZONE 'Pacific/Honolulu'")
        self.connection.execute(
            """
            INSERT INTO cpk_workspaces (workspace_id, name, lifecycle)
            VALUES ('workspace-a', 'Workspace A', 'created')
            """
        )

        cases = (
            (
                "ingress",
                "cpk_ingress_authorities",
                "registration_id",
                self._register_ingress,
                lambda reference: IngressAuthorityStore(self.connection).get(
                    "workspace-a", IngressAuthorityReference(reference)
                ),
            ),
            (
                "runtime",
                "cpk_runtime_authorities",
                "registration_id",
                self._register_runtime,
                lambda reference: RuntimeAuthorityStore(self.connection).get(
                    "workspace-a", RuntimeAuthorityReference(reference)
                ),
            ),
            (
                "delivery",
                "cpk_runtime_authority_deliveries",
                "delivery_id",
                self._register_delivery,
                lambda reference: RuntimeAuthorityDeliveryStore(self.connection).get(
                    "workspace-a", RuntimeAuthorityReference(reference)
                ),
            ),
        )

        for prefix, table, identity_column, register, get in cases:
            with self.subTest(table=table, law="native instant"):
                reference = f"{prefix}-mixed"
                original_id = register(reference, _EARLIER)
                self._replace_with_pair(
                    table,
                    identity_column,
                    original_id,
                    "row-earlier",
                    "row-later",
                    _EARLIER,
                    _LATER,
                    "revoked",
                )
                selected = get(reference)
                self.assertEqual(getattr(selected, identity_column), "row-later")
                self.assertEqual(selected.admitted_at, _LATER)

            with self.subTest(table=table, law="identity tie break"):
                reference = f"{prefix}-tie"
                original_id = register(reference, _LATER)
                self._replace_with_pair(
                    table,
                    identity_column,
                    original_id,
                    "row-a",
                    "row-z",
                    _LATER,
                    _LATER,
                    "revoked",
                )
                selected = get(reference)
                self.assertEqual(getattr(selected, identity_column), "row-z")
                self.assertEqual(selected.admitted_at, _LATER)

            with self.subTest(table=table, law="active status"):
                reference = f"{prefix}-status"
                original_id = register(reference, _EARLIER)
                self.connection.execute(
                    f"""
                    INSERT INTO {table}
                    SELECT 'row-newer-revoked', workspace_id, authority_ref,
                           {self._copy_columns(table)}, admitted_by, %s, 'revoked', metadata
                    FROM {table}
                    WHERE {identity_column} = %s
                    """,
                    (_LATER, original_id),
                )
                selected = get(reference)
                self.assertEqual(getattr(selected, identity_column), original_id)
                self.assertEqual(selected.admitted_at, _EARLIER)

    def _seed_runs(self) -> None:
        self.connection.execute(
            """
            INSERT INTO cpk_approval_decisions
              (decision_id, request_id, actor_id, decision, scope, decided_at)
            SELECT 'decision-' || request_id, request_id, 'manager-a',
                   'approved', 'plan:approve', requested_at
            FROM cpk_approval_requests
            """
        )
        self.connection.execute(
            """
            INSERT INTO cpk_execution_requests
              (request_id, workspace_id, session_id, plan_id, status,
               requested_by, requested_at, approval_request_id,
               approval_decision_id, idempotency_key, intent_fingerprint)
            VALUES
              ('request-earlier', 'workspace-a', 'session-earlier', 'plan-earlier',
               'cancelled', 'operator-a', %s, 'approval-earlier',
               'decision-approval-earlier', 'key-earlier', 'fingerprint-earlier'),
              ('request-later-a', 'workspace-a', 'session-earlier', 'plan-earlier',
               'cancelled', 'operator-a', %s, 'approval-later-a',
               'decision-approval-later-a', 'key-later-a', 'fingerprint-later-a'),
              ('request-later-z', 'workspace-a', 'session-earlier', 'plan-earlier',
               'cancelled', 'operator-a', %s, 'approval-later-z',
               'decision-approval-later-z', 'key-later-z', 'fingerprint-later-z')
            """,
            (_EARLIER, _LATER, _LATER),
        )
        self.connection.execute(
            """
            INSERT INTO cpk_activity_runs
              (run_id, plan_id, request_id, attempt, status, created_at, metadata)
            VALUES
              ('run-earlier', 'plan-earlier', 'request-earlier', 1, 'claimed', %s, '{}'),
              ('run-later-a', 'plan-earlier', 'request-later-a', 1, 'claimed', %s, '{}'),
              ('run-later-z', 'plan-earlier', 'request-later-z', 1, 'claimed', %s, '{}')
            """,
            (_EARLIER, _LATER, _LATER),
        )

    def _register_ingress(self, reference: str, timestamp: str) -> str:
        record = IngressAuthorityStore(self.connection).register(
            workspace_id="workspace-a",
            authority_ref=IngressAuthorityReference(reference),
            authority=CloudflareZoneIngressAuthority(
                account_id="account-openj92",
                zone_id="zone-openj92",
                zone_name="openj92.dev",
                api_token_ref=SecretReference("secret://cloudflare/openj92/api-token"),
                allowed_hostname_pattern="cpk-gateway-*.openj92.dev",
                generated_secret_provider_registration_id="sprov-generated-ingress",
                generated_secret_reference_prefix=SecretReference(
                    "secret://generated/ingress"
                ),
            ),
            admitted_by="operator-a",
            admitted_at=timestamp,
        )
        return record.registration_id

    def _register_runtime(self, reference: str, timestamp: str) -> str:
        record = RuntimeAuthorityStore(self.connection).register(
            workspace_id="workspace-a",
            authority_ref=RuntimeAuthorityReference(reference),
            runtime_kind=RuntimeKind.DOCKER,
            authority=LocalDockerSocketAuthority(),
            admitted_by="operator-a",
            admitted_at=timestamp,
        )
        return record.registration_id

    def _register_delivery(self, reference: str, timestamp: str) -> str:
        self._register_runtime(reference, timestamp)
        record = RuntimeAuthorityDeliveryStore(self.connection).register(
            workspace_id="workspace-a",
            delivery=RuntimeAuthorityAccessDelivery(
                authority_ref=RuntimeAuthorityReference(reference),
                delivery_kind=(
                    RuntimeAuthorityAccessDeliveryKind.LOCAL_DOCKER_SOCKET_MOUNT
                ),
            ),
            admitted_by="operator-a",
            admitted_at=timestamp,
        )
        return record.delivery_id

    def _replace_with_pair(
        self,
        table: str,
        identity_column: str,
        original_id: str,
        first_id: str,
        second_id: str,
        first_time: str,
        second_time: str,
        status: str,
    ) -> None:
        self.connection.execute(
            f"UPDATE {table} SET {identity_column} = %s, admitted_at = %s, status = %s "
            f"WHERE {identity_column} = %s",
            (first_id, first_time, status, original_id),
        )
        self.connection.execute(
            f"""
            INSERT INTO {table}
            SELECT %s, workspace_id, authority_ref,
                   {self._copy_columns(table)}, admitted_by, %s, %s, metadata
            FROM {table}
            WHERE {identity_column} = %s
            """,
            (second_id, second_time, status, first_id),
        )

    @staticmethod
    def _copy_columns(table: str) -> str:
        return {
            "cpk_ingress_authorities": (
                "provider_kind, authority, credential_references, "
                "allowed_hostname_pattern"
            ),
            "cpk_runtime_authorities": (
                "runtime_kind, authority_kind, authority, credential_references"
            ),
            "cpk_runtime_authority_deliveries": (
                "delivery_kind, delivery, secret_references"
            ),
        }[table]


if __name__ == "__main__":
    unittest.main()
