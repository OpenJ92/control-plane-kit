"""Postgres schema and adapters for durable control-plane stores.

The first implementation is intentionally direct: each store owns explicit
tables and transactions are supplied by the caller through the connection.  The
module does not import a Postgres driver at import time, so the base package can
still be used for algebra and in-memory tests without a database dependency.
"""

from __future__ import annotations

import json
from dataclasses import replace
from typing import Any, Protocol

from control_plane_kit.stores.records import (
    ActivityEventRecord,
    ActivityPlanRecord,
    ActivityRunRecord,
    ApprovalRecord,
    GraphVersionRecord,
    InstanceRecord,
    ObservationRecord,
    OperationActionRecord,
    OperationSessionRecord,
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
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS cpk_operation_actions (
  action_id text PRIMARY KEY,
  session_id text NOT NULL REFERENCES cpk_operation_sessions(session_id),
  ordinal integer NOT NULL,
  action_type text NOT NULL,
  actor_id text NOT NULL,
  payload jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at text NOT NULL,
  UNIQUE (session_id, ordinal)
);

CREATE TABLE IF NOT EXISTS cpk_approvals (
  approval_id text PRIMARY KEY,
  session_id text NOT NULL REFERENCES cpk_operation_sessions(session_id),
  target_id text NOT NULL,
  actor_id text NOT NULL,
  decision text NOT NULL,
  scope text NOT NULL,
  decided_at text NOT NULL,
  comment text
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
        row = _record(
            self._connection.execute(
                """
                SELECT workspace_id, name, lifecycle, current_graph_id, desired_graph_id, metadata
                FROM cpk_workspaces WHERE workspace_id = %s
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
              (session_id, workspace_id, actor_id, title, status, created_at, closed_at, metadata)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb)
            """,
            (
                record.session_id,
                record.workspace_id,
                record.actor_id,
                record.title,
                record.status,
                record.created_at,
                record.closed_at,
                _json(record.metadata),
            ),
        )
        return record

    def get_session(self, session_id: str) -> OperationSessionRecord:
        row = _record(
            self._connection.execute(
                """
                SELECT session_id, workspace_id, actor_id, title, status, created_at, closed_at, metadata
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
            status=row[4],
            created_at=row[5],
            closed_at=row[6],
            metadata=row[7],
        )

    def add_action(self, record: OperationActionRecord) -> OperationActionRecord:
        self._connection.execute(
            """
            INSERT INTO cpk_operation_actions
              (action_id, session_id, ordinal, action_type, actor_id, payload, created_at)
            VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s)
            """,
            (
                record.action_id,
                record.session_id,
                record.ordinal,
                record.action_type,
                record.actor_id,
                _json(record.payload),
                record.created_at,
            ),
        )
        return record

    def actions_for_session(self, session_id: str) -> tuple[OperationActionRecord, ...]:
        rows = self._connection.execute(
            """
            SELECT action_id, session_id, ordinal, action_type, actor_id, payload, created_at
            FROM cpk_operation_actions WHERE session_id = %s ORDER BY ordinal ASC
            """,
            (session_id,),
        ).fetchall()
        return tuple(
            OperationActionRecord(
                action_id=row[0],
                session_id=row[1],
                ordinal=row[2],
                action_type=row[3],
                actor_id=row[4],
                payload=row[5],
                created_at=row[6],
            )
            for row in rows
        )

    def add_approval(self, record: ApprovalRecord) -> ApprovalRecord:
        self._connection.execute(
            """
            INSERT INTO cpk_approvals
              (approval_id, session_id, target_id, actor_id, decision, scope, decided_at, comment)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                record.approval_id,
                record.session_id,
                record.target_id,
                record.actor_id,
                record.decision,
                record.scope,
                record.decided_at,
                record.comment,
            ),
        )
        return record

    def approvals_for_session(self, session_id: str) -> tuple[ApprovalRecord, ...]:
        rows = self._connection.execute(
            """
            SELECT approval_id, session_id, target_id, actor_id, decision, scope, decided_at, comment
            FROM cpk_approvals WHERE session_id = %s ORDER BY decided_at ASC
            """,
            (session_id,),
        ).fetchall()
        return tuple(
            ApprovalRecord(
                approval_id=row[0],
                session_id=row[1],
                target_id=row[2],
                actor_id=row[3],
                decision=row[4],
                scope=row[5],
                decided_at=row[6],
                comment=row[7],
            )
            for row in rows
        )

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
                _json(record.payload),
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
            payload=row[6],
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
