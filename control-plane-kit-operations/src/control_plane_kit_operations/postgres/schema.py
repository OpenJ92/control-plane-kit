"""Caller-transactional Postgres schema installation for operations."""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Protocol

from jinja2 import Environment, StrictUndefined

from control_plane_kit_core.operations.commands import OperatorCommandKind
from control_plane_kit_core.operations.lifecycle import (
    ActivityEventKind,
    ActivityEventScope,
    ActivityRunStatus,
    ExecutionRequestStatus,
    activity_event_scope,
)
from control_plane_kit_core.planning import RiskLevel
from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_core.types import WorkspaceLifecycle


class PostgresConnection(Protocol):
    """Small connection protocol satisfied by psycopg connections."""

    def execute(self, query: str, params: tuple[object, ...] = ()) -> Any: ...


class _OperationsSessionStatus(StrEnum):
    OPEN = "open"
    CLOSED = "closed"
    CANCELLED = "cancelled"


class _ApprovalDecisionKind(StrEnum):
    APPROVED = "approved"
    REJECTED = "rejected"


class _RegisteredProductStatus(StrEnum):
    ACTIVE = "active"
    REVOKED = "revoked"


_SETTLED_RUN_STATUSES = frozenset(
    {
        ActivityRunStatus.SUCCEEDED,
        ActivityRunStatus.COMPENSATED,
        ActivityRunStatus.PARTIALLY_FAILED,
        ActivityRunStatus.UNCOMPENSATED_FAILURE,
        ActivityRunStatus.CANCELLED,
    }
)
_STARTED_RUN_STATUSES = frozenset(set(ActivityRunStatus) - {ActivityRunStatus.CLAIMED})
_ACTIVITY_EVENT_KINDS = tuple(
    kind for kind in ActivityEventKind if activity_event_scope(kind) is ActivityEventScope.ACTIVITY
)
_RUN_EVENT_KINDS = tuple(
    kind for kind in ActivityEventKind if activity_event_scope(kind) is ActivityEventScope.RUN
)


def _sql_values(values: tuple[StrEnum, ...] | frozenset[StrEnum]) -> str:
    return ", ".join(f"'{value.value}'" for value in values)


_SQL_ENVIRONMENT = Environment(
    autoescape=False,
    trim_blocks=True,
    lstrip_blocks=True,
    undefined=StrictUndefined,
)
_SQL_ENVIRONMENT.filters["sql_values"] = _sql_values


_POSTGRES_SCHEMA_TEMPLATE = """
CREATE TABLE IF NOT EXISTS cpk_workspaces (
  workspace_id text PRIMARY KEY,
  name text NOT NULL,
  lifecycle text NOT NULL,
  current_graph_id text,
  desired_graph_id text,
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
  CONSTRAINT cpk_workspaces_lifecycle_check
    CHECK (lifecycle IN ({{ workspace_lifecycles | sql_values }}))
);

CREATE TABLE IF NOT EXISTS cpk_graph_versions (
  graph_id text PRIMARY KEY,
  workspace_id text NOT NULL REFERENCES cpk_workspaces(workspace_id),
  version integer NOT NULL,
  graph_descriptor jsonb NOT NULL,
  created_by text NOT NULL,
  created_at text NOT NULL,
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
  CONSTRAINT cpk_graph_versions_version_check CHECK (version > 0),
  CONSTRAINT cpk_graph_versions_workspace_identity
    UNIQUE (graph_id, workspace_id),
  UNIQUE (workspace_id, version)
);

CREATE TABLE IF NOT EXISTS cpk_registered_products (
  registration_id text PRIMARY KEY,
  workspace_id text NOT NULL REFERENCES cpk_workspaces(workspace_id),
  product_reference jsonb NOT NULL,
  descriptor_sha256 text NOT NULL,
  descriptor_document jsonb NOT NULL,
  descriptor_content text NOT NULL,
  source jsonb NOT NULL,
  imported_by text NOT NULL,
  imported_at text NOT NULL,
  status text NOT NULL,
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
  CONSTRAINT cpk_registered_products_status_check
    CHECK (status IN ({{ registered_product_statuses | sql_values }})),
  CONSTRAINT cpk_registered_products_digest_check
    CHECK (descriptor_sha256 ~ '^[0-9a-f]{64}$'),
  UNIQUE (workspace_id, descriptor_sha256)
);

ALTER TABLE cpk_registered_products
  ADD COLUMN IF NOT EXISTS descriptor_content text;

CREATE TABLE IF NOT EXISTS cpk_operation_sessions (
  session_id text PRIMARY KEY,
  workspace_id text NOT NULL REFERENCES cpk_workspaces(workspace_id),
  actor_id text NOT NULL,
  title text NOT NULL,
  status text NOT NULL,
  created_at text NOT NULL,
  closed_at text,
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
  idempotency_key text,
  intent_fingerprint text,
  CONSTRAINT cpk_operation_sessions_status_check
    CHECK (status IN ({{ operation_session_statuses | sql_values }})),
  CONSTRAINT cpk_operation_sessions_closed_check
    CHECK (
      (status = 'open' AND closed_at IS NULL)
      OR (status IN ('closed', 'cancelled') AND closed_at IS NOT NULL)
    ),
  CONSTRAINT cpk_operation_sessions_workspace_identity
    UNIQUE (session_id, workspace_id)
);

CREATE UNIQUE INDEX IF NOT EXISTS cpk_operation_sessions_idempotency
  ON cpk_operation_sessions (workspace_id, idempotency_key)
  WHERE idempotency_key IS NOT NULL;

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
  CONSTRAINT cpk_operation_actions_ordinal_check CHECK (ordinal > 0),
  CONSTRAINT cpk_operation_actions_type_check
    CHECK (action_type IN ({{ operator_command_kinds | sql_values }})),
  UNIQUE (session_id, ordinal)
);

CREATE UNIQUE INDEX IF NOT EXISTS cpk_operation_actions_idempotency
  ON cpk_operation_actions (session_id, idempotency_key)
  WHERE idempotency_key IS NOT NULL;

CREATE TABLE IF NOT EXISTS cpk_activity_plans (
  plan_id text PRIMARY KEY,
  session_id text NOT NULL REFERENCES cpk_operation_sessions(session_id),
  base_graph_id text NOT NULL,
  desired_graph_id text NOT NULL,
  status text NOT NULL,
  created_at text NOT NULL,
  payload jsonb NOT NULL DEFAULT '{}'::jsonb,
  CONSTRAINT cpk_activity_plans_status_check
    CHECK (status IN ('planned', 'superseded', 'cancelled')),
  CONSTRAINT cpk_activity_plans_session_identity
    UNIQUE (plan_id, session_id)
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
  intent_fingerprint text,
  CONSTRAINT cpk_approval_requests_scope_check
    CHECK (required_scope IN ({{ policy_scopes | sql_values }})),
  CONSTRAINT cpk_approval_requests_risk_check
    CHECK (max_risk IN ({{ risk_levels | sql_values }}))
);

CREATE UNIQUE INDEX IF NOT EXISTS cpk_approval_requests_idempotency
  ON cpk_approval_requests (session_id, idempotency_key)
  WHERE idempotency_key IS NOT NULL;

CREATE TABLE IF NOT EXISTS cpk_approval_decisions (
  decision_id text PRIMARY KEY,
  request_id text NOT NULL UNIQUE REFERENCES cpk_approval_requests(request_id),
  actor_id text NOT NULL,
  decision text NOT NULL,
  scope text NOT NULL,
  decided_at text NOT NULL,
  comment text,
  idempotency_key text,
  intent_fingerprint text,
  CONSTRAINT cpk_approval_decisions_kind_check
    CHECK (decision IN ({{ approval_decision_kinds | sql_values }})),
  CONSTRAINT cpk_approval_decisions_scope_check
    CHECK (scope IN ({{ policy_scopes | sql_values }})),
  CONSTRAINT cpk_approval_decisions_request_identity
    UNIQUE (decision_id, request_id)
);

CREATE UNIQUE INDEX IF NOT EXISTS cpk_approval_decisions_idempotency
  ON cpk_approval_decisions (request_id, idempotency_key)
  WHERE idempotency_key IS NOT NULL;

CREATE TABLE IF NOT EXISTS cpk_execution_requests (
  request_id text PRIMARY KEY,
  workspace_id text NOT NULL REFERENCES cpk_workspaces(workspace_id),
  session_id text NOT NULL,
  plan_id text NOT NULL,
  status text NOT NULL,
  requested_by text NOT NULL,
  requested_at text NOT NULL,
  approval_request_id text NOT NULL REFERENCES cpk_approval_requests(request_id),
  approval_decision_id text NOT NULL,
  idempotency_key text NOT NULL,
  intent_fingerprint text NOT NULL,
  claim_worker_id text,
  claimed_at text,
  lease_expires_at text,
  CONSTRAINT cpk_execution_requests_status_check
    CHECK (status IN ({{ execution_request_statuses | sql_values }})),
  CONSTRAINT cpk_execution_requests_claim_check
    CHECK (
      (status = 'claimed' AND claim_worker_id IS NOT NULL
        AND claimed_at IS NOT NULL AND lease_expires_at IS NOT NULL)
      OR
      (status <> 'claimed' AND claim_worker_id IS NULL
        AND claimed_at IS NULL AND lease_expires_at IS NULL)
    ),
  CONSTRAINT cpk_execution_requests_workspace_session_fk
    FOREIGN KEY (session_id, workspace_id)
    REFERENCES cpk_operation_sessions(session_id, workspace_id),
  CONSTRAINT cpk_execution_requests_plan_session_fk
    FOREIGN KEY (plan_id, session_id)
    REFERENCES cpk_activity_plans(plan_id, session_id),
  CONSTRAINT cpk_execution_requests_plan_identity
    UNIQUE (request_id, plan_id),
  CONSTRAINT cpk_execution_requests_approval_identity_fk
    FOREIGN KEY (approval_decision_id, approval_request_id)
    REFERENCES cpk_approval_decisions(decision_id, request_id),
  UNIQUE (workspace_id, idempotency_key)
);

CREATE UNIQUE INDEX IF NOT EXISTS cpk_execution_requests_active_plan
  ON cpk_execution_requests (plan_id)
  WHERE status IN ('queued', 'claimed');

CREATE TABLE IF NOT EXISTS cpk_activity_runs (
  run_id text PRIMARY KEY,
  plan_id text NOT NULL,
  request_id text NOT NULL,
  attempt integer NOT NULL DEFAULT 1,
  prior_run_id text REFERENCES cpk_activity_runs(run_id),
  status text NOT NULL,
  created_at text NOT NULL,
  started_at text,
  settled_at text,
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
  CONSTRAINT cpk_activity_runs_request_plan_fk
    FOREIGN KEY (request_id, plan_id)
    REFERENCES cpk_execution_requests(request_id, plan_id),
  CONSTRAINT cpk_activity_runs_status_check
    CHECK (status IN ({{ activity_run_statuses | sql_values }})),
  CONSTRAINT cpk_activity_runs_attempt_check
    CHECK (
      attempt > 0
      AND ((attempt = 1 AND prior_run_id IS NULL)
        OR (attempt > 1 AND prior_run_id IS NOT NULL))
      AND prior_run_id IS DISTINCT FROM run_id
    ),
  CONSTRAINT cpk_activity_runs_settlement_check
    CHECK (
      (status IN ({{ settled_run_statuses | sql_values }}) AND settled_at IS NOT NULL)
      OR
      (status NOT IN ({{ settled_run_statuses | sql_values }}) AND settled_at IS NULL)
    ),
  CONSTRAINT cpk_activity_runs_started_check
    CHECK (
      (status = 'claimed' AND started_at IS NULL)
      OR
      (status IN ({{ started_run_statuses | sql_values }}) AND started_at IS NOT NULL)
    )
);

CREATE UNIQUE INDEX IF NOT EXISTS cpk_activity_runs_active_request
  ON cpk_activity_runs (request_id)
  WHERE status IN ('claimed', 'running', 'paused', 'compensating');

CREATE UNIQUE INDEX IF NOT EXISTS cpk_activity_runs_request_attempt
  ON cpk_activity_runs (request_id, attempt);

CREATE TABLE IF NOT EXISTS cpk_activity_events (
  event_id text PRIMARY KEY,
  run_id text NOT NULL REFERENCES cpk_activity_runs(run_id),
  ordinal integer NOT NULL,
  event_type text NOT NULL,
  occurred_at text NOT NULL,
  payload jsonb NOT NULL DEFAULT '{}'::jsonb,
  CONSTRAINT cpk_activity_events_ordinal_check CHECK (ordinal > 0),
  CONSTRAINT cpk_activity_events_kind_check
    CHECK (event_type IN ({{ activity_event_kinds | sql_values }})),
  CONSTRAINT cpk_activity_events_shape_check
    CHECK (
      (
        (
          event_type IN ({{ activity_event_step_kinds | sql_values }})
          AND NULLIF(payload->>'activity_id', '') IS NOT NULL
        )
        OR
        (
          event_type IN ({{ activity_event_run_kinds | sql_values }})
          AND payload->>'activity_id' IS NULL
        )
      )
      AND
      (
        (
          event_type = 'recovery_decision_recorded'
          AND payload ? 'recovery'
          AND jsonb_typeof(payload->'recovery') = 'object'
        )
        OR
        (
          event_type <> 'recovery_decision_recorded'
          AND (
            NOT payload ? 'recovery'
            OR payload->'recovery' = 'null'::jsonb
          )
        )
      )
    ),
  UNIQUE (run_id, ordinal)
);
"""


POSTGRES_SCHEMA = _SQL_ENVIRONMENT.from_string(_POSTGRES_SCHEMA_TEMPLATE).render(
    activity_event_kinds=tuple(ActivityEventKind),
    activity_event_run_kinds=_RUN_EVENT_KINDS,
    activity_event_step_kinds=_ACTIVITY_EVENT_KINDS,
    activity_run_statuses=tuple(ActivityRunStatus),
    approval_decision_kinds=tuple(_ApprovalDecisionKind),
    execution_request_statuses=tuple(ExecutionRequestStatus),
    operation_session_statuses=tuple(_OperationsSessionStatus),
    operator_command_kinds=tuple(OperatorCommandKind),
    policy_scopes=tuple(PolicyScope),
    registered_product_statuses=tuple(_RegisteredProductStatus),
    risk_levels=tuple(RiskLevel),
    settled_run_statuses=_SETTLED_RUN_STATUSES,
    started_run_statuses=_STARTED_RUN_STATUSES,
    workspace_lifecycles=tuple(WorkspaceLifecycle),
)


def install_schema(connection: PostgresConnection) -> None:
    """Install the current operations schema on a caller-managed transaction."""

    connection.execute(POSTGRES_SCHEMA)
