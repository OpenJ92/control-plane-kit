"""Postgres schema and adapters for durable control-plane stores.

The first implementation is intentionally direct: each store owns explicit
tables and transactions are supplied by the caller through the connection.  The
module does not import a Postgres driver at import time, so the base package can
still be used for algebra without a database dependency.  Store tests run
against a real Postgres service.
"""

from __future__ import annotations

import json
from dataclasses import replace
from typing import Any, Protocol

from control_plane_kit.planning.activity_plan import RiskLevel
from control_plane_kit.planning.codec import DEFAULT_ACTIVITY_PLAN_CODEC
from control_plane_kit.stores.records import (
    ActivityEventRecord,
    ActivityPlanRecord,
    ActivityRunRecord,
    ApprovalDecisionKind,
    ApprovalDecisionRecord,
    ApprovalRequestRecord,
    GraphVersionRecord,
    InstanceRecord,
    ObservationRecord,
    OperationActionKind,
    OperationActionRecord,
    OperationSessionRecord,
    OperationSessionStatus,
    SecretReferenceRecord,
    WorkspaceLifecycle,
    WorkspaceRecord,
)


POSTGRES_SCHEMA = """
CREATE TABLE IF NOT EXISTS cpk_workspaces (
  workspace_id text PRIMARY KEY,
  name text NOT NULL,
  lifecycle text NOT NULL,
  current_graph_id text,
  desired_graph_id text,
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS cpk_graph_versions (
  graph_id text PRIMARY KEY,
  workspace_id text NOT NULL REFERENCES cpk_workspaces(workspace_id),
  version integer NOT NULL,
  graph_descriptor jsonb NOT NULL,
  created_by text NOT NULL,
  created_at text NOT NULL,
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
  UNIQUE (workspace_id, version)
);

CREATE TABLE IF NOT EXISTS cpk_operation_sessions (
  session_id text PRIMARY KEY,
  workspace_id text NOT NULL,
  actor_id text NOT NULL,
  title text NOT NULL,
  status text NOT NULL,
  created_at text NOT NULL,
  closed_at text,
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
  idempotency_key text,
  intent_fingerprint text
);

CREATE TABLE IF NOT EXISTS cpk_operation_actions (
  action_id text PRIMARY KEY,
  session_id text NOT NULL REFERENCES cpk_operation_sessions(session_id),
  ordinal integer NOT NULL,
  action_type text NOT NULL,
  actor_id text NOT NULL,
  payload jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at text NOT NULL,
  idempotency_key text,
  intent_fingerprint text,
  UNIQUE (session_id, ordinal)
);

CREATE TABLE IF NOT EXISTS cpk_activity_plans (
  plan_id text PRIMARY KEY,
  session_id text NOT NULL REFERENCES cpk_operation_sessions(session_id),
  base_graph_id text NOT NULL,
  desired_graph_id text NOT NULL,
  status text NOT NULL,
  created_at text NOT NULL,
  payload jsonb NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS cpk_approval_requests (
  request_id text PRIMARY KEY,
  session_id text NOT NULL REFERENCES cpk_operation_sessions(session_id),
  plan_id text NOT NULL REFERENCES cpk_activity_plans(plan_id),
  requested_by text NOT NULL,
  requested_at text NOT NULL,
  required_scope text NOT NULL,
  max_risk text NOT NULL,
  destructive boolean NOT NULL,
  comment text,
  idempotency_key text,
  intent_fingerprint text
);

CREATE TABLE IF NOT EXISTS cpk_approval_decisions (
  decision_id text PRIMARY KEY,
  request_id text NOT NULL UNIQUE REFERENCES cpk_approval_requests(request_id),
  actor_id text NOT NULL,
  decision text NOT NULL,
  scope text NOT NULL,
  decided_at text NOT NULL,
  comment text,
  idempotency_key text,
  intent_fingerprint text
);

CREATE TABLE IF NOT EXISTS cpk_activity_runs (
  run_id text PRIMARY KEY,
  plan_id text NOT NULL REFERENCES cpk_activity_plans(plan_id),
  status text NOT NULL,
  started_at text NOT NULL,
  finished_at text,
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS cpk_activity_events (
  event_id text PRIMARY KEY,
  run_id text NOT NULL REFERENCES cpk_activity_runs(run_id),
  ordinal integer NOT NULL,
  event_type text NOT NULL,
  occurred_at text NOT NULL,
  payload jsonb NOT NULL DEFAULT '{}'::jsonb,
  UNIQUE (run_id, ordinal)
);

CREATE TABLE IF NOT EXISTS cpk_observations (
  observation_id text PRIMARY KEY,
  workspace_id text NOT NULL,
  subject_id text NOT NULL,
  status text NOT NULL,
  observed_at text NOT NULL,
  payload jsonb NOT NULL DEFAULT '{}'::jsonb,
  stale boolean NOT NULL DEFAULT false
);

CREATE TABLE IF NOT EXISTS cpk_instances (
  instance_id text PRIMARY KEY,
  owner_id text NOT NULL,
  lifecycle text NOT NULL,
  endpoint text,
  wake_hint text,
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS cpk_secret_references (
  secret_ref text PRIMARY KEY,
  owner_id text NOT NULL,
  purpose text NOT NULL,
  assigned_at text NOT NULL,
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb
);

ALTER TABLE cpk_operation_sessions
  ADD COLUMN IF NOT EXISTS idempotency_key text,
  ADD COLUMN IF NOT EXISTS intent_fingerprint text;

ALTER TABLE cpk_operation_actions
  ADD COLUMN IF NOT EXISTS idempotency_key text,
  ADD COLUMN IF NOT EXISTS intent_fingerprint text;

CREATE UNIQUE INDEX IF NOT EXISTS cpk_operation_sessions_idempotency
  ON cpk_operation_sessions (workspace_id, idempotency_key)
  WHERE idempotency_key IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS cpk_operation_actions_idempotency
  ON cpk_operation_actions (session_id, idempotency_key)
  WHERE idempotency_key IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS cpk_approval_requests_idempotency
  ON cpk_approval_requests (session_id, idempotency_key)
  WHERE idempotency_key IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS cpk_approval_decisions_idempotency
  ON cpk_approval_decisions (request_id, idempotency_key)
  WHERE idempotency_key IS NOT NULL;
"""


class PostgresConnection(Protocol):
    """Small connection protocol satisfied by psycopg connections."""

    def execute(self, query: str, params: tuple[object, ...] = ()) -> Any: ...


def install_schema(connection: PostgresConnection) -> None:
    """Install the durable store schema on a caller-managed connection."""

    connection.execute(POSTGRES_SCHEMA)


def _json(value: object) -> str:
    return json.dumps(value, sort_keys=True)


def _record(row: tuple[Any, ...] | None, kind: str, key: str) -> tuple[Any, ...]:
    if row is None:
        raise KeyError(f"missing {kind} {key!r}")
    return row


def _session_record(row: tuple[Any, ...]) -> OperationSessionRecord:
    return OperationSessionRecord(
        session_id=row[0],
        workspace_id=row[1],
        actor_id=row[2],
        title=row[3],
        status=OperationSessionStatus(row[4]),
        created_at=row[5],
        closed_at=row[6],
        metadata=row[7],
        idempotency_key=row[8],
        intent_fingerprint=row[9],
    )


def _action_record(row: tuple[Any, ...]) -> OperationActionRecord:
    return OperationActionRecord(
        action_id=row[0],
        session_id=row[1],
        ordinal=row[2],
        action_type=OperationActionKind(row[3]),
        actor_id=row[4],
        payload=row[5],
        created_at=row[6],
        idempotency_key=row[7],
        intent_fingerprint=row[8],
    )


def _approval_request_record(row: tuple[Any, ...]) -> ApprovalRequestRecord:
    return ApprovalRequestRecord(
        request_id=row[0],
        session_id=row[1],
        plan_id=row[2],
        requested_by=row[3],
        requested_at=row[4],
        required_scope=row[5],
        max_risk=RiskLevel(row[6]),
        destructive=row[7],
        comment=row[8],
        idempotency_key=row[9],
        intent_fingerprint=row[10],
    )


def _approval_decision_record(row: tuple[Any, ...]) -> ApprovalDecisionRecord:
    return ApprovalDecisionRecord(
        decision_id=row[0],
        request_id=row[1],
        actor_id=row[2],
        decision=ApprovalDecisionKind(row[3]),
        scope=row[4],
        decided_at=row[5],
        comment=row[6],
        idempotency_key=row[7],
        intent_fingerprint=row[8],
    )


class PostgresWorkspaceStore:
    """Postgres-backed workspace truth store."""

    def __init__(self, connection: PostgresConnection) -> None:
        self._connection = connection

    def create(self, record: WorkspaceRecord) -> WorkspaceRecord:
        self._connection.execute(
            """
            INSERT INTO cpk_workspaces
              (workspace_id, name, lifecycle, current_graph_id, desired_graph_id, metadata)
            VALUES (%s, %s, %s, %s, %s, %s::jsonb)
            """,
            (
                record.workspace_id,
                record.name,
                record.lifecycle.value,
                record.current_graph_id,
                record.desired_graph_id,
                _json(record.metadata),
            ),
        )
        return record

    def get(self, workspace_id: str) -> WorkspaceRecord:
        return self._get(workspace_id, for_update=False)

    def get_for_update(self, workspace_id: str) -> WorkspaceRecord:
        """Lock one workspace truth row for the caller-owned transaction."""

        return self._get(workspace_id, for_update=True)

    def _get(self, workspace_id: str, *, for_update: bool) -> WorkspaceRecord:
        lock = " FOR UPDATE" if for_update else ""
        row = _record(
            self._connection.execute(
                f"""
                SELECT workspace_id, name, lifecycle, current_graph_id, desired_graph_id, metadata
                FROM cpk_workspaces WHERE workspace_id = %s{lock}
                """,
                (workspace_id,),
            ).fetchone(),
            "workspace",
            workspace_id,
        )
        return WorkspaceRecord(
            workspace_id=row[0],
            name=row[1],
            lifecycle=WorkspaceLifecycle(row[2]),
            current_graph_id=row[3],
            desired_graph_id=row[4],
            metadata=row[5],
        )

    def set_lifecycle(self, workspace_id: str, lifecycle: WorkspaceLifecycle) -> WorkspaceRecord:
        record = replace(self.get(workspace_id), lifecycle=lifecycle)
        self._connection.execute(
            "UPDATE cpk_workspaces SET lifecycle = %s WHERE workspace_id = %s",
            (lifecycle.value, workspace_id),
        )
        return record

    def set_current_graph(self, workspace_id: str, graph_id: str) -> WorkspaceRecord:
        record = replace(self.get(workspace_id), current_graph_id=graph_id)
        self._connection.execute(
            "UPDATE cpk_workspaces SET current_graph_id = %s WHERE workspace_id = %s",
            (graph_id, workspace_id),
        )
        return record

    def set_desired_graph(self, workspace_id: str, graph_id: str) -> WorkspaceRecord:
        record = replace(self.get(workspace_id), desired_graph_id=graph_id)
        self._connection.execute(
            "UPDATE cpk_workspaces SET desired_graph_id = %s WHERE workspace_id = %s",
            (graph_id, workspace_id),
        )
        return record


class PostgresGraphTopologyStore:
    """Postgres-backed graph topology store using descriptor payloads."""

    def __init__(self, connection: PostgresConnection) -> None:
        self._connection = connection

    def save(self, record: GraphVersionRecord) -> GraphVersionRecord:
        self._connection.execute(
            """
            INSERT INTO cpk_graph_versions
              (graph_id, workspace_id, version, graph_descriptor, created_by, created_at, metadata)
            VALUES (%s, %s, %s, %s::jsonb, %s, %s, %s::jsonb)
            """,
            (
                record.graph_id,
                record.workspace_id,
                record.version,
                _json(record.graph_descriptor),
                record.created_by,
                record.created_at,
                _json(record.metadata),
            ),
        )
        return record

    def get(self, graph_id: str) -> GraphVersionRecord:
        row = _record(
            self._connection.execute(
                """
                SELECT graph_id, workspace_id, version, graph_descriptor, created_by, created_at, metadata
                FROM cpk_graph_versions WHERE graph_id = %s
                """,
                (graph_id,),
            ).fetchone(),
            "graph",
            graph_id,
        )
        return GraphVersionRecord(
            graph_id=row[0],
            workspace_id=row[1],
            version=row[2],
            graph_descriptor=row[3],
            created_by=row[4],
            created_at=row[5],
            metadata=row[6],
        )

    def latest_for_workspace(self, workspace_id: str) -> GraphVersionRecord | None:
        row = self._connection.execute(
            """
            SELECT graph_id, workspace_id, version, graph_descriptor, created_by, created_at, metadata
            FROM cpk_graph_versions
            WHERE workspace_id = %s
            ORDER BY version DESC
            LIMIT 1
            """,
            (workspace_id,),
        ).fetchone()
        if row is None:
            return None
        return GraphVersionRecord(
            graph_id=row[0],
            workspace_id=row[1],
            version=row[2],
            graph_descriptor=row[3],
            created_by=row[4],
            created_at=row[5],
            metadata=row[6],
        )

    def next_version_for_workspace(self, workspace_id: str) -> int:
        """Allocate the next version while the command holds the workspace row lock."""

        row = self._connection.execute(
            """
            SELECT COALESCE(MAX(version), 0) + 1
            FROM cpk_graph_versions
            WHERE workspace_id = %s
            """,
            (workspace_id,),
        ).fetchone()
        return int(row[0])


class PostgresSecretReferenceStore:
    """Postgres-backed secret reference store with no secret-value column."""

    def __init__(self, connection: PostgresConnection) -> None:
        self._connection = connection

    def assign(self, record: SecretReferenceRecord) -> SecretReferenceRecord:
        self._connection.execute(
            """
            INSERT INTO cpk_secret_references
              (secret_ref, owner_id, purpose, assigned_at, metadata)
            VALUES (%s, %s, %s, %s, %s::jsonb)
            """,
            (record.secret_ref, record.owner_id, record.purpose, record.assigned_at, _json(record.metadata)),
        )
        return record

    def get(self, secret_ref: str) -> SecretReferenceRecord:
        row = _record(
            self._connection.execute(
                """
                SELECT secret_ref, owner_id, purpose, assigned_at, metadata
                FROM cpk_secret_references WHERE secret_ref = %s
                """,
                (secret_ref,),
            ).fetchone(),
            "secret reference",
            secret_ref,
        )
        return SecretReferenceRecord(
            secret_ref=row[0],
            owner_id=row[1],
            purpose=row[2],
            assigned_at=row[3],
            metadata=row[4],
        )

    def exists(self, secret_ref: str) -> bool:
        row = self._connection.execute(
            "SELECT 1 FROM cpk_secret_references WHERE secret_ref = %s",
            (secret_ref,),
        ).fetchone()
        return row is not None


class PostgresStoreBundle:
    """Convenience bundle for the first durable store target."""

    def __init__(self, connection: PostgresConnection) -> None:
        self.workspace = PostgresWorkspaceStore(connection)
        self.graph_topology = PostgresGraphTopologyStore(connection)
        self.activity_history = PostgresActivityHistoryStore(connection)
        self.observed_state = PostgresObservedStateStore(connection)
        self.instance_registry = PostgresInstanceRegistryStore(connection)
        self.secret_references = PostgresSecretReferenceStore(connection)


class PostgresActivityHistoryStore:
    """Postgres-backed activity history store."""

    def __init__(self, connection: PostgresConnection) -> None:
        self._connection = connection

    def add_session(self, record: OperationSessionRecord) -> OperationSessionRecord:
        self._connection.execute(
            """
            INSERT INTO cpk_operation_sessions
              (session_id, workspace_id, actor_id, title, status, created_at, closed_at,
               metadata, idempotency_key, intent_fingerprint)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s)
            """,
            (
                record.session_id,
                record.workspace_id,
                record.actor_id,
                record.title,
                record.status.value,
                record.created_at,
                record.closed_at,
                _json(record.metadata),
                record.idempotency_key,
                record.intent_fingerprint,
            ),
        )
        return record

    def lock_session_idempotency(self, workspace_id: str, idempotency_key: str) -> None:
        """Serialize starts before a session row exists in this transaction."""

        self._connection.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
            (f"operation-session:{workspace_id}:{idempotency_key}",),
        )

    def get_session(self, session_id: str) -> OperationSessionRecord:
        row = _record(
            self._connection.execute(
                """
                SELECT session_id, workspace_id, actor_id, title, status, created_at, closed_at,
                       metadata, idempotency_key, intent_fingerprint
                FROM cpk_operation_sessions WHERE session_id = %s
                """,
                (session_id,),
            ).fetchone(),
            "session",
            session_id,
        )
        return OperationSessionRecord(
            session_id=row[0],
            workspace_id=row[1],
            actor_id=row[2],
            title=row[3],
            status=OperationSessionStatus(row[4]),
            created_at=row[5],
            closed_at=row[6],
            metadata=row[7],
            idempotency_key=row[8],
            intent_fingerprint=row[9],
        )

    def session_for_idempotency(
        self, workspace_id: str, idempotency_key: str
    ) -> OperationSessionRecord | None:
        row = self._connection.execute(
            """
            SELECT session_id, workspace_id, actor_id, title, status, created_at, closed_at,
                   metadata, idempotency_key, intent_fingerprint
            FROM cpk_operation_sessions
            WHERE workspace_id = %s AND idempotency_key = %s
            """,
            (workspace_id, idempotency_key),
        ).fetchone()
        return None if row is None else _session_record(row)

    def sessions_for_workspace(self, workspace_id: str) -> tuple[OperationSessionRecord, ...]:
        rows = self._connection.execute(
            """
            SELECT session_id, workspace_id, actor_id, title, status, created_at, closed_at,
                   metadata, idempotency_key, intent_fingerprint
            FROM cpk_operation_sessions
            WHERE workspace_id = %s
            ORDER BY created_at ASC, session_id ASC
            """,
            (workspace_id,),
        ).fetchall()
        return tuple(
            _session_record(row)
            for row in rows
        )

    def update_session(self, record: OperationSessionRecord) -> OperationSessionRecord:
        self._connection.execute(
            """
            UPDATE cpk_operation_sessions
            SET workspace_id = %s,
                actor_id = %s,
                title = %s,
                status = %s,
                created_at = %s,
                closed_at = %s,
                metadata = %s::jsonb
            WHERE session_id = %s
            """,
            (
                record.workspace_id,
                record.actor_id,
                record.title,
                record.status.value,
                record.created_at,
                record.closed_at,
                _json(record.metadata),
                record.session_id,
            ),
        )
        return record

    def add_action(self, record: OperationActionRecord) -> OperationActionRecord:
        self._connection.execute(
            """
            INSERT INTO cpk_operation_actions
              (action_id, session_id, ordinal, action_type, actor_id, payload, created_at,
               idempotency_key, intent_fingerprint)
            VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s)
            """,
            (
                record.action_id,
                record.session_id,
                record.ordinal,
                record.action_type.value,
                record.actor_id,
                _json(record.payload),
                record.created_at,
                record.idempotency_key,
                record.intent_fingerprint,
            ),
        )
        return record

    def action_for_idempotency(
        self, session_id: str, idempotency_key: str
    ) -> OperationActionRecord | None:
        row = self._connection.execute(
            """
            SELECT action_id, session_id, ordinal, action_type, actor_id, payload, created_at,
                   idempotency_key, intent_fingerprint
            FROM cpk_operation_actions
            WHERE session_id = %s AND idempotency_key = %s
            """,
            (session_id, idempotency_key),
        ).fetchone()
        return None if row is None else _action_record(row)

    def next_action_ordinal(self, session_id: str) -> int:
        """Serialize one session's writers on the caller-managed transaction."""

        session = self._connection.execute(
            "SELECT session_id FROM cpk_operation_sessions WHERE session_id = %s FOR UPDATE",
            (session_id,),
        ).fetchone()
        if session is None:
            raise KeyError(f"missing session {session_id!r}")
        row = self._connection.execute(
            "SELECT COALESCE(MAX(ordinal), 0) + 1 FROM cpk_operation_actions WHERE session_id = %s",
            (session_id,),
        ).fetchone()
        return int(row[0])

    def actions_for_session(self, session_id: str) -> tuple[OperationActionRecord, ...]:
        rows = self._connection.execute(
            """
            SELECT action_id, session_id, ordinal, action_type, actor_id, payload, created_at,
                   idempotency_key, intent_fingerprint
            FROM cpk_operation_actions WHERE session_id = %s ORDER BY ordinal ASC
            """,
            (session_id,),
        ).fetchall()
        return tuple(
            _action_record(row)
            for row in rows
        )

    def add_approval_request(
        self,
        record: ApprovalRequestRecord,
    ) -> ApprovalRequestRecord:
        self._connection.execute(
            """
            INSERT INTO cpk_approval_requests
              (request_id, session_id, plan_id, requested_by, requested_at,
               required_scope, max_risk, destructive, comment,
               idempotency_key, intent_fingerprint)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                record.request_id,
                record.session_id,
                record.plan_id,
                record.requested_by,
                record.requested_at,
                record.required_scope,
                record.max_risk.value,
                record.destructive,
                record.comment,
                record.idempotency_key,
                record.intent_fingerprint,
            ),
        )
        return record

    def get_approval_request(self, request_id: str) -> ApprovalRequestRecord:
        row = _record(
            self._connection.execute(
                """
                SELECT request_id, session_id, plan_id, requested_by, requested_at,
                       required_scope, max_risk, destructive, comment,
                       idempotency_key, intent_fingerprint
                FROM cpk_approval_requests WHERE request_id = %s
                """,
                (request_id,),
            ).fetchone(),
            "approval request",
            request_id,
        )
        return _approval_request_record(row)

    def approval_request_for_idempotency(
        self,
        session_id: str,
        idempotency_key: str,
    ) -> ApprovalRequestRecord | None:
        row = self._connection.execute(
            """
            SELECT request_id, session_id, plan_id, requested_by, requested_at,
                   required_scope, max_risk, destructive, comment,
                   idempotency_key, intent_fingerprint
            FROM cpk_approval_requests
            WHERE session_id = %s AND idempotency_key = %s
            """,
            (session_id, idempotency_key),
        ).fetchone()
        return None if row is None else _approval_request_record(row)

    def approval_requests_for_session(
        self,
        session_id: str,
    ) -> tuple[ApprovalRequestRecord, ...]:
        rows = self._connection.execute(
            """
            SELECT request_id, session_id, plan_id, requested_by, requested_at,
                   required_scope, max_risk, destructive, comment,
                   idempotency_key, intent_fingerprint
            FROM cpk_approval_requests
            WHERE session_id = %s ORDER BY requested_at ASC, request_id ASC
            """,
            (session_id,),
        ).fetchall()
        return tuple(_approval_request_record(row) for row in rows)

    def add_approval_decision(
        self,
        record: ApprovalDecisionRecord,
    ) -> ApprovalDecisionRecord:
        self._connection.execute(
            """
            INSERT INTO cpk_approval_decisions
              (decision_id, request_id, actor_id, decision, scope, decided_at,
               comment, idempotency_key, intent_fingerprint)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                record.decision_id,
                record.request_id,
                record.actor_id,
                record.decision.value,
                record.scope,
                record.decided_at,
                record.comment,
                record.idempotency_key,
                record.intent_fingerprint,
            ),
        )
        return record

    def approval_decision_for_request(
        self,
        request_id: str,
    ) -> ApprovalDecisionRecord | None:
        row = self._connection.execute(
            """
            SELECT decision_id, request_id, actor_id, decision, scope, decided_at,
                   comment, idempotency_key, intent_fingerprint
            FROM cpk_approval_decisions WHERE request_id = %s
            """,
            (request_id,),
        ).fetchone()
        return None if row is None else _approval_decision_record(row)

    def approval_decision_for_idempotency(
        self,
        request_id: str,
        idempotency_key: str,
    ) -> ApprovalDecisionRecord | None:
        row = self._connection.execute(
            """
            SELECT decision_id, request_id, actor_id, decision, scope, decided_at,
                   comment, idempotency_key, intent_fingerprint
            FROM cpk_approval_decisions
            WHERE request_id = %s AND idempotency_key = %s
            """,
            (request_id, idempotency_key),
        ).fetchone()
        return None if row is None else _approval_decision_record(row)

    def add_plan(self, record: ActivityPlanRecord) -> ActivityPlanRecord:
        self._connection.execute(
            """
            INSERT INTO cpk_activity_plans
              (plan_id, session_id, base_graph_id, desired_graph_id, status, created_at, payload)
            VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb)
            """,
            (
                record.plan_id,
                record.session_id,
                record.base_graph_id,
                record.desired_graph_id,
                record.status,
                record.created_at,
                DEFAULT_ACTIVITY_PLAN_CODEC.dumps(record.plan),
            ),
        )
        return record

    def get_plan(self, plan_id: str) -> ActivityPlanRecord:
        row = _record(
            self._connection.execute(
                """
                SELECT plan_id, session_id, base_graph_id, desired_graph_id, status, created_at, payload
                FROM cpk_activity_plans WHERE plan_id = %s
                """,
                (plan_id,),
            ).fetchone(),
            "plan",
            plan_id,
        )
        return ActivityPlanRecord(
            plan_id=row[0],
            session_id=row[1],
            base_graph_id=row[2],
            desired_graph_id=row[3],
            status=row[4],
            created_at=row[5],
            plan=DEFAULT_ACTIVITY_PLAN_CODEC.decode(row[6]),
        )

    def plans_for_session(self, session_id: str) -> tuple[ActivityPlanRecord, ...]:
        rows = self._connection.execute(
            """
            SELECT plan_id, session_id, base_graph_id, desired_graph_id, status, created_at, payload
            FROM cpk_activity_plans
            WHERE session_id = %s
            ORDER BY created_at ASC, plan_id ASC
            """,
            (session_id,),
        ).fetchall()
        return tuple(
            ActivityPlanRecord(
                plan_id=row[0],
                session_id=row[1],
                base_graph_id=row[2],
                desired_graph_id=row[3],
                status=row[4],
                created_at=row[5],
                plan=DEFAULT_ACTIVITY_PLAN_CODEC.decode(row[6]),
            )
            for row in rows
        )

    def add_run(self, record: ActivityRunRecord) -> ActivityRunRecord:
        self._connection.execute(
            """
            INSERT INTO cpk_activity_runs
              (run_id, plan_id, status, started_at, finished_at, metadata)
            VALUES (%s, %s, %s, %s, %s, %s::jsonb)
            """,
            (record.run_id, record.plan_id, record.status, record.started_at, record.finished_at, _json(record.metadata)),
        )
        return record

    def runs_for_plan(self, plan_id: str) -> tuple[ActivityRunRecord, ...]:
        rows = self._connection.execute(
            """
            SELECT run_id, plan_id, status, started_at, finished_at, metadata
            FROM cpk_activity_runs
            WHERE plan_id = %s
            ORDER BY started_at ASC, run_id ASC
            """,
            (plan_id,),
        ).fetchall()
        return tuple(
            ActivityRunRecord(
                run_id=row[0],
                plan_id=row[1],
                status=row[2],
                started_at=row[3],
                finished_at=row[4],
                metadata=row[5],
            )
            for row in rows
        )

    def add_event(self, record: ActivityEventRecord) -> ActivityEventRecord:
        self._connection.execute(
            """
            INSERT INTO cpk_activity_events
              (event_id, run_id, ordinal, event_type, occurred_at, payload)
            VALUES (%s, %s, %s, %s, %s, %s::jsonb)
            """,
            (record.event_id, record.run_id, record.ordinal, record.event_type, record.occurred_at, _json(record.payload)),
        )
        return record

    def events_for_run(self, run_id: str) -> tuple[ActivityEventRecord, ...]:
        rows = self._connection.execute(
            """
            SELECT event_id, run_id, ordinal, event_type, occurred_at, payload
            FROM cpk_activity_events WHERE run_id = %s ORDER BY ordinal ASC
            """,
            (run_id,),
        ).fetchall()
        return tuple(
            ActivityEventRecord(
                event_id=row[0],
                run_id=row[1],
                ordinal=row[2],
                event_type=row[3],
                occurred_at=row[4],
                payload=row[5],
            )
            for row in rows
        )


class PostgresObservedStateStore:
    """Postgres-backed observed state store."""

    def __init__(self, connection: PostgresConnection) -> None:
        self._connection = connection

    def put(self, record: ObservationRecord) -> ObservationRecord:
        self._connection.execute(
            """
            INSERT INTO cpk_observations
              (observation_id, workspace_id, subject_id, status, observed_at, payload, stale)
            VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s)
            """,
            (
                record.observation_id,
                record.workspace_id,
                record.subject_id,
                record.status,
                record.observed_at,
                _json(record.payload),
                record.stale,
            ),
        )
        return record

    def latest(self, workspace_id: str, subject_id: str) -> ObservationRecord | None:
        row = self._connection.execute(
            """
            SELECT observation_id, workspace_id, subject_id, status, observed_at, payload, stale
            FROM cpk_observations
            WHERE workspace_id = %s AND subject_id = %s
            ORDER BY observed_at DESC LIMIT 1
            """,
            (workspace_id, subject_id),
        ).fetchone()
        return self._observation(row) if row is not None else None

    def latest_for_workspace(self, workspace_id: str) -> tuple[ObservationRecord, ...]:
        rows = self._connection.execute(
            """
            SELECT DISTINCT ON (subject_id)
              observation_id, workspace_id, subject_id, status, observed_at, payload, stale
            FROM cpk_observations
            WHERE workspace_id = %s
            ORDER BY subject_id ASC, observed_at DESC, observation_id DESC
            """,
            (workspace_id,),
        ).fetchall()
        return tuple(self._observation(row) for row in rows)

    def history(self, workspace_id: str, subject_id: str) -> tuple[ObservationRecord, ...]:
        rows = self._connection.execute(
            """
            SELECT observation_id, workspace_id, subject_id, status, observed_at, payload, stale
            FROM cpk_observations
            WHERE workspace_id = %s AND subject_id = %s
            ORDER BY observed_at ASC
            """,
            (workspace_id, subject_id),
        ).fetchall()
        return tuple(self._observation(row) for row in rows)

    def _observation(self, row: tuple[Any, ...]) -> ObservationRecord:
        return ObservationRecord(
            observation_id=row[0],
            workspace_id=row[1],
            subject_id=row[2],
            status=row[3],
            observed_at=row[4],
            payload=row[5],
            stale=row[6],
        )


class PostgresInstanceRegistryStore:
    """Postgres-backed instance registry store."""

    def __init__(self, connection: PostgresConnection) -> None:
        self._connection = connection

    def register(self, record: InstanceRecord) -> InstanceRecord:
        self._connection.execute(
            """
            INSERT INTO cpk_instances
              (instance_id, owner_id, lifecycle, endpoint, wake_hint, metadata)
            VALUES (%s, %s, %s, %s, %s, %s::jsonb)
            """,
            (
                record.instance_id,
                record.owner_id,
                record.lifecycle.value,
                record.endpoint,
                record.wake_hint,
                _json(record.metadata),
            ),
        )
        return record

    def get(self, instance_id: str) -> InstanceRecord:
        row = _record(
            self._connection.execute(
                """
                SELECT instance_id, owner_id, lifecycle, endpoint, wake_hint, metadata
                FROM cpk_instances WHERE instance_id = %s
                """,
                (instance_id,),
            ).fetchone(),
            "instance",
            instance_id,
        )
        return InstanceRecord(
            instance_id=row[0],
            owner_id=row[1],
            lifecycle=WorkspaceLifecycle(row[2]),
            endpoint=row[3],
            wake_hint=row[4],
            metadata=row[5],
        )

    def set_lifecycle(self, instance_id: str, lifecycle: WorkspaceLifecycle) -> InstanceRecord:
        record = replace(self.get(instance_id), lifecycle=lifecycle)
        self._connection.execute(
            "UPDATE cpk_instances SET lifecycle = %s WHERE instance_id = %s",
            (lifecycle.value, instance_id),
        )
        return record

    def list_for_owner(self, owner_id: str) -> tuple[InstanceRecord, ...]:
        rows = self._connection.execute(
            """
            SELECT instance_id, owner_id, lifecycle, endpoint, wake_hint, metadata
            FROM cpk_instances WHERE owner_id = %s ORDER BY instance_id ASC
            """,
            (owner_id,),
        ).fetchall()
        return tuple(
            InstanceRecord(
                instance_id=row[0],
                owner_id=row[1],
                lifecycle=WorkspaceLifecycle(row[2]),
                endpoint=row[3],
                wake_hint=row[4],
                metadata=row[5],
            )
            for row in rows
        )
