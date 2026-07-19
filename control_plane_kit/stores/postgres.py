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

from control_plane_kit.execution import (
    AdmittedRun,
    ActivityEventKind,
    ActivityEventRecord,
    ActivityRunRecord,
    ActivityRunStatus,
    BoundedEvidence,
    ClaimIdentity,
    ExecutionIdempotency,
    ExecutionRequestIdentity,
    ExecutionRequestRecord,
    ExecutionRequestStatus,
    FailureCategory,
    FailureEvidence,
    EndpointContext,
    ObservationFreshness,
    ObservationRecord,
    ObservationStatus,
    ProbeKind,
    ProbeOutcome,
    RecoveryDecisionRecord,
    recovery_decision_record_from_descriptor,
    RetryIdentity,
)
from control_plane_kit.planning.activity_plan import RiskLevel
from control_plane_kit.planning.codec import DEFAULT_ACTIVITY_PLAN_CODEC
from control_plane_kit.stores.records import (
    ActivityPlanRecord,
    ApprovalDecisionKind,
    ApprovalDecisionRecord,
    ApprovalRequestRecord,
    GraphVersionRecord,
    InstanceRecord,
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

CREATE TABLE IF NOT EXISTS cpk_execution_requests (
  request_id text PRIMARY KEY,
  workspace_id text NOT NULL REFERENCES cpk_workspaces(workspace_id),
  session_id text NOT NULL REFERENCES cpk_operation_sessions(session_id),
  plan_id text NOT NULL REFERENCES cpk_activity_plans(plan_id),
  status text NOT NULL,
  requested_by text NOT NULL,
  requested_at text NOT NULL,
  approval_request_id text NOT NULL REFERENCES cpk_approval_requests(request_id),
  approval_decision_id text NOT NULL REFERENCES cpk_approval_decisions(decision_id),
  idempotency_key text NOT NULL,
  intent_fingerprint text NOT NULL,
  claim_worker_id text,
  claimed_at text,
  lease_expires_at text,
  CONSTRAINT cpk_execution_requests_status_check
    CHECK (status IN ('queued', 'claimed', 'cancelled', 'abandoned')),
  CONSTRAINT cpk_execution_requests_claim_check
    CHECK (
      (status = 'claimed' AND claim_worker_id IS NOT NULL
        AND claimed_at IS NOT NULL AND lease_expires_at IS NOT NULL)
      OR
      (status <> 'claimed' AND claim_worker_id IS NULL
        AND claimed_at IS NULL AND lease_expires_at IS NULL)
    ),
  UNIQUE (workspace_id, idempotency_key)
);

CREATE TABLE IF NOT EXISTS cpk_activity_runs (
  run_id text PRIMARY KEY,
  plan_id text NOT NULL REFERENCES cpk_activity_plans(plan_id),
  request_id text NOT NULL REFERENCES cpk_execution_requests(request_id),
  attempt integer NOT NULL DEFAULT 1,
  prior_run_id text REFERENCES cpk_activity_runs(run_id),
  status text NOT NULL,
  created_at text NOT NULL,
  started_at text,
  settled_at text,
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
  stale boolean NOT NULL DEFAULT false,
  graph_id text,
  probe_kind text,
  probe_outcome text,
  endpoint_context text,
  CONSTRAINT cpk_observations_correlation_check CHECK (
    (graph_id IS NULL AND probe_kind IS NULL AND probe_outcome IS NULL
      AND endpoint_context IS NULL)
    OR
    (graph_id IS NOT NULL AND probe_kind IS NOT NULL AND probe_outcome IS NOT NULL)
  ),
  CONSTRAINT cpk_observations_probe_kind_check CHECK (
    probe_kind IS NULL OR probe_kind IN (
      'process', 'transport', 'application-health', 'readiness',
      'semantic-verification'
    )
  ),
  CONSTRAINT cpk_observations_endpoint_context_check CHECK (
    endpoint_context IS NULL OR endpoint_context IN (
      'runtime-private', 'host-local', 'public'
    )
  ),
  CONSTRAINT cpk_observations_probe_outcome_check CHECK (
    probe_outcome IS NULL OR probe_outcome IN (
      'process-running', 'process-stopped', 'reachable', 'refused', 'healthy',
      'unhealthy', 'timed-out', 'malformed', 'unknown', 'ready', 'not-ready',
      'verified', 'verification-failed', 'unsupported', 'rejected'
    )
  ),
  CONSTRAINT cpk_observations_context_kind_check CHECK (
    (probe_kind IN ('transport', 'application-health', 'semantic-verification')
      AND endpoint_context IS NOT NULL)
    OR
    (probe_kind IN ('process', 'readiness') AND endpoint_context IS NULL)
    OR
    probe_kind IS NULL
  ),
  CONSTRAINT cpk_observations_outcome_kind_check CHECK (
    probe_kind IS NULL
    OR (probe_kind = 'process' AND probe_outcome IN (
      'process-running', 'process-stopped', 'unknown'
    ))
    OR (probe_kind = 'transport' AND probe_outcome IN (
      'reachable', 'refused', 'timed-out', 'unknown'
    ))
    OR (probe_kind = 'application-health' AND probe_outcome IN (
      'healthy', 'unhealthy', 'refused', 'timed-out', 'malformed', 'unknown'
    ))
    OR (probe_kind = 'readiness' AND probe_outcome IN (
      'ready', 'not-ready', 'unknown'
    ))
    OR (probe_kind = 'semantic-verification' AND probe_outcome IN (
      'verified', 'verification-failed', 'timed-out', 'malformed',
      'unsupported', 'rejected', 'unknown'
    ))
  )
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

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conrelid = 'cpk_graph_versions'::regclass
      AND conname = 'cpk_graph_versions_workspace_identity'
  ) THEN
    ALTER TABLE cpk_graph_versions
      ADD CONSTRAINT cpk_graph_versions_workspace_identity
      UNIQUE (graph_id, workspace_id);
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conrelid = 'cpk_operation_sessions'::regclass
      AND conname = 'cpk_operation_sessions_workspace_identity'
  ) THEN
    ALTER TABLE cpk_operation_sessions
      ADD CONSTRAINT cpk_operation_sessions_workspace_identity
      UNIQUE (session_id, workspace_id);
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conrelid = 'cpk_activity_plans'::regclass
      AND conname = 'cpk_activity_plans_session_identity'
  ) THEN
    ALTER TABLE cpk_activity_plans
      ADD CONSTRAINT cpk_activity_plans_session_identity
      UNIQUE (plan_id, session_id);
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conrelid = 'cpk_approval_decisions'::regclass
      AND conname = 'cpk_approval_decisions_request_identity'
  ) THEN
    ALTER TABLE cpk_approval_decisions
      ADD CONSTRAINT cpk_approval_decisions_request_identity
      UNIQUE (decision_id, request_id);
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conrelid = 'cpk_execution_requests'::regclass
      AND conname = 'cpk_execution_requests_plan_identity'
  ) THEN
    ALTER TABLE cpk_execution_requests
      ADD CONSTRAINT cpk_execution_requests_plan_identity
      UNIQUE (request_id, plan_id);
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conrelid = 'cpk_execution_requests'::regclass
      AND conname = 'cpk_execution_requests_workspace_session_fk'
  ) THEN
    ALTER TABLE cpk_execution_requests
      ADD CONSTRAINT cpk_execution_requests_workspace_session_fk
      FOREIGN KEY (session_id, workspace_id)
      REFERENCES cpk_operation_sessions(session_id, workspace_id);
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conrelid = 'cpk_execution_requests'::regclass
      AND conname = 'cpk_execution_requests_plan_session_fk'
  ) THEN
    ALTER TABLE cpk_execution_requests
      ADD CONSTRAINT cpk_execution_requests_plan_session_fk
      FOREIGN KEY (plan_id, session_id)
      REFERENCES cpk_activity_plans(plan_id, session_id);
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conrelid = 'cpk_execution_requests'::regclass
      AND conname = 'cpk_execution_requests_approval_identity_fk'
  ) THEN
    ALTER TABLE cpk_execution_requests
      ADD CONSTRAINT cpk_execution_requests_approval_identity_fk
      FOREIGN KEY (approval_decision_id, approval_request_id)
      REFERENCES cpk_approval_decisions(decision_id, request_id);
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conrelid = 'cpk_activity_runs'::regclass
      AND conname = 'cpk_activity_runs_request_plan_fk'
  ) THEN
    ALTER TABLE cpk_activity_runs
      ADD CONSTRAINT cpk_activity_runs_request_plan_fk
      FOREIGN KEY (request_id, plan_id)
      REFERENCES cpk_execution_requests(request_id, plan_id) NOT VALID;
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conrelid = 'cpk_observations'::regclass
      AND conname = 'cpk_observations_workspace_fk'
  ) THEN
    ALTER TABLE cpk_observations
      ADD CONSTRAINT cpk_observations_workspace_fk
      FOREIGN KEY (workspace_id) REFERENCES cpk_workspaces(workspace_id) NOT VALID;
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conrelid = 'cpk_observations'::regclass
      AND conname = 'cpk_observations_graph_workspace_fk'
  ) THEN
    ALTER TABLE cpk_observations
      ADD CONSTRAINT cpk_observations_graph_workspace_fk
      FOREIGN KEY (graph_id, workspace_id)
      REFERENCES cpk_graph_versions(graph_id, workspace_id) NOT VALID;
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conrelid = 'cpk_activity_runs'::regclass
      AND conname = 'cpk_activity_runs_status_check'
  ) THEN
    ALTER TABLE cpk_activity_runs
      ADD CONSTRAINT cpk_activity_runs_status_check
      CHECK (status IN (
        'claimed', 'running', 'paused', 'succeeded', 'failed',
        'compensating', 'compensated', 'partially_failed',
        'uncompensated_failure', 'cancelled'
      )) NOT VALID;
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conrelid = 'cpk_activity_runs'::regclass
      AND conname = 'cpk_activity_runs_attempt_check'
  ) THEN
    ALTER TABLE cpk_activity_runs
      ADD CONSTRAINT cpk_activity_runs_attempt_check
      CHECK (
        attempt > 0
        AND ((attempt = 1 AND prior_run_id IS NULL)
          OR (attempt > 1 AND prior_run_id IS NOT NULL))
        AND prior_run_id IS DISTINCT FROM run_id
      ) NOT VALID;
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conrelid = 'cpk_activity_runs'::regclass
      AND conname = 'cpk_activity_runs_settlement_check'
  ) THEN
    ALTER TABLE cpk_activity_runs
      ADD CONSTRAINT cpk_activity_runs_settlement_check
      CHECK (
        (
          status IN (
            'succeeded', 'compensated', 'partially_failed',
            'uncompensated_failure', 'cancelled'
          )
          AND settled_at IS NOT NULL
        )
        OR (
          status IN ('claimed', 'running', 'paused', 'failed', 'compensating')
          AND settled_at IS NULL
        )
      ) NOT VALID;
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conrelid = 'cpk_activity_runs'::regclass
      AND conname = 'cpk_activity_runs_started_check'
  ) THEN
    ALTER TABLE cpk_activity_runs
      ADD CONSTRAINT cpk_activity_runs_started_check
      CHECK (
        (status = 'claimed' AND started_at IS NULL)
        OR status = 'cancelled'
        OR (
          status IN (
            'running', 'paused', 'succeeded', 'failed', 'compensating',
            'compensated', 'partially_failed', 'uncompensated_failure'
          )
          AND started_at IS NOT NULL
        )
      ) NOT VALID;
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conrelid = 'cpk_activity_events'::regclass
      AND conname = 'cpk_activity_events_kind_check'
  ) THEN
    ALTER TABLE cpk_activity_events
      ADD CONSTRAINT cpk_activity_events_kind_check
      CHECK (event_type IN (
        'request_admitted', 'request_claimed', 'request_claim_renewed',
        'request_claim_taken_over', 'request_claim_abandoned',
        'run_opened', 'run_started', 'run_paused',
        'run_resumed', 'step_started', 'step_succeeded', 'step_failed',
        'step_unsupported', 'step_uncertain',
        'step_uncertainty_resolved_succeeded', 'step_uncertainty_resolved_failed',
        'step_compensation_started', 'step_compensation_succeeded',
        'step_compensation_failed', 'step_compensation_unsupported',
        'step_compensation_uncertain',
        'step_compensation_uncertainty_resolved_succeeded',
        'step_compensation_uncertainty_resolved_failed',
        'recovery_decision_recorded',
        'run_compensation_started', 'run_compensation_succeeded',
        'run_compensation_failed', 'run_uncompensated_failure_accepted',
        'run_succeeded', 'run_failed', 'run_cancelled', 'current_graph_advanced'
      )) NOT VALID;
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conrelid = 'cpk_activity_events'::regclass
      AND conname = 'cpk_activity_events_shape_check'
  ) THEN
    ALTER TABLE cpk_activity_events
      ADD CONSTRAINT cpk_activity_events_shape_check
      CHECK (
        (
          (
            event_type IN (
              'step_started', 'step_succeeded', 'step_failed',
              'step_unsupported', 'step_uncertain',
              'step_uncertainty_resolved_succeeded',
              'step_uncertainty_resolved_failed',
              'step_compensation_started', 'step_compensation_succeeded',
              'step_compensation_failed', 'step_compensation_unsupported',
              'step_compensation_uncertain',
              'step_compensation_uncertainty_resolved_succeeded',
              'step_compensation_uncertainty_resolved_failed'
            )
            AND NULLIF(payload->>'activity_id', '') IS NOT NULL
          )
          OR (
            event_type NOT IN (
              'step_started', 'step_succeeded', 'step_failed',
              'step_unsupported', 'step_uncertain',
              'step_uncertainty_resolved_succeeded',
              'step_uncertainty_resolved_failed',
              'step_compensation_started', 'step_compensation_succeeded',
              'step_compensation_failed', 'step_compensation_unsupported',
              'step_compensation_uncertain',
              'step_compensation_uncertainty_resolved_succeeded',
              'step_compensation_uncertainty_resolved_failed'
            )
            AND payload->>'activity_id' IS NULL
          )
        )
        AND (
          (
            event_type = 'recovery_decision_recorded'
            AND payload ? 'recovery'
            AND jsonb_typeof(payload->'recovery') = 'object'
          )
          OR (
            event_type <> 'recovery_decision_recorded'
            AND (
              NOT payload ? 'recovery'
              OR payload->'recovery' = 'null'::jsonb
            )
          )
        )
      ) NOT VALID;
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conrelid = 'cpk_observations'::regclass
      AND conname = 'cpk_observations_status_check'
  ) THEN
    ALTER TABLE cpk_observations
      ADD CONSTRAINT cpk_observations_status_check
      CHECK (status IN (
        'starting', 'process_started', 'reachable', 'healthy', 'unhealthy',
        'timed_out', 'verified', 'verification_failed', 'unsupported',
        'rejected', 'malformed', 'unknown'
      )) NOT VALID;
  END IF;
END $$;

DO $$
DECLARE
  definition text;
BEGIN
  SELECT pg_get_constraintdef(oid) INTO definition
  FROM pg_constraint
  WHERE conrelid = 'cpk_observations'::regclass
    AND conname = 'cpk_observations_probe_kind_check';
  IF definition IS NOT NULL AND position('semantic-verification' IN definition) = 0 THEN
    ALTER TABLE cpk_observations DROP CONSTRAINT cpk_observations_probe_kind_check;
    ALTER TABLE cpk_observations ADD CONSTRAINT cpk_observations_probe_kind_check
      CHECK (probe_kind IS NULL OR probe_kind IN (
        'process', 'transport', 'application-health', 'readiness',
        'semantic-verification'
      ));
  END IF;

  SELECT pg_get_constraintdef(oid) INTO definition
  FROM pg_constraint
  WHERE conrelid = 'cpk_observations'::regclass
    AND conname = 'cpk_observations_probe_outcome_check';
  IF definition IS NOT NULL AND position('verification-failed' IN definition) = 0 THEN
    ALTER TABLE cpk_observations DROP CONSTRAINT cpk_observations_probe_outcome_check;
    ALTER TABLE cpk_observations ADD CONSTRAINT cpk_observations_probe_outcome_check
      CHECK (probe_outcome IS NULL OR probe_outcome IN (
        'process-running', 'process-stopped', 'reachable', 'refused', 'healthy',
        'unhealthy', 'timed-out', 'malformed', 'unknown', 'ready', 'not-ready',
        'verified', 'verification-failed', 'unsupported', 'rejected'
      ));
  END IF;

  SELECT pg_get_constraintdef(oid) INTO definition
  FROM pg_constraint
  WHERE conrelid = 'cpk_observations'::regclass
    AND conname = 'cpk_observations_context_kind_check';
  IF definition IS NOT NULL AND position('semantic-verification' IN definition) = 0 THEN
    ALTER TABLE cpk_observations DROP CONSTRAINT cpk_observations_context_kind_check;
    ALTER TABLE cpk_observations ADD CONSTRAINT cpk_observations_context_kind_check CHECK (
      (probe_kind IN ('transport', 'application-health', 'semantic-verification')
        AND endpoint_context IS NOT NULL)
      OR (probe_kind IN ('process', 'readiness') AND endpoint_context IS NULL)
      OR probe_kind IS NULL
    );
  END IF;

  SELECT pg_get_constraintdef(oid) INTO definition
  FROM pg_constraint
  WHERE conrelid = 'cpk_observations'::regclass
    AND conname = 'cpk_observations_outcome_kind_check';
  IF definition IS NOT NULL AND position('semantic-verification' IN definition) = 0 THEN
    ALTER TABLE cpk_observations DROP CONSTRAINT cpk_observations_outcome_kind_check;
    ALTER TABLE cpk_observations ADD CONSTRAINT cpk_observations_outcome_kind_check CHECK (
      probe_kind IS NULL
      OR (probe_kind = 'process' AND probe_outcome IN (
        'process-running', 'process-stopped', 'unknown'
      ))
      OR (probe_kind = 'transport' AND probe_outcome IN (
        'reachable', 'refused', 'timed-out', 'unknown'
      ))
      OR (probe_kind = 'application-health' AND probe_outcome IN (
        'healthy', 'unhealthy', 'refused', 'timed-out', 'malformed', 'unknown'
      ))
      OR (probe_kind = 'readiness' AND probe_outcome IN (
        'ready', 'not-ready', 'unknown'
      ))
      OR (probe_kind = 'semantic-verification' AND probe_outcome IN (
        'verified', 'verification-failed', 'timed-out', 'malformed',
        'unsupported', 'rejected', 'unknown'
      ))
    );
  END IF;

  SELECT pg_get_constraintdef(oid) INTO definition
  FROM pg_constraint
  WHERE conrelid = 'cpk_observations'::regclass
    AND conname = 'cpk_observations_status_check';
  IF definition IS NOT NULL AND position('verification_failed' IN definition) = 0 THEN
    ALTER TABLE cpk_observations DROP CONSTRAINT cpk_observations_status_check;
    ALTER TABLE cpk_observations ADD CONSTRAINT cpk_observations_status_check
      CHECK (status IN (
        'starting', 'process_started', 'reachable', 'healthy', 'unhealthy',
        'timed_out', 'verified', 'verification_failed', 'unsupported',
        'rejected', 'malformed', 'unknown'
      )) NOT VALID;
  END IF;
END $$;

CREATE UNIQUE INDEX IF NOT EXISTS cpk_execution_requests_active_plan
  ON cpk_execution_requests (plan_id)
  WHERE status IN ('queued', 'claimed');

CREATE UNIQUE INDEX IF NOT EXISTS cpk_activity_runs_active_request
  ON cpk_activity_runs (request_id)
  WHERE request_id IS NOT NULL
    AND status IN ('claimed', 'running', 'paused', 'compensating');

CREATE UNIQUE INDEX IF NOT EXISTS cpk_activity_runs_request_attempt
  ON cpk_activity_runs (request_id, attempt)
  WHERE request_id IS NOT NULL;
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

    def compare_and_set_current_graph(
        self,
        workspace_id: str,
        *,
        expected_graph_id: str,
        replacement_graph_id: str,
    ) -> WorkspaceRecord | None:
        """Advance a pointer only from the caller's expected graph."""

        row = self._connection.execute(
            """
            UPDATE cpk_workspaces
            SET current_graph_id = %s
            WHERE workspace_id = %s AND current_graph_id = %s
            RETURNING workspace_id, name, lifecycle, current_graph_id,
                      desired_graph_id, metadata
            """,
            (replacement_graph_id, workspace_id, expected_graph_id),
        ).fetchone()
        if row is None:
            return None
        return WorkspaceRecord(
            workspace_id=row[0],
            name=row[1],
            lifecycle=WorkspaceLifecycle(row[2]),
            current_graph_id=row[3],
            desired_graph_id=row[4],
            metadata=row[5],
        )

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
        self.execution = PostgresExecutionStore(connection)
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

    def transition_open_session(
        self,
        session_id: str,
        *,
        replacement: OperationSessionStatus,
        closed_at: str,
    ) -> OperationSessionRecord | None:
        if replacement not in {
            OperationSessionStatus.CLOSED,
            OperationSessionStatus.CANCELLED,
        }:
            raise ValueError("operation sessions may transition only to a terminal status")
        if not closed_at:
            raise ValueError("terminal operation sessions require closed_at")
        row = self._connection.execute(
            """
            UPDATE cpk_operation_sessions
            SET status = %s, closed_at = %s
            WHERE session_id = %s AND status = 'open'
            RETURNING session_id, workspace_id, actor_id, title, status,
                      created_at, closed_at, metadata, idempotency_key,
                      intent_fingerprint
            """,
            (
                replacement.value,
                closed_at,
                session_id,
            ),
        ).fetchone()
        return None if row is None else _session_record(row)

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

class PostgresExecutionStore:
    """Postgres execution truth on a caller-owned UnitOfWork connection."""

    def __init__(self, connection: PostgresConnection) -> None:
        self._connection = connection

    def add_request(self, record: ExecutionRequestRecord) -> ExecutionRequestRecord:
        claim = record.claim
        self._connection.execute(
            """
            INSERT INTO cpk_execution_requests
              (request_id, workspace_id, session_id, plan_id, status,
               requested_by, requested_at, approval_request_id,
               approval_decision_id, idempotency_key, intent_fingerprint,
               claim_worker_id, claimed_at, lease_expires_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                record.identity.request_id,
                record.identity.workspace_id,
                record.identity.session_id,
                record.identity.plan_id,
                record.status.value,
                record.requested_by,
                record.requested_at,
                record.approval_request_id,
                record.approval_decision_id,
                record.idempotency.key,
                record.idempotency.intent_fingerprint,
                None if claim is None else claim.worker_id,
                None if claim is None else claim.claimed_at,
                None if claim is None else claim.lease_expires_at,
            ),
        )
        return record

    def get_request(self, request_id: str) -> ExecutionRequestRecord:
        row = _record(
            self._connection.execute(
                """
                SELECT request_id, workspace_id, session_id, plan_id, status,
                       requested_by, requested_at, approval_request_id,
                       approval_decision_id, idempotency_key, intent_fingerprint,
                       claim_worker_id, claimed_at, lease_expires_at
                FROM cpk_execution_requests WHERE request_id = %s
                """,
                (request_id,),
            ).fetchone(),
            "execution request",
            request_id,
        )
        return _execution_request(row)

    def get_request_for_update(self, request_id: str) -> ExecutionRequestRecord:
        row = _record(
            self._connection.execute(
                """
                SELECT request_id, workspace_id, session_id, plan_id, status,
                       requested_by, requested_at, approval_request_id,
                       approval_decision_id, idempotency_key, intent_fingerprint,
                       claim_worker_id, claimed_at, lease_expires_at
                FROM cpk_execution_requests
                WHERE request_id = %s
                FOR UPDATE
                """,
                (request_id,),
            ).fetchone(),
            "execution request",
            request_id,
        )
        return _execution_request(row)

    def request_for_idempotency(
        self, workspace_id: str, idempotency_key: str
    ) -> ExecutionRequestRecord | None:
        row = self._connection.execute(
            """
            SELECT request_id, workspace_id, session_id, plan_id, status,
                   requested_by, requested_at, approval_request_id,
                   approval_decision_id, idempotency_key, intent_fingerprint,
                   claim_worker_id, claimed_at, lease_expires_at
            FROM cpk_execution_requests
            WHERE workspace_id = %s AND idempotency_key = %s
            """,
            (workspace_id, idempotency_key),
        ).fetchone()
        return None if row is None else _execution_request(row)

    def claim_request(
        self,
        request_id: str,
        worker_id: str,
        claimed_at: str,
        lease_expires_at: str,
    ) -> ExecutionRequestRecord | None:
        row = self._connection.execute(
            """
            SELECT request_id, workspace_id, session_id, plan_id, status,
                   requested_by, requested_at, approval_request_id,
                   approval_decision_id, idempotency_key, intent_fingerprint,
                   claim_worker_id, claimed_at, lease_expires_at
            FROM cpk_execution_requests
            WHERE request_id = %s
            FOR UPDATE
            """,
            (request_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"missing execution request {request_id!r}")
        current = _execution_request(row)
        if current.status is ExecutionRequestStatus.CLAIMED:
            if current.claim is not None and current.claim.worker_id == worker_id:
                return current
            return None
        if current.status is not ExecutionRequestStatus.QUEUED:
            return None
        updated = self._connection.execute(
            """
            UPDATE cpk_execution_requests
            SET status = 'claimed', claim_worker_id = %s, claimed_at = %s,
                lease_expires_at = %s
            WHERE request_id = %s AND status = 'queued'
            RETURNING request_id, workspace_id, session_id, plan_id, status,
                      requested_by, requested_at, approval_request_id,
                      approval_decision_id, idempotency_key, intent_fingerprint,
                      claim_worker_id, claimed_at, lease_expires_at
            """,
            (worker_id, claimed_at, lease_expires_at, request_id),
        ).fetchone()
        return None if updated is None else _execution_request(updated)

    def cancel_claimed_request(
        self, request_id: str, *, worker_id: str
    ) -> ExecutionRequestRecord | None:
        row = self._connection.execute(
            """
            UPDATE cpk_execution_requests
            SET status = 'cancelled', claim_worker_id = NULL,
                claimed_at = NULL, lease_expires_at = NULL
            WHERE request_id = %s
              AND status = 'claimed'
              AND claim_worker_id = %s
            RETURNING request_id, workspace_id, session_id, plan_id, status,
                      requested_by, requested_at, approval_request_id,
                      approval_decision_id, idempotency_key, intent_fingerprint,
                      claim_worker_id, claimed_at, lease_expires_at
            """,
            (request_id, worker_id),
        ).fetchone()
        return None if row is None else _execution_request(row)

    def renew_expired_request_claim(
        self,
        request_id: str,
        *,
        expected_worker_id: str,
        observed_at: str,
        lease_expires_at: str,
    ) -> ExecutionRequestRecord | None:
        row = self._connection.execute(
            """
            UPDATE cpk_execution_requests
            SET lease_expires_at = %s
            WHERE request_id = %s
              AND status = 'claimed'
              AND claim_worker_id = %s
              AND lease_expires_at::timestamptz <= %s::timestamptz
            RETURNING request_id, workspace_id, session_id, plan_id, status,
                      requested_by, requested_at, approval_request_id,
                      approval_decision_id, idempotency_key, intent_fingerprint,
                      claim_worker_id, claimed_at, lease_expires_at
            """,
            (lease_expires_at, request_id, expected_worker_id, observed_at),
        ).fetchone()
        return None if row is None else _execution_request(row)

    def take_over_expired_request_claim(
        self,
        request_id: str,
        *,
        expected_worker_id: str,
        replacement_worker_id: str,
        observed_at: str,
        lease_expires_at: str,
    ) -> ExecutionRequestRecord | None:
        row = self._connection.execute(
            """
            UPDATE cpk_execution_requests
            SET claim_worker_id = %s, claimed_at = %s, lease_expires_at = %s
            WHERE request_id = %s
              AND status = 'claimed'
              AND claim_worker_id = %s
              AND lease_expires_at::timestamptz <= %s::timestamptz
            RETURNING request_id, workspace_id, session_id, plan_id, status,
                      requested_by, requested_at, approval_request_id,
                      approval_decision_id, idempotency_key, intent_fingerprint,
                      claim_worker_id, claimed_at, lease_expires_at
            """,
            (
                replacement_worker_id,
                observed_at,
                lease_expires_at,
                request_id,
                expected_worker_id,
                observed_at,
            ),
        ).fetchone()
        return None if row is None else _execution_request(row)

    def abandon_expired_request_claim(
        self,
        request_id: str,
        *,
        expected_worker_id: str,
        observed_at: str,
    ) -> ExecutionRequestRecord | None:
        row = self._connection.execute(
            """
            UPDATE cpk_execution_requests
            SET status = 'abandoned', claim_worker_id = NULL,
                claimed_at = NULL, lease_expires_at = NULL
            WHERE request_id = %s
              AND status = 'claimed'
              AND claim_worker_id = %s
              AND lease_expires_at::timestamptz <= %s::timestamptz
            RETURNING request_id, workspace_id, session_id, plan_id, status,
                      requested_by, requested_at, approval_request_id,
                      approval_decision_id, idempotency_key, intent_fingerprint,
                      claim_worker_id, claimed_at, lease_expires_at
            """,
            (request_id, expected_worker_id, observed_at),
        ).fetchone()
        return None if row is None else _execution_request(row)

    def add_run(self, record: ActivityRunRecord) -> ActivityRunRecord:
        self._connection.execute(
            """
            INSERT INTO cpk_activity_runs
              (run_id, plan_id, request_id, attempt, prior_run_id, status,
               created_at, started_at, settled_at, metadata)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
            """,
            (
                record.run_id,
                record.plan_id,
                record.admission.request_id,
                record.retry.attempt,
                record.retry.prior_run_id,
                record.status.value,
                record.created_at,
                record.started_at,
                record.settled_at,
                _json(record.metadata.descriptor()),
            ),
        )
        return record

    def get_run(self, run_id: str) -> ActivityRunRecord:
        row = _record(
            self._connection.execute(
                """
                SELECT run_id, plan_id, request_id, attempt, prior_run_id, status,
                       created_at, started_at, settled_at, metadata
                FROM cpk_activity_runs WHERE run_id = %s
                """,
                (run_id,),
            ).fetchone(),
            "activity run",
            run_id,
        )
        return _activity_run(row)

    def get_run_for_update(self, run_id: str) -> ActivityRunRecord:
        row = _record(
            self._connection.execute(
                """
                SELECT run_id, plan_id, request_id, attempt, prior_run_id, status,
                       created_at, started_at, settled_at, metadata
                FROM cpk_activity_runs
                WHERE run_id = %s
                FOR UPDATE
                """,
                (run_id,),
            ).fetchone(),
            "activity run",
            run_id,
        )
        return _activity_run(row)

    def compare_and_set_run_status(
        self,
        run_id: str,
        *,
        expected: ActivityRunStatus,
        replacement: ActivityRunStatus,
        started_at: str | None = None,
        settled_at: str | None = None,
    ) -> ActivityRunRecord | None:
        row = self._connection.execute(
            """
            UPDATE cpk_activity_runs
            SET status = %s,
                started_at = COALESCE(%s, started_at),
                settled_at = COALESCE(settled_at, %s)
            WHERE run_id = %s AND status = %s
              AND settled_at IS NULL
            RETURNING run_id, plan_id, request_id, attempt, prior_run_id, status,
                      created_at, started_at, settled_at, metadata
            """,
            (
                replacement.value,
                started_at,
                settled_at,
                run_id,
                expected.value,
            ),
        ).fetchone()
        return None if row is None else _activity_run(row)

    def runs_for_plan(self, plan_id: str) -> tuple[ActivityRunRecord, ...]:
        return self._runs("plan_id", plan_id, "created_at ASC, run_id ASC")

    def runs_for_request(self, request_id: str) -> tuple[ActivityRunRecord, ...]:
        return self._runs("request_id", request_id, "attempt ASC, run_id ASC")

    def _runs(
        self, column: str, identity: str, ordering: str
    ) -> tuple[ActivityRunRecord, ...]:
        if column not in {"plan_id", "request_id"}:
            raise ValueError("unsupported run lookup")
        rows = self._connection.execute(
            f"""
            SELECT run_id, plan_id, request_id, attempt, prior_run_id, status,
                   created_at, started_at, settled_at, metadata
            FROM cpk_activity_runs WHERE {column} = %s ORDER BY {ordering}
            """,
            (identity,),
        ).fetchall()
        return tuple(_activity_run(row) for row in rows)

    def add_event(self, record: ActivityEventRecord) -> ActivityEventRecord:
        self._connection.execute(
            """
            INSERT INTO cpk_activity_events
              (event_id, run_id, ordinal, event_type, occurred_at, payload)
            VALUES (%s, %s, %s, %s, %s, %s::jsonb)
            """,
            (record.event_id, record.run_id, record.ordinal, record.kind.value, record.occurred_at, _json({
                "activity_id": record.activity_id,
                "evidence": record.evidence.descriptor(),
                "failure": None if record.failure is None else {
                    "category": record.failure.category.value,
                    "code": record.failure.code,
                    "message": record.failure.message,
                    "details": record.failure.details.descriptor(),
                },
                "recovery": (
                    None if record.recovery is None else record.recovery.descriptor()
                ),
            })),
        )
        return record

    def get_event(self, event_id: str) -> ActivityEventRecord:
        row = _record(
            self._connection.execute(
                """
                SELECT event_id, run_id, ordinal, event_type, occurred_at, payload
                FROM cpk_activity_events WHERE event_id = %s
                """,
                (event_id,),
            ).fetchone(),
            "activity event",
            event_id,
        )
        return _activity_event(row)

    def next_event_ordinal(self, run_id: str) -> int:
        locked = self._connection.execute(
            "SELECT run_id FROM cpk_activity_runs WHERE run_id = %s FOR UPDATE",
            (run_id,),
        ).fetchone()
        if locked is None:
            raise KeyError(f"missing activity run {run_id!r}")
        row = self._connection.execute(
            """
            SELECT COALESCE(MAX(ordinal), 0) + 1
            FROM cpk_activity_events WHERE run_id = %s
            """,
            (run_id,),
        ).fetchone()
        return int(row[0])

    def events_for_run(self, run_id: str) -> tuple[ActivityEventRecord, ...]:
        rows = self._connection.execute(
            """
            SELECT event_id, run_id, ordinal, event_type, occurred_at, payload
            FROM cpk_activity_events WHERE run_id = %s ORDER BY ordinal ASC
            """,
            (run_id,),
        ).fetchall()
        return tuple(_activity_event(row) for row in rows)


def _execution_request(row: tuple[Any, ...]) -> ExecutionRequestRecord:
    claim = None
    if row[11] is not None:
        claim = ClaimIdentity(row[11], row[12], row[13])
    return ExecutionRequestRecord(
        identity=ExecutionRequestIdentity(row[0], row[1], row[2], row[3]),
        status=ExecutionRequestStatus(row[4]),
        requested_by=row[5],
        requested_at=row[6],
        approval_request_id=row[7],
        approval_decision_id=row[8],
        idempotency=ExecutionIdempotency(row[9], row[10]),
        claim=claim,
    )


def _activity_run(row: tuple[Any, ...]) -> ActivityRunRecord:
    return ActivityRunRecord(
        run_id=row[0],
        plan_id=row[1],
        admission=AdmittedRun(row[2]),
        retry=RetryIdentity(row[3], row[4]),
        status=ActivityRunStatus(row[5]),
        created_at=row[6],
        started_at=row[7],
        settled_at=row[8],
        metadata=BoundedEvidence.from_mapping(row[9]),
    )


def _activity_event(row: tuple[Any, ...]) -> ActivityEventRecord:
    return ActivityEventRecord(
        event_id=row[0],
        run_id=row[1],
        ordinal=row[2],
        kind=ActivityEventKind(row[3]),
        occurred_at=row[4],
        activity_id=row[5].get("activity_id"),
        evidence=BoundedEvidence.from_mapping(row[5].get("evidence", {})),
        failure=_failure_evidence(row[5].get("failure")),
        recovery=_recovery_decision(row[5].get("recovery")),
    )


def _failure_evidence(value: object) -> FailureEvidence | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("persisted activity failure must be an object")
    return FailureEvidence(
        category=FailureCategory(value["category"]),
        code=value["code"],
        message=value["message"],
        details=BoundedEvidence.from_mapping(value.get("details", {})),
    )


def _recovery_decision(value: object) -> RecoveryDecisionRecord | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("persisted recovery decision must be an object")
    return recovery_decision_record_from_descriptor(value)


class PostgresObservedStateStore:
    """Postgres-backed observed state store."""

    def __init__(self, connection: PostgresConnection) -> None:
        self._connection = connection

    def put(self, record: ObservationRecord) -> ObservationRecord:
        self._connection.execute(
            """
            INSERT INTO cpk_observations
              (observation_id, workspace_id, subject_id, status, observed_at,
               payload, stale, graph_id, probe_kind, probe_outcome, endpoint_context)
            VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s, %s)
            """,
            (
                record.observation_id,
                record.workspace_id,
                record.subject_id,
                record.status.value,
                record.observed_at,
                _json(record.evidence.descriptor()),
                record.freshness is ObservationFreshness.STALE,
                record.graph_id,
                None if record.probe_kind is None else record.probe_kind.value,
                None if record.probe_outcome is None else record.probe_outcome.value,
                None
                if record.endpoint_context is None
                else record.endpoint_context.value,
            ),
        )
        return record

    def latest(self, workspace_id: str, subject_id: str) -> ObservationRecord | None:
        row = self._connection.execute(
            """
            SELECT observation_id, workspace_id, subject_id, status, observed_at,
                   payload, stale, graph_id, probe_kind, probe_outcome, endpoint_context
            FROM cpk_observations
            WHERE workspace_id = %s AND subject_id = %s
            ORDER BY observed_at DESC, observation_id DESC LIMIT 1
            """,
            (workspace_id, subject_id),
        ).fetchone()
        return self._observation(row) if row is not None else None

    def latest_for_workspace(self, workspace_id: str) -> tuple[ObservationRecord, ...]:
        rows = self._connection.execute(
            """
            SELECT DISTINCT ON (subject_id)
              observation_id, workspace_id, subject_id, status, observed_at,
              payload, stale, graph_id, probe_kind, probe_outcome, endpoint_context
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
            SELECT observation_id, workspace_id, subject_id, status, observed_at,
                   payload, stale, graph_id, probe_kind, probe_outcome, endpoint_context
            FROM cpk_observations
            WHERE workspace_id = %s AND subject_id = %s
            ORDER BY observed_at ASC, observation_id ASC
            """,
            (workspace_id, subject_id),
        ).fetchall()
        return tuple(self._observation(row) for row in rows)

    def _observation(self, row: tuple[Any, ...]) -> ObservationRecord:
        return ObservationRecord(
            observation_id=row[0],
            workspace_id=row[1],
            subject_id=row[2],
            status=ObservationStatus(row[3]),
            observed_at=row[4],
            evidence=BoundedEvidence.from_mapping(row[5]),
            freshness=(
                ObservationFreshness.STALE
                if row[6]
                else ObservationFreshness.FRESH
            ),
            graph_id=row[7],
            probe_kind=None if row[8] is None else ProbeKind(row[8]),
            probe_outcome=None if row[9] is None else ProbeOutcome(row[9]),
            endpoint_context=(
                None if row[10] is None else EndpointContext(row[10])
            ),
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
