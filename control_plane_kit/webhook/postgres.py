"""Postgres stores owned only by the webhook-delivery application."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
from typing import Any, Mapping, Protocol

from control_plane_kit.domains.webhook.language import (
    MAX_WEBHOOK_PAYLOAD_BYTES,
    WebhookAttemptOutcome,
    WebhookClaim,
    WebhookClaimed,
    WebhookContentType,
    WebhookDeliveryIdentity,
    WebhookDeliveryIntent,
    WebhookDeliveryState,
    WebhookDeliveryStatus,
    WebhookEndpoint,
    WebhookEnqueued,
    WebhookEvent,
    WebhookPayload,
    WebhookRetryPolicy,
    WebhookSigning,
    WebhookSigningAlgorithm,
    webhook_event_descriptor,
    webhook_event_from_descriptor,
)
from control_plane_kit.core.secrets import SecretReference


WEBHOOK_POSTGRES_SCHEMA = f"""
CREATE TABLE IF NOT EXISTS cpk_webhook_intents (
  workspace_id text NOT NULL,
  delivery_id text NOT NULL,
  command_id text NOT NULL UNIQUE,
  intent_fingerprint text NOT NULL,
  endpoint_id text NOT NULL,
  endpoint_url text NOT NULL,
  endpoint_scheme text NOT NULL,
  content_type text NOT NULL,
  payload bytea NOT NULL,
  content_digest text NOT NULL,
  max_attempts integer NOT NULL,
  initial_backoff_ms integer NOT NULL,
  maximum_backoff_ms integer NOT NULL,
  deadline_seconds integer NOT NULL,
  enqueued_at timestamptz NOT NULL,
  deadline_at timestamptz NOT NULL,
  signing_reference_id text,
  signing_algorithm text,
  signing_header_name text,
  PRIMARY KEY (workspace_id, delivery_id),
  CONSTRAINT cpk_webhook_intent_scheme_check CHECK (
    endpoint_scheme IN ('http', 'https')
  ),
  CONSTRAINT cpk_webhook_intent_content_type_check CHECK (
    content_type IN (
      'application/json',
      'application/cloudevents+json',
      'application/octet-stream'
    )
  ),
  CONSTRAINT cpk_webhook_intent_payload_check CHECK (
    octet_length(payload) BETWEEN 1 AND {MAX_WEBHOOK_PAYLOAD_BYTES}
    AND content_digest ~ '^[0-9a-f]{{64}}$'
  ),
  CONSTRAINT cpk_webhook_intent_retry_check CHECK (
    max_attempts BETWEEN 1 AND 20
    AND initial_backoff_ms BETWEEN 1 AND 60000
    AND maximum_backoff_ms BETWEEN initial_backoff_ms AND 3600000
    AND deadline_seconds BETWEEN 1 AND 604800
    AND deadline_at = enqueued_at + make_interval(secs => deadline_seconds)
  ),
  CONSTRAINT cpk_webhook_intent_signing_shape_check CHECK (
    (
      signing_reference_id IS NULL
      AND signing_algorithm IS NULL
      AND signing_header_name IS NULL
    )
    OR
    (
      signing_reference_id IS NOT NULL
      AND signing_algorithm = 'hmac-sha256'
      AND signing_header_name IS NOT NULL
    )
  )
);

CREATE TABLE IF NOT EXISTS cpk_webhook_events (
  workspace_id text NOT NULL,
  delivery_id text NOT NULL,
  ordinal integer NOT NULL,
  variant text NOT NULL,
  descriptor jsonb NOT NULL,
  recorded_at timestamptz NOT NULL,
  PRIMARY KEY (workspace_id, delivery_id, ordinal),
  FOREIGN KEY (workspace_id, delivery_id)
    REFERENCES cpk_webhook_intents (workspace_id, delivery_id),
  CONSTRAINT cpk_webhook_event_ordinal_check CHECK (ordinal > 0),
  CONSTRAINT cpk_webhook_event_variant_check CHECK (
    variant IN (
      'enqueued', 'claimed', 'claim-released', 'attempt-started',
      'attempt-finished', 'retry-scheduled', 'dead-lettered',
      'operator-required'
    )
    AND descriptor ->> 'variant' = variant
  )
);

CREATE TABLE IF NOT EXISTS cpk_webhook_projections (
  workspace_id text NOT NULL,
  delivery_id text NOT NULL,
  status text NOT NULL,
  attempts_started integer NOT NULL,
  attempts_completed integer NOT NULL,
  updated_at timestamptz NOT NULL,
  active_claim_id text,
  active_worker_id text,
  active_claim_attempt integer,
  active_claimed_at timestamptz,
  active_lease_expires_at timestamptz,
  next_attempt_at timestamptz,
  last_outcome text,
  journal_version integer NOT NULL,
  PRIMARY KEY (workspace_id, delivery_id),
  FOREIGN KEY (workspace_id, delivery_id)
    REFERENCES cpk_webhook_intents (workspace_id, delivery_id),
  CONSTRAINT cpk_webhook_projection_status_check CHECK (
    status IN (
      'queued', 'claimed', 'in-flight', 'retry-scheduled', 'delivered',
      'failed', 'uncertain', 'dead-letter', 'operator-required'
    )
  ),
  CONSTRAINT cpk_webhook_projection_counts_check CHECK (
    attempts_started BETWEEN 0 AND 20
    AND attempts_completed BETWEEN 0 AND attempts_started
    AND journal_version > 0
  ),
  CONSTRAINT cpk_webhook_projection_claim_shape_check CHECK (
    (
      active_claim_id IS NULL
      AND active_worker_id IS NULL
      AND active_claim_attempt IS NULL
      AND active_claimed_at IS NULL
      AND active_lease_expires_at IS NULL
    )
    OR
    (
      active_claim_id IS NOT NULL
      AND active_worker_id IS NOT NULL
      AND active_claim_attempt IS NOT NULL
      AND active_claimed_at IS NOT NULL
      AND active_lease_expires_at > active_claimed_at
    )
  ),
  CONSTRAINT cpk_webhook_projection_state_shape_check CHECK (
    (
      status = 'queued'
      AND attempts_started = 0 AND attempts_completed = 0
      AND active_claim_id IS NULL AND next_attempt_at IS NULL
      AND last_outcome IS NULL
    )
    OR
    (
      status = 'claimed'
      AND attempts_started = attempts_completed
      AND active_claim_attempt = attempts_started + 1
      AND (
        (attempts_started = 0 AND last_outcome IS NULL AND next_attempt_at IS NULL)
        OR
        (attempts_started > 0 AND last_outcome = 'retryable-failure'
         AND next_attempt_at IS NOT NULL)
      )
    )
    OR
    (
      status = 'in-flight'
      AND attempts_started = attempts_completed + 1
      AND active_claim_attempt = attempts_started
      AND next_attempt_at IS NULL
      AND (last_outcome IS NULL OR last_outcome = 'retryable-failure')
    )
    OR
    (
      status = 'retry-scheduled'
      AND attempts_started = attempts_completed AND attempts_started > 0
      AND active_claim_id IS NULL AND next_attempt_at IS NOT NULL
      AND last_outcome = 'retryable-failure'
    )
    OR
    (
      status = 'delivered'
      AND attempts_started = attempts_completed AND attempts_started > 0
      AND active_claim_id IS NULL AND next_attempt_at IS NULL
      AND last_outcome = 'succeeded'
    )
    OR
    (
      status = 'failed'
      AND attempts_started = attempts_completed AND attempts_started > 0
      AND active_claim_id IS NULL AND next_attempt_at IS NULL
      AND last_outcome IN ('retryable-failure', 'terminal-failure')
    )
    OR
    (
      status IN ('uncertain', 'operator-required')
      AND attempts_started = attempts_completed AND attempts_started > 0
      AND active_claim_id IS NULL AND next_attempt_at IS NULL
      AND last_outcome = 'uncertain'
    )
    OR
    (
      status = 'dead-letter'
      AND attempts_started = attempts_completed AND attempts_started > 0
      AND active_claim_id IS NULL AND next_attempt_at IS NULL
      AND last_outcome IN ('retryable-failure', 'terminal-failure')
    )
  )
);

CREATE TABLE IF NOT EXISTS cpk_webhook_commands (
  command_id text PRIMARY KEY,
  workspace_id text NOT NULL,
  variant text NOT NULL,
  intent_fingerprint text NOT NULL,
  actor_id text NOT NULL,
  result_descriptor jsonb NOT NULL,
  recorded_at timestamptz NOT NULL,
  CONSTRAINT cpk_webhook_command_variant_check CHECK (
    variant IN (
      'enqueue', 'claim', 'release-claim', 'start-attempt',
      'finish-attempt', 'schedule-retry', 'dead-letter',
      'require-operator'
    )
  )
);
CREATE INDEX IF NOT EXISTS cpk_webhook_commands_workspace
  ON cpk_webhook_commands (workspace_id, recorded_at, command_id);
CREATE INDEX IF NOT EXISTS cpk_webhook_projection_ready
  ON cpk_webhook_projections (status, next_attempt_at, updated_at, delivery_id);
"""


class Connection(Protocol):
    def execute(self, query: str, params: tuple[object, ...] = ()) -> Any: ...


@dataclass(frozen=True, slots=True)
class PostgresWebhookProjectionRecord:
    state: WebhookDeliveryState
    journal_version: int


@dataclass(frozen=True, slots=True)
class PostgresWebhookCommandRecord:
    command_id: str
    intent_fingerprint: str
    result_descriptor: dict[str, object]


def install_webhook_schema(connection: Connection) -> None:
    """Install schema inside the caller-owned transaction without committing."""

    connection.execute(WEBHOOK_POSTGRES_SCHEMA)


class PostgresWebhookIntentStore:
    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def add(self, intent: WebhookDeliveryIntent) -> None:
        identity = intent.identity
        endpoint = intent.endpoint
        policy = intent.retry_policy
        signing = intent.signing
        self._connection.execute(
            """
            INSERT INTO cpk_webhook_intents
              (workspace_id, delivery_id, command_id, intent_fingerprint,
               endpoint_id, endpoint_url, endpoint_scheme, content_type,
               payload, content_digest, max_attempts, initial_backoff_ms,
               maximum_backoff_ms, deadline_seconds, enqueued_at, deadline_at,
               signing_reference_id, signing_algorithm, signing_header_name)
            VALUES (
              %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
              %s, %s, %s, %s, %s
            )
            """,
            (
                identity.workspace_id,
                identity.delivery_id,
                intent.command_id,
                intent.intent_fingerprint,
                endpoint.endpoint_id,
                endpoint.url,
                endpoint.scheme.value,
                intent.payload.content_type.value,
                intent.payload.body,
                intent.payload.content_digest,
                policy.max_attempts,
                policy.initial_backoff_ms,
                policy.maximum_backoff_ms,
                policy.deadline_seconds,
                intent.enqueued_at,
                intent.deadline_at,
                None if signing is None else signing.secret_reference.reference_id,
                None if signing is None else signing.algorithm.value,
                None if signing is None else signing.header_name,
            ),
        )

    def get(self, identity: WebhookDeliveryIdentity) -> WebhookDeliveryIntent | None:
        row = self._connection.execute(
            _INTENT_SELECT
            + " WHERE workspace_id = %s AND delivery_id = %s",
            (identity.workspace_id, identity.delivery_id),
        ).fetchone()
        return None if row is None else _intent(row)


class PostgresWebhookJournalStore:
    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def lock_delivery(self, identity: WebhookDeliveryIdentity) -> None:
        self._connection.execute(
            "SELECT pg_advisory_xact_lock(hashtext(%s))",
            (f"webhook:{identity.workspace_id}:{identity.delivery_id}",),
        )

    def append(
        self,
        identity: WebhookDeliveryIdentity,
        expected_ordinal: int,
        event: WebhookEvent,
    ) -> bool:
        if type(expected_ordinal) is not int or expected_ordinal < 1:
            raise ValueError("webhook expected ordinal must be positive")
        if _event_identity(event) != identity:
            raise ValueError("webhook event identity does not match journal")
        descriptor = webhook_event_descriptor(event)
        row = self._connection.execute(
            """
            INSERT INTO cpk_webhook_events
              (workspace_id, delivery_id, ordinal, variant, descriptor, recorded_at)
            SELECT %s, %s, %s, %s, %s::jsonb, %s
            WHERE %s = COALESCE((
              SELECT max(ordinal) + 1 FROM cpk_webhook_events
              WHERE workspace_id = %s AND delivery_id = %s
            ), 1)
            ON CONFLICT DO NOTHING
            RETURNING ordinal
            """,
            (
                identity.workspace_id,
                identity.delivery_id,
                expected_ordinal,
                descriptor["variant"],
                _json(descriptor),
                _event_recorded_at(event),
                expected_ordinal,
                identity.workspace_id,
                identity.delivery_id,
            ),
        ).fetchone()
        return row is not None

    def events_for(self, identity: WebhookDeliveryIdentity) -> tuple[WebhookEvent, ...]:
        rows = self._connection.execute(
            """
            SELECT descriptor FROM cpk_webhook_events
            WHERE workspace_id = %s AND delivery_id = %s
            ORDER BY ordinal
            """,
            (identity.workspace_id, identity.delivery_id),
        ).fetchall()
        return tuple(webhook_event_from_descriptor(row[0]) for row in rows)


class PostgresWebhookProjectionStore:
    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def add(self, state: WebhookDeliveryState, journal_version: int) -> None:
        self._connection.execute(
            _PROJECTION_INSERT,
            _projection_values(state, journal_version),
        )

    def get(
        self, identity: WebhookDeliveryIdentity
    ) -> PostgresWebhookProjectionRecord | None:
        row = self._connection.execute(
            _PROJECTION_SELECT
            + " WHERE projection.workspace_id = %s AND projection.delivery_id = %s",
            (identity.workspace_id, identity.delivery_id),
        ).fetchone()
        return None if row is None else _projection(row)

    def replace(
        self,
        state: WebhookDeliveryState,
        expected_journal_version: int,
        replacement_journal_version: int,
    ) -> bool:
        values = _projection_values(state, replacement_journal_version)
        row = self._connection.execute(
            """
            UPDATE cpk_webhook_projections SET
              status = %s, attempts_started = %s, attempts_completed = %s,
              updated_at = %s, active_claim_id = %s, active_worker_id = %s,
              active_claim_attempt = %s, active_claimed_at = %s,
              active_lease_expires_at = %s, next_attempt_at = %s,
              last_outcome = %s, journal_version = %s
            WHERE workspace_id = %s AND delivery_id = %s
              AND journal_version = %s
            RETURNING journal_version
            """,
            values[2:] + values[:2] + (expected_journal_version,),
        ).fetchone()
        return row is not None


class PostgresWebhookCommandStore:
    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def lock_command(self, command_id: str) -> None:
        self._connection.execute(
            "SELECT pg_advisory_xact_lock(hashtext(%s))",
            (f"webhook-command:{command_id}",),
        )

    def get(self, command_id: str) -> PostgresWebhookCommandRecord | None:
        row = self._connection.execute(
            """
            SELECT command_id, intent_fingerprint, result_descriptor
            FROM cpk_webhook_commands WHERE command_id = %s
            """,
            (command_id,),
        ).fetchone()
        return None if row is None else PostgresWebhookCommandRecord(row[0], row[1], row[2])

    def add(
        self,
        command_id: str,
        workspace_id: str,
        variant: str,
        intent_fingerprint: str,
        actor_id: str,
        result_descriptor: dict[str, object],
        recorded_at: datetime,
    ) -> None:
        self._connection.execute(
            """
            INSERT INTO cpk_webhook_commands
              (command_id, workspace_id, variant, intent_fingerprint,
               actor_id, result_descriptor, recorded_at)
            VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s)
            """,
            (
                command_id,
                workspace_id,
                variant,
                intent_fingerprint,
                actor_id,
                _json(result_descriptor),
                recorded_at,
            ),
        )


_INTENT_COLUMNS = """
workspace_id, delivery_id, command_id, intent_fingerprint,
endpoint_id, endpoint_url, endpoint_scheme, content_type, payload,
content_digest, max_attempts, initial_backoff_ms, maximum_backoff_ms,
deadline_seconds, enqueued_at, deadline_at, signing_reference_id,
signing_algorithm, signing_header_name
"""
_INTENT_SELECT = "SELECT " + _INTENT_COLUMNS + " FROM cpk_webhook_intents"

_PROJECTION_INSERT = """
INSERT INTO cpk_webhook_projections
  (workspace_id, delivery_id, status, attempts_started, attempts_completed,
   updated_at, active_claim_id, active_worker_id, active_claim_attempt,
   active_claimed_at, active_lease_expires_at, next_attempt_at, last_outcome,
   journal_version)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
"""

_PROJECTION_SELECT = """
SELECT projection.status, projection.attempts_started,
  projection.attempts_completed, projection.updated_at,
  projection.active_claim_id, projection.active_worker_id,
  projection.active_claim_attempt, projection.active_claimed_at,
  projection.active_lease_expires_at, projection.next_attempt_at,
  projection.last_outcome, projection.journal_version,
  intent.workspace_id, intent.delivery_id, intent.command_id,
  intent.intent_fingerprint, intent.endpoint_id, intent.endpoint_url,
  intent.endpoint_scheme, intent.content_type, intent.payload,
  intent.content_digest, intent.max_attempts, intent.initial_backoff_ms,
  intent.maximum_backoff_ms, intent.deadline_seconds, intent.enqueued_at,
  intent.deadline_at, intent.signing_reference_id, intent.signing_algorithm,
  intent.signing_header_name
FROM cpk_webhook_projections projection
JOIN cpk_webhook_intents intent
  ON intent.workspace_id = projection.workspace_id
 AND intent.delivery_id = projection.delivery_id
"""


def _intent(row: tuple[Any, ...]) -> WebhookDeliveryIntent:
    identity = WebhookDeliveryIdentity(row[0], row[1])
    signing = None
    if row[16] is not None:
        signing = WebhookSigning(
            SecretReference(row[16]),
            WebhookSigningAlgorithm(row[17]),
            row[18],
        )
    intent = WebhookDeliveryIntent(
        row[2],
        identity,
        WebhookEndpoint(row[4], row[5]),
        WebhookPayload(WebhookContentType(row[7]), bytes(row[8])),
        WebhookRetryPolicy(row[10], row[11], row[12], row[13]),
        row[14],
        signing,
    )
    if intent.endpoint.scheme.value != row[6]:
        raise ValueError("stored webhook endpoint scheme does not match URL")
    if intent.payload.content_digest != row[9]:
        raise ValueError("stored webhook payload digest does not match content")
    if intent.deadline_at != row[15]:
        raise ValueError("stored webhook deadline does not match retry policy")
    if intent.intent_fingerprint != row[3]:
        raise ValueError("stored webhook intent fingerprint does not match content")
    return intent


def _projection_values(
    state: WebhookDeliveryState,
    journal_version: int,
) -> tuple[object, ...]:
    if type(journal_version) is not int or journal_version < 1:
        raise ValueError("webhook journal version must be positive")
    claim = state.active_claim
    identity = state.intent.identity
    return (
        identity.workspace_id,
        identity.delivery_id,
        state.status.value,
        state.attempts_started,
        state.attempts_completed,
        state.updated_at,
        None if claim is None else claim.claim_id,
        None if claim is None else claim.worker_id,
        None if claim is None else claim.attempt_number,
        None if claim is None else claim.claimed_at,
        None if claim is None else claim.lease_expires_at,
        state.next_attempt_at,
        None if state.last_outcome is None else state.last_outcome.value,
        journal_version,
    )


def _projection(row: tuple[Any, ...]) -> PostgresWebhookProjectionRecord:
    intent = _intent(row[12:])
    claim = None
    if row[4] is not None:
        claim = WebhookClaim(
            intent.identity,
            row[4],
            row[5],
            row[6],
            row[7],
            row[8],
        )
    state = WebhookDeliveryState(
        intent=intent,
        status=WebhookDeliveryStatus(row[0]),
        attempts_started=row[1],
        attempts_completed=row[2],
        updated_at=row[3],
        active_claim=claim,
        next_attempt_at=row[9],
        last_outcome=None if row[10] is None else WebhookAttemptOutcome(row[10]),
    )
    return PostgresWebhookProjectionRecord(state, row[11])


def _event_identity(event: WebhookEvent) -> WebhookDeliveryIdentity:
    if isinstance(event, WebhookEnqueued):
        return event.intent.identity
    if isinstance(event, WebhookClaimed):
        return event.claim.identity
    return event.identity


def _event_recorded_at(event: WebhookEvent) -> datetime:
    if isinstance(event, WebhookEnqueued):
        return event.intent.enqueued_at
    if isinstance(event, WebhookClaimed):
        return event.claim.claimed_at
    return event.recorded_at


def _json(value: Mapping[str, object]) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))
