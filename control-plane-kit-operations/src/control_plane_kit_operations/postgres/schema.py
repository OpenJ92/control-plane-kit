"""Caller-transactional Postgres schema installation for operations."""

from __future__ import annotations

from contextlib import AbstractContextManager
from enum import StrEnum
from typing import Any, Protocol

from jinja2 import Environment, StrictUndefined
from psycopg.types.json import Jsonb

from control_plane_kit_core.approval_subjects import ApprovalSubjectKind
from control_plane_kit_core.operations.commands import OperatorCommandKind
from control_plane_kit_core.gateway_delegation import (
    GatewayProbeAccessPath,
    GatewayProbeCommandKind,
)
from control_plane_kit_core.delegation_keys import (
    DelegationKeyAlgorithm,
    DelegationKeyPurpose,
)
from control_plane_kit_core.operations.lifecycle import (
    ActivityEventKind,
    ActivityEventScope,
    ActivityRunStatus,
    ExecutionRequestStatus,
    LifecycleOperationKind,
    activity_event_scope,
)
from control_plane_kit_core.planning import RiskLevel
from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_core.public_ingress import PublicIngressLifecycle
from control_plane_kit_core.secrets import SecretUseIntent
from control_plane_kit_core.probe_intents import (
    EndpointContext,
    ProbeKind,
    ProbeOutcome,
)
from control_plane_kit_core.types import RuntimeKind
from control_plane_kit_core.types import WorkspaceLifecycle
from control_plane_kit_operations.records import (
    GraphVersionRecord,
    ObservationFreshness,
    ObservationStatus,
    RealizedGraphProjectionKind,
    RealizedGraphProjectionRecord,
)
from control_plane_kit_operations.gateway_probes import GatewayProbeAttemptStatus
from control_plane_kit_operations.gateway_key_rotations import (
    GatewayKeyRotationDeploymentPhase,
    GatewayKeyRotationDeploymentStatus,
    GatewayKeyRotationStatus,
)
from control_plane_kit_operations.delegation_signing_keys import (
    RegisteredDelegationSigningKeyStatus,
)
from control_plane_kit_operations.ingress_authorities import (
    GeneratedSecretPurpose,
    IngressAuthorityProviderKind,
    OwnedIngressResourceStatus,
    RegisteredIngressAuthorityStatus,
)
from control_plane_kit_operations.runtime_authorities import (
    RegisteredRuntimeAuthorityDeliveryStatus,
    RegisteredRuntimeAuthorityStatus,
    RuntimeAuthorityKind,
)
from control_plane_kit_operations.secret_providers import (
    RegisteredSecretProviderStatus,
    RegisteredSecretReferenceStatus,
    SecretProviderKind,
)
from control_plane_kit_operations.postgres.migrations import (
    SchemaMigration,
    SchemaMigrationError,
    SchemaMigrationRegistry,
)


class PostgresConnection(Protocol):
    """Small connection protocol satisfied by psycopg connections."""

    def execute(self, query: str, params: tuple[object, ...] = ()) -> Any: ...


class MigrationPostgresConnection(PostgresConnection, Protocol):
    """Postgres capabilities required by the migration interpreter."""

    @property
    def autocommit(self) -> bool: ...

    def transaction(self) -> AbstractContextManager[object]: ...


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


class _RegisteredImagePullAuthorityStatus(StrEnum):
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
    if isinstance(values, frozenset):
        values = tuple(sorted(values, key=lambda value: value.value))
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
  current_realized_projection_id text,
  desired_realized_projection_id text,
  desired_graph_revision bigint NOT NULL DEFAULT 0,
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

CREATE TABLE IF NOT EXISTS cpk_realized_graph_projections (
  projection_id text PRIMARY KEY,
  workspace_id text NOT NULL REFERENCES cpk_workspaces(workspace_id),
  source_authored_graph_id text NOT NULL,
  projection_kind text NOT NULL,
  projection_key text NOT NULL,
  projection_digest text NOT NULL,
  graph_descriptor jsonb NOT NULL,
  created_by text NOT NULL,
  created_at text NOT NULL,
  CONSTRAINT cpk_realized_graph_projection_source
    FOREIGN KEY (source_authored_graph_id, workspace_id)
    REFERENCES cpk_graph_versions(graph_id, workspace_id),
  CONSTRAINT cpk_realized_graph_projection_kind_check
    CHECK (projection_kind IN ({{ realized_graph_projection_kinds | sql_values }})),
  CONSTRAINT cpk_realized_graph_projection_digest_check
    CHECK (projection_digest ~ '^[0-9a-f]{64}$'),
  CONSTRAINT cpk_realized_graph_projection_identity
    UNIQUE (
      workspace_id,
      source_authored_graph_id,
      projection_kind,
      projection_key
    ),
  CONSTRAINT cpk_realized_graph_projection_workspace_identity
    UNIQUE (projection_id, workspace_id),
  CONSTRAINT cpk_realized_graph_projection_source_identity
    UNIQUE (projection_id, source_authored_graph_id)
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

CREATE TABLE IF NOT EXISTS cpk_gateway_probe_attempts (
  probe_id text PRIMARY KEY,
  workspace_id text NOT NULL REFERENCES cpk_workspaces(workspace_id),
  request_id text NOT NULL,
  actor_id text NOT NULL,
  current_graph_id text NOT NULL REFERENCES cpk_graph_versions(graph_id),
  gateway_node_id text NOT NULL,
  gateway_runtime_id text NOT NULL,
  access_path text NOT NULL DEFAULT 'runtime-private',
  probe_kind text NOT NULL,
  target_id text NOT NULL,
  request_digest text NOT NULL,
  issuer text NOT NULL,
  key_id text NOT NULL,
  audience text NOT NULL,
  grant_jti text NOT NULL UNIQUE,
  issued_at bigint NOT NULL,
  expires_at bigint NOT NULL,
  status text NOT NULL,
  requested_at text NOT NULL,
  intent_fingerprint text NOT NULL,
  completed_at text,
  result_code text,
  evidence jsonb NOT NULL DEFAULT '{}'::jsonb,
  CONSTRAINT cpk_gateway_probe_request_identity
    UNIQUE (workspace_id, request_id),
  CONSTRAINT cpk_gateway_probe_status_check
    CHECK (status IN ({{ gateway_probe_attempt_statuses | sql_values }})),
  CONSTRAINT cpk_gateway_probe_kind_check
    CHECK (probe_kind IN ({{ gateway_probe_command_kinds | sql_values }})),
  CONSTRAINT cpk_gateway_probe_access_path_check
    CHECK (access_path IN ({{ gateway_probe_access_paths | sql_values }})),
  CONSTRAINT cpk_gateway_probe_digest_check
    CHECK (request_digest ~ '^[0-9a-f]{64}$'),
  CONSTRAINT cpk_gateway_probe_time_check
    CHECK (issued_at >= 0 AND expires_at > issued_at),
  CONSTRAINT cpk_gateway_probe_completion_check
    CHECK (
      (
        status = 'intended'
        AND completed_at IS NULL
        AND result_code IS NULL
      )
      OR
      (
        status <> 'intended'
        AND completed_at IS NOT NULL
        AND result_code IS NOT NULL
      )
    )
);

CREATE INDEX IF NOT EXISTS cpk_gateway_probe_workspace_timeline
  ON cpk_gateway_probe_attempts (workspace_id, issued_at DESC, probe_id DESC);

ALTER TABLE cpk_registered_products
  ADD COLUMN IF NOT EXISTS descriptor_content text;

ALTER TABLE cpk_gateway_probe_attempts
  ADD COLUMN IF NOT EXISTS access_path text NOT NULL DEFAULT 'runtime-private';

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname = 'cpk_gateway_probe_access_path_check'
      AND conrelid = 'cpk_gateway_probe_attempts'::regclass
  ) THEN
    ALTER TABLE cpk_gateway_probe_attempts
      ADD CONSTRAINT cpk_gateway_probe_access_path_check
      CHECK (access_path IN ({{ gateway_probe_access_paths | sql_values }}));
  END IF;
END
$$;

CREATE TABLE IF NOT EXISTS cpk_image_pull_authorities (
  authority_id text PRIMARY KEY,
  workspace_id text NOT NULL REFERENCES cpk_workspaces(workspace_id),
  authority jsonb NOT NULL,
  registry text NOT NULL,
  repository text,
  credential_reference text NOT NULL,
  admitted_by text NOT NULL,
  admitted_at text NOT NULL,
  status text NOT NULL,
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
  CONSTRAINT cpk_image_pull_authorities_status_check
    CHECK (status IN ({{ registered_image_pull_authority_statuses | sql_values }})),
  CONSTRAINT cpk_image_pull_authorities_reference_check
    CHECK (credential_reference LIKE 'secret://%')
);

CREATE INDEX IF NOT EXISTS cpk_image_pull_authorities_active_scope
  ON cpk_image_pull_authorities (workspace_id, registry, repository, status);

CREATE TABLE IF NOT EXISTS cpk_runtime_authorities (
  registration_id text PRIMARY KEY,
  workspace_id text NOT NULL REFERENCES cpk_workspaces(workspace_id),
  authority_ref text NOT NULL,
  runtime_kind text NOT NULL,
  authority_kind text NOT NULL,
  authority jsonb NOT NULL,
  credential_references jsonb NOT NULL DEFAULT '{}'::jsonb,
  admitted_by text NOT NULL,
  admitted_at text NOT NULL,
  status text NOT NULL,
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
  CONSTRAINT cpk_runtime_authorities_status_check
    CHECK (status IN ({{ registered_runtime_authority_statuses | sql_values }})),
  CONSTRAINT cpk_runtime_authorities_runtime_kind_check
    CHECK (runtime_kind IN ({{ runtime_kinds | sql_values }})),
  CONSTRAINT cpk_runtime_authorities_authority_kind_check
    CHECK (authority_kind IN ({{ runtime_authority_kinds | sql_values }})),
  CONSTRAINT cpk_runtime_authorities_reference_check
    CHECK (authority_ref ~ '^[a-z][a-z0-9._-]{0,127}$'),
  CONSTRAINT cpk_runtime_authorities_credential_shape_check
    CHECK (jsonb_typeof(credential_references) = 'object')
);

CREATE UNIQUE INDEX IF NOT EXISTS cpk_runtime_authorities_active_ref
  ON cpk_runtime_authorities (workspace_id, authority_ref)
  WHERE status = 'active';

CREATE TABLE IF NOT EXISTS cpk_runtime_authority_deliveries (
  delivery_id text PRIMARY KEY,
  workspace_id text NOT NULL REFERENCES cpk_workspaces(workspace_id),
  authority_ref text NOT NULL,
  delivery_kind text NOT NULL,
  delivery jsonb NOT NULL,
  secret_references jsonb NOT NULL DEFAULT '[]'::jsonb,
  admitted_by text NOT NULL,
  admitted_at text NOT NULL,
  status text NOT NULL,
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
  CONSTRAINT cpk_runtime_authority_deliveries_status_check
    CHECK (status IN ({{ registered_runtime_authority_delivery_statuses | sql_values }})),
  CONSTRAINT cpk_runtime_authority_deliveries_reference_check
    CHECK (authority_ref ~ '^[a-z][a-z0-9._-]{0,127}$'),
  CONSTRAINT cpk_runtime_authority_deliveries_delivery_shape_check
    CHECK (jsonb_typeof(delivery) = 'object'),
  CONSTRAINT cpk_runtime_authority_deliveries_secret_refs_shape_check
    CHECK (jsonb_typeof(secret_references) = 'array')
);

CREATE UNIQUE INDEX IF NOT EXISTS cpk_runtime_authority_deliveries_active_ref
  ON cpk_runtime_authority_deliveries (workspace_id, authority_ref)
  WHERE status = 'active';

CREATE TABLE IF NOT EXISTS cpk_ingress_authorities (
  registration_id text PRIMARY KEY,
  workspace_id text NOT NULL REFERENCES cpk_workspaces(workspace_id),
  authority_ref text NOT NULL,
  provider_kind text NOT NULL,
  authority jsonb NOT NULL,
  credential_references jsonb NOT NULL DEFAULT '{}'::jsonb,
  allowed_hostname_pattern text NOT NULL,
  admitted_by text NOT NULL,
  admitted_at text NOT NULL,
  status text NOT NULL,
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
  CONSTRAINT cpk_ingress_authorities_status_check
    CHECK (status IN ({{ registered_ingress_authority_statuses | sql_values }})),
  CONSTRAINT cpk_ingress_authorities_provider_kind_check
    CHECK (provider_kind IN ({{ ingress_authority_provider_kinds | sql_values }})),
  CONSTRAINT cpk_ingress_authorities_reference_check
    CHECK (authority_ref ~ '^[a-z][a-z0-9._-]{0,127}$'),
  CONSTRAINT cpk_ingress_authorities_authority_shape_check
    CHECK (jsonb_typeof(authority) = 'object'),
  CONSTRAINT cpk_ingress_authorities_credential_shape_check
    CHECK (jsonb_typeof(credential_references) = 'object')
);

CREATE UNIQUE INDEX IF NOT EXISTS cpk_ingress_authorities_active_ref
  ON cpk_ingress_authorities (workspace_id, authority_ref)
  WHERE status = 'active';

CREATE TABLE IF NOT EXISTS cpk_secret_providers (
  registration_id text PRIMARY KEY,
  workspace_id text NOT NULL REFERENCES cpk_workspaces(workspace_id),
  provider_id text NOT NULL,
  provider_kind text NOT NULL,
  display_name text NOT NULL,
  endpoint_reference text NOT NULL,
  credential_reference text NOT NULL,
  allowed_reference_prefixes jsonb NOT NULL,
  allowed_intents jsonb NOT NULL,
  admitted_by text NOT NULL,
  admitted_at text NOT NULL,
  status text NOT NULL,
  supersedes_registration_id text,
  revoked_by text,
  revoked_at text,
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
  UNIQUE (registration_id, workspace_id),
  CONSTRAINT cpk_secret_providers_status_check
    CHECK (status IN ({{ registered_secret_provider_statuses | sql_values }})),
  CONSTRAINT cpk_secret_providers_kind_check
    CHECK (provider_kind IN ({{ secret_provider_kinds | sql_values }})),
  CONSTRAINT cpk_secret_providers_id_check
    CHECK (provider_id ~ '^[a-z][a-z0-9-]{0,62}$'),
  CONSTRAINT cpk_secret_providers_endpoint_reference_check
    CHECK (endpoint_reference ~ '^[a-z][a-z0-9._-]{0,127}$'),
  CONSTRAINT cpk_secret_providers_credential_reference_check
    CHECK (credential_reference ~ '^secret://[a-z][a-z0-9-]{0,62}/[A-Za-z0-9._/-]+$'),
  CONSTRAINT cpk_secret_providers_prefixes_shape_check
    CHECK (jsonb_typeof(allowed_reference_prefixes) = 'array'),
  CONSTRAINT cpk_secret_providers_intents_shape_check
    CHECK (jsonb_typeof(allowed_intents) = 'array'),
  CONSTRAINT cpk_secret_providers_metadata_shape_check
    CHECK (jsonb_typeof(metadata) = 'object'),
  CONSTRAINT cpk_secret_providers_revocation_evidence_check
    CHECK (
      (status = 'revoked' AND revoked_by IS NOT NULL AND revoked_at IS NOT NULL)
      OR (status <> 'revoked' AND revoked_by IS NULL AND revoked_at IS NULL)
    ),
  CONSTRAINT cpk_secret_providers_supersedes_fk
    FOREIGN KEY (supersedes_registration_id, workspace_id)
    REFERENCES cpk_secret_providers (registration_id, workspace_id)
);

CREATE UNIQUE INDEX IF NOT EXISTS cpk_secret_providers_active_identity
  ON cpk_secret_providers (workspace_id, provider_id)
  WHERE status = 'active';

CREATE INDEX IF NOT EXISTS cpk_secret_providers_history
  ON cpk_secret_providers (workspace_id, provider_id, admitted_at, registration_id);

CREATE TABLE IF NOT EXISTS cpk_secret_references (
  registration_id text PRIMARY KEY,
  workspace_id text NOT NULL REFERENCES cpk_workspaces(workspace_id),
  secret_reference text NOT NULL,
  provider_registration_id text NOT NULL,
  allowed_intents jsonb NOT NULL,
  admitted_by text NOT NULL,
  admitted_at text NOT NULL,
  status text NOT NULL,
  supersedes_registration_id text,
  revoked_by text,
  revoked_at text,
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
  UNIQUE (registration_id, workspace_id),
  CONSTRAINT cpk_secret_references_status_check
    CHECK (status IN ({{ registered_secret_reference_statuses | sql_values }})),
  CONSTRAINT cpk_secret_references_reference_check
    CHECK (secret_reference ~ '^secret://[a-z][a-z0-9-]{0,62}/[A-Za-z0-9._/-]+$'),
  CONSTRAINT cpk_secret_references_intents_shape_check
    CHECK (jsonb_typeof(allowed_intents) = 'array'),
  CONSTRAINT cpk_secret_references_metadata_shape_check
    CHECK (jsonb_typeof(metadata) = 'object'),
  CONSTRAINT cpk_secret_references_revocation_evidence_check
    CHECK (
      (status = 'revoked' AND revoked_by IS NOT NULL AND revoked_at IS NOT NULL)
      OR (status <> 'revoked' AND revoked_by IS NULL AND revoked_at IS NULL)
    ),
  CONSTRAINT cpk_secret_references_provider_fk
    FOREIGN KEY (provider_registration_id, workspace_id)
    REFERENCES cpk_secret_providers (registration_id, workspace_id),
  CONSTRAINT cpk_secret_references_supersedes_fk
    FOREIGN KEY (supersedes_registration_id, workspace_id)
    REFERENCES cpk_secret_references (registration_id, workspace_id)
);

CREATE UNIQUE INDEX IF NOT EXISTS cpk_secret_references_active_reference
  ON cpk_secret_references (workspace_id, secret_reference)
  WHERE status = 'active';

CREATE INDEX IF NOT EXISTS cpk_secret_references_history
  ON cpk_secret_references (
    workspace_id,
    secret_reference,
    admitted_at,
    registration_id
  );

CREATE TABLE IF NOT EXISTS cpk_delegation_signing_keys (
  registration_id text PRIMARY KEY,
  workspace_id text NOT NULL REFERENCES cpk_workspaces(workspace_id),
  purpose text NOT NULL,
  issuer text NOT NULL,
  key_id text NOT NULL,
  algorithm text NOT NULL,
  public_key_pem text NOT NULL,
  public_fingerprint_sha256 text NOT NULL,
  private_key_reference text NOT NULL,
  admitted_by text NOT NULL,
  admitted_at text NOT NULL,
  status text NOT NULL,
  activated_by text,
  activated_at text,
  retired_by text,
  retired_at text,
  revoked_by text,
  revoked_at text,
  UNIQUE (registration_id, workspace_id),
  UNIQUE (workspace_id, purpose, issuer, key_id),
  CONSTRAINT cpk_delegation_signing_keys_registration_check
    CHECK (registration_id ~ '^dkey_[0-9a-f]{64}$'),
  CONSTRAINT cpk_delegation_signing_keys_purpose_check
    CHECK (purpose IN ({{ delegation_key_purposes | sql_values }})),
  CONSTRAINT cpk_delegation_signing_keys_algorithm_check
    CHECK (algorithm IN ({{ delegation_key_algorithms | sql_values }})),
  CONSTRAINT cpk_delegation_signing_keys_status_check
    CHECK (status IN ({{ delegation_signing_key_statuses | sql_values }})),
  CONSTRAINT cpk_delegation_signing_keys_issuer_check
    CHECK (issuer ~ '^[a-z][a-z0-9._-]{0,127}$'),
  CONSTRAINT cpk_delegation_signing_keys_key_id_check
    CHECK (key_id ~ '^[a-z][a-z0-9._-]{0,127}$'),
  CONSTRAINT cpk_delegation_signing_keys_fingerprint_check
    CHECK (public_fingerprint_sha256 ~ '^[0-9a-f]{64}$'),
  CONSTRAINT cpk_delegation_signing_keys_private_reference_check
    CHECK (private_key_reference ~ '^secret://[a-z][a-z0-9-]{0,62}/[A-Za-z0-9._/-]+$'),
  CONSTRAINT cpk_delegation_signing_keys_activation_evidence_check
    CHECK ((activated_by IS NULL) = (activated_at IS NULL)),
  CONSTRAINT cpk_delegation_signing_keys_retirement_evidence_check
    CHECK ((retired_by IS NULL) = (retired_at IS NULL)),
  CONSTRAINT cpk_delegation_signing_keys_revocation_evidence_check
    CHECK ((revoked_by IS NULL) = (revoked_at IS NULL))
);

CREATE UNIQUE INDEX IF NOT EXISTS cpk_delegation_signing_keys_active_scope
  ON cpk_delegation_signing_keys (workspace_id, purpose, issuer)
  WHERE status = 'active';

CREATE INDEX IF NOT EXISTS cpk_delegation_signing_keys_verifier_set
  ON cpk_delegation_signing_keys (workspace_id, purpose, issuer, key_id)
  WHERE status IN ('active', 'verify-only');

CREATE TABLE IF NOT EXISTS cpk_gateway_key_rotations (
  rotation_id text PRIMARY KEY,
  workspace_id text NOT NULL REFERENCES cpk_workspaces(workspace_id),
  gateway_node_id text NOT NULL,
  purpose text NOT NULL,
  issuer text NOT NULL,
  old_key_id text NOT NULL,
  new_secret_reference text NOT NULL,
  key_generation_correlation text NOT NULL,
  maximum_grant_lifetime_seconds integer NOT NULL,
  clock_skew_seconds integer NOT NULL,
  correlation_id text NOT NULL,
  requested_by text NOT NULL,
  requested_at text NOT NULL,
  intent_fingerprint text NOT NULL,
  status text NOT NULL,
  version integer NOT NULL,
  approval_request_id text,
  approval_decision_id text,
  generation_provider_registration_id text,
  generation_action_digest text,
  new_key_id text,
  new_secret_version_id text,
  new_secret_version_number integer,
  new_key_activated_at text,
  drain_deadline_epoch bigint,
  old_key_retired_at text,
  old_secret_revoked_at text,
  failure_code text,
  updated_by text,
  updated_at text,
  UNIQUE (workspace_id, correlation_id),
  CONSTRAINT cpk_gateway_key_rotations_status_check
    CHECK (status IN ({{ gateway_key_rotation_statuses | sql_values }})),
  CONSTRAINT cpk_gateway_key_rotations_purpose_check
    CHECK (purpose IN ({{ delegation_key_purposes | sql_values }})),
  CONSTRAINT cpk_gateway_key_rotations_version_check CHECK (version > 0),
  CONSTRAINT cpk_gateway_key_rotations_lifetime_check
    CHECK (maximum_grant_lifetime_seconds BETWEEN 1 AND 300),
  CONSTRAINT cpk_gateway_key_rotations_skew_check
    CHECK (clock_skew_seconds BETWEEN 0 AND 60),
  CONSTRAINT cpk_gateway_key_rotations_secret_version_check
    CHECK ((new_key_id IS NULL) = (new_secret_version_id IS NULL)
      AND (new_secret_version_id IS NULL) = (new_secret_version_number IS NULL)),
  CONSTRAINT cpk_gateway_key_rotations_generation_checkpoint_check
    CHECK ((generation_provider_registration_id IS NULL)
      = (generation_action_digest IS NULL)),
  CONSTRAINT cpk_gateway_key_rotations_generation_digest_check
    CHECK (generation_action_digest IS NULL
      OR generation_action_digest ~ '^[0-9a-f]{64}$'),
  CONSTRAINT cpk_gateway_key_rotations_activation_check
    CHECK ((new_key_activated_at IS NULL) = (drain_deadline_epoch IS NULL)),
  CONSTRAINT cpk_gateway_key_rotations_retirement_check
    CHECK (old_secret_revoked_at IS NULL OR old_key_retired_at IS NOT NULL),
  CONSTRAINT cpk_gateway_key_rotations_failure_check
    CHECK ((status IN ('blocked', 'rejected')) = (failure_code IS NOT NULL)),
  CONSTRAINT cpk_gateway_key_rotations_fingerprint_check
    CHECK (intent_fingerprint ~ '^[0-9a-f]{64}$')
);

ALTER TABLE cpk_gateway_key_rotations
  ADD COLUMN IF NOT EXISTS generation_provider_registration_id text;
ALTER TABLE cpk_gateway_key_rotations
  ADD COLUMN IF NOT EXISTS generation_action_digest text;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname = 'cpk_gateway_key_rotations_generation_checkpoint_check'
      AND conrelid = 'cpk_gateway_key_rotations'::regclass
  ) THEN
    ALTER TABLE cpk_gateway_key_rotations
      ADD CONSTRAINT cpk_gateway_key_rotations_generation_checkpoint_check
      CHECK ((generation_provider_registration_id IS NULL)
        = (generation_action_digest IS NULL));
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname = 'cpk_gateway_key_rotations_generation_digest_check'
      AND conrelid = 'cpk_gateway_key_rotations'::regclass
  ) THEN
    ALTER TABLE cpk_gateway_key_rotations
      ADD CONSTRAINT cpk_gateway_key_rotations_generation_digest_check
      CHECK (generation_action_digest IS NULL
        OR generation_action_digest ~ '^[0-9a-f]{64}$');
  END IF;
END
$$;

CREATE UNIQUE INDEX IF NOT EXISTS cpk_gateway_key_rotations_nonterminal_binding
  ON cpk_gateway_key_rotations (workspace_id, gateway_node_id, purpose, issuer)
  WHERE status NOT IN ('completed', 'blocked', 'rejected');

CREATE TABLE IF NOT EXISTS cpk_gateway_key_rotation_revocations (
  rotation_id text PRIMARY KEY REFERENCES cpk_gateway_key_rotations(rotation_id),
  provider_registration_id text NOT NULL,
  secret_reference text NOT NULL,
  provider_version_id text NOT NULL,
  provider_version_number integer NOT NULL,
  revocation_id text NOT NULL,
  correlation_id text NOT NULL,
  action_digest text NOT NULL,
  prepared_at text NOT NULL,
  CONSTRAINT cpk_gateway_key_rotation_revocations_version_check
    CHECK (provider_version_number > 0),
  CONSTRAINT cpk_gateway_key_rotation_revocations_reference_check
    CHECK (secret_reference ~ '^secret://[a-z][a-z0-9-]{0,62}/[A-Za-z0-9._/-]+$'),
  CONSTRAINT cpk_gateway_key_rotation_revocations_digest_check
    CHECK (action_digest ~ '^[0-9a-f]{64}$')
);

CREATE TABLE IF NOT EXISTS cpk_gateway_key_rotation_transitions (
  rotation_id text NOT NULL REFERENCES cpk_gateway_key_rotations(rotation_id),
  transition_id text NOT NULL,
  from_status text NOT NULL,
  to_status text NOT NULL,
  from_version integer NOT NULL,
  to_version integer NOT NULL,
  transition_fingerprint text NOT NULL,
  advanced_by text NOT NULL,
  advanced_at text NOT NULL,
  failure_code text,
  PRIMARY KEY (rotation_id, transition_id),
  UNIQUE (rotation_id, to_version),
  CONSTRAINT cpk_gateway_key_rotation_transitions_from_status_check
    CHECK (from_status IN ({{ gateway_key_rotation_statuses | sql_values }})),
  CONSTRAINT cpk_gateway_key_rotation_transitions_to_status_check
    CHECK (to_status IN ({{ gateway_key_rotation_statuses | sql_values }})),
  CONSTRAINT cpk_gateway_key_rotation_transitions_version_check
    CHECK (from_version > 0 AND to_version = from_version + 1),
  CONSTRAINT cpk_gateway_key_rotation_transitions_fingerprint_check
    CHECK (transition_fingerprint ~ '^[0-9a-f]{64}$')
);

CREATE TABLE IF NOT EXISTS cpk_gateway_key_rotation_deployments (
  rotation_id text NOT NULL REFERENCES cpk_gateway_key_rotations(rotation_id),
  phase text NOT NULL,
  status text NOT NULL,
  session_id text NOT NULL,
  plan_id text NOT NULL,
  approval_request_id text NOT NULL,
  approval_decision_id text NOT NULL,
  execution_request_id text NOT NULL,
  run_id text NOT NULL,
  base_authored_graph_id text NOT NULL,
  base_realized_projection_id text NOT NULL,
  desired_authored_graph_id text NOT NULL,
  desired_realized_projection_id text NOT NULL,
  desired_revision integer NOT NULL,
  prepared_at text NOT NULL,
  accepted_current_graph_id text,
  accepted_current_projection_id text,
  accepted_at text,
  PRIMARY KEY (rotation_id, phase),
  CONSTRAINT cpk_gateway_key_rotation_deployments_phase_check
    CHECK (phase IN ({{ gateway_key_rotation_deployment_phases | sql_values }})),
  CONSTRAINT cpk_gateway_key_rotation_deployments_status_check
    CHECK (status IN ({{ gateway_key_rotation_deployment_statuses | sql_values }})),
  CONSTRAINT cpk_gateway_key_rotation_deployments_revision_check
    CHECK (desired_revision >= 0),
  CONSTRAINT cpk_gateway_key_rotation_deployments_acceptance_check CHECK (
    (status = 'accepted' AND accepted_current_graph_id IS NOT NULL
      AND accepted_current_projection_id IS NOT NULL AND accepted_at IS NOT NULL)
    OR
    (status = 'prepared' AND accepted_current_graph_id IS NULL
      AND accepted_current_projection_id IS NULL AND accepted_at IS NULL)
  )
);

CREATE TABLE IF NOT EXISTS cpk_secret_use_authorizations (
  authorization_id text PRIMARY KEY,
  workspace_id text NOT NULL REFERENCES cpk_workspaces(workspace_id),
  reference_registration_id text NOT NULL,
  provider_registration_id text NOT NULL,
  secret_reference text NOT NULL,
  use_intent text NOT NULL,
  actor_subject text NOT NULL,
  correlation_id text NOT NULL,
  requested_at text NOT NULL,
  intent_fingerprint text NOT NULL,
  operation_id text,
  session_id text,
  run_id text,
  activity_id text,
  effect_id text,
  probe_id text,
  UNIQUE (authorization_id, workspace_id),
  UNIQUE (workspace_id, correlation_id),
  CONSTRAINT cpk_secret_use_authorizations_reference_fk
    FOREIGN KEY (reference_registration_id, workspace_id)
    REFERENCES cpk_secret_references (registration_id, workspace_id),
  CONSTRAINT cpk_secret_use_authorizations_provider_fk
    FOREIGN KEY (provider_registration_id, workspace_id)
    REFERENCES cpk_secret_providers (registration_id, workspace_id),
  CONSTRAINT cpk_secret_use_authorizations_id_check
    CHECK (authorization_id ~ '^suse_[0-9a-f]{64}$'),
  CONSTRAINT cpk_secret_use_authorizations_reference_check
    CHECK (secret_reference ~ '^secret://[a-z][a-z0-9-]{0,62}/[A-Za-z0-9._/-]+$'),
  CONSTRAINT cpk_secret_use_authorizations_intent_check
    CHECK (use_intent IN ({{ secret_use_intents | sql_values }})),
  CONSTRAINT cpk_secret_use_authorizations_actor_check
    CHECK (actor_subject ~ '^[a-z][a-z0-9._-]{0,127}$'),
  CONSTRAINT cpk_secret_use_authorizations_correlation_check
    CHECK (correlation_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$'),
  CONSTRAINT cpk_secret_use_authorizations_fingerprint_check
    CHECK (intent_fingerprint ~ '^[0-9a-f]{64}$'),
  CONSTRAINT cpk_secret_use_authorizations_operation_check
    CHECK (operation_id IS NULL OR operation_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$'),
  CONSTRAINT cpk_secret_use_authorizations_session_check
    CHECK (session_id IS NULL OR session_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$'),
  CONSTRAINT cpk_secret_use_authorizations_run_check
    CHECK (run_id IS NULL OR run_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$'),
  CONSTRAINT cpk_secret_use_authorizations_activity_check
    CHECK (activity_id IS NULL OR activity_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$'),
  CONSTRAINT cpk_secret_use_authorizations_effect_check
    CHECK (effect_id IS NULL OR effect_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$'),
  CONSTRAINT cpk_secret_use_authorizations_probe_check
    CHECK (probe_id IS NULL OR probe_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$')
);

CREATE INDEX IF NOT EXISTS cpk_secret_use_authorizations_reference_history
  ON cpk_secret_use_authorizations (
    workspace_id,
    reference_registration_id,
    requested_at,
    authorization_id
  );

CREATE TABLE IF NOT EXISTS cpk_cloudflare_ingress_resources (
  workspace_id text NOT NULL REFERENCES cpk_workspaces(workspace_id),
  runtime_id text NOT NULL,
  ingress_id text NOT NULL,
  epoch integer NOT NULL DEFAULT 1,
  status text NOT NULL DEFAULT 'active',
  authority_ref text NOT NULL,
  provider_kind text NOT NULL,
  tunnel_name text NOT NULL,
  tunnel_id text NOT NULL,
  dns_record_id text NOT NULL,
  hostname text NOT NULL,
  zone_id text NOT NULL,
  lifecycle text NOT NULL,
  created_at text NOT NULL,
  observed_at text NOT NULL,
  source_run_id text NOT NULL,
  source_activity_id text NOT NULL,
  source_event_id text NOT NULL,
  removed_at text,
  removed_by_run_id text,
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
  PRIMARY KEY (workspace_id, ingress_id, epoch),
  CONSTRAINT cpk_cloudflare_ingress_resources_provider_kind_check
    CHECK (provider_kind = 'cloudflare'),
  CONSTRAINT cpk_cloudflare_ingress_resources_epoch_check
    CHECK (epoch > 0),
  CONSTRAINT cpk_cloudflare_ingress_resources_status_check
    CHECK (status IN ({{ owned_ingress_resource_statuses | sql_values }})),
  CONSTRAINT cpk_cloudflare_ingress_resources_lifecycle_check
    CHECK (lifecycle IN ({{ public_ingress_lifecycles | sql_values }})),
  CONSTRAINT cpk_cloudflare_ingress_resources_authority_ref_check
    CHECK (authority_ref ~ '^[a-z][a-z0-9._-]{0,127}$'),
  CONSTRAINT cpk_cloudflare_ingress_resources_removed_evidence_check
    CHECK (
      (status = 'removed' AND removed_at IS NOT NULL AND removed_by_run_id IS NOT NULL)
      OR (status <> 'removed' AND removed_at IS NULL AND removed_by_run_id IS NULL)
    ),
  CONSTRAINT cpk_cloudflare_ingress_resources_metadata_shape_check
    CHECK (jsonb_typeof(metadata) = 'object')
);

CREATE INDEX IF NOT EXISTS cpk_cloudflare_ingress_resources_workspace
  ON cpk_cloudflare_ingress_resources (workspace_id, observed_at DESC, ingress_id, epoch);

CREATE UNIQUE INDEX IF NOT EXISTS cpk_cloudflare_ingress_resources_active_key
  ON cpk_cloudflare_ingress_resources (workspace_id, ingress_id)
  WHERE status IN ('allocating', 'active', 'removing');

CREATE TABLE IF NOT EXISTS cpk_generated_ingress_secret_references (
  workspace_id text NOT NULL REFERENCES cpk_workspaces(workspace_id),
  purpose text NOT NULL,
  secret_ref text NOT NULL,
  recorded_at text NOT NULL,
  source_run_id text NOT NULL,
  source_activity_id text NOT NULL,
  source_event_id text NOT NULL,
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
  PRIMARY KEY (
    workspace_id,
    purpose,
    source_run_id,
    source_activity_id,
    source_event_id
  ),
  CONSTRAINT cpk_generated_ingress_secret_references_purpose_check
    CHECK (purpose IN ({{ generated_secret_purposes | sql_values }})),
  CONSTRAINT cpk_generated_ingress_secret_references_ref_check
    CHECK (secret_ref ~ '^secret://[a-z][a-z0-9-]{0,62}/[A-Za-z0-9._/-]+$'),
  CONSTRAINT cpk_generated_ingress_secret_references_metadata_shape_check
    CHECK (jsonb_typeof(metadata) = 'object')
);

CREATE UNIQUE INDEX IF NOT EXISTS cpk_generated_ingress_secret_references_secret_ref
  ON cpk_generated_ingress_secret_references (workspace_id, secret_ref);

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
    CHECK (action_type IN ({{ operation_action_kinds | sql_values }})),
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
  base_realized_projection_id text,
  desired_realized_projection_id text,
  desired_graph_revision bigint NOT NULL DEFAULT 0,
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
  plan_id text REFERENCES cpk_activity_plans(plan_id),
  rotation_id text,
  subject_kind text NOT NULL,
  subject_payload jsonb NOT NULL,
  review_digest text NOT NULL,
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
    CHECK (max_risk IN ({{ risk_levels | sql_values }})),
  CONSTRAINT cpk_approval_requests_subject_kind_check
    CHECK (subject_kind IN ({{ approval_subject_kinds | sql_values }})),
  CONSTRAINT cpk_approval_requests_review_digest_check
    CHECK (review_digest ~ '^[0-9a-f]{64}$'),
  CONSTRAINT cpk_approval_requests_subject_identity_check
    CHECK (
      (subject_kind = 'activity-plan' AND plan_id IS NOT NULL AND rotation_id IS NULL)
      OR
      (subject_kind = 'gateway-key-rotation' AND plan_id IS NULL AND rotation_id IS NOT NULL)
    )
);

ALTER TABLE cpk_approval_requests
  ADD COLUMN IF NOT EXISTS rotation_id text;
ALTER TABLE cpk_approval_requests
  ADD COLUMN IF NOT EXISTS subject_kind text;
ALTER TABLE cpk_approval_requests
  ADD COLUMN IF NOT EXISTS subject_payload jsonb;
ALTER TABLE cpk_approval_requests
  ADD COLUMN IF NOT EXISTS review_digest text;

UPDATE cpk_approval_requests
SET subject_kind = 'activity-plan',
    subject_payload = jsonb_build_object(
      'kind', 'activity-plan',
      'plan_id', plan_id
    ),
    review_digest = encode(
      sha256(convert_to('activity-plan:' || plan_id, 'UTF8')),
      'hex'
    )
WHERE subject_kind IS NULL;

ALTER TABLE cpk_approval_requests
  ALTER COLUMN plan_id DROP NOT NULL;
ALTER TABLE cpk_approval_requests
  ALTER COLUMN subject_kind SET NOT NULL;
ALTER TABLE cpk_approval_requests
  ALTER COLUMN subject_payload SET NOT NULL;
ALTER TABLE cpk_approval_requests
  ALTER COLUMN review_digest SET NOT NULL;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname = 'cpk_approval_requests_rotation_fk'
      AND conrelid = 'cpk_approval_requests'::regclass
  ) THEN
    ALTER TABLE cpk_approval_requests
      ADD CONSTRAINT cpk_approval_requests_rotation_fk
      FOREIGN KEY (rotation_id)
      REFERENCES cpk_gateway_key_rotations(rotation_id);
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname = 'cpk_approval_requests_subject_kind_check'
      AND conrelid = 'cpk_approval_requests'::regclass
  ) THEN
    ALTER TABLE cpk_approval_requests
      ADD CONSTRAINT cpk_approval_requests_subject_kind_check
      CHECK (subject_kind IN ({{ approval_subject_kinds | sql_values }}));
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname = 'cpk_approval_requests_review_digest_check'
      AND conrelid = 'cpk_approval_requests'::regclass
  ) THEN
    ALTER TABLE cpk_approval_requests
      ADD CONSTRAINT cpk_approval_requests_review_digest_check
      CHECK (review_digest ~ '^[0-9a-f]{64}$');
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname = 'cpk_approval_requests_subject_identity_check'
      AND conrelid = 'cpk_approval_requests'::regclass
  ) THEN
    ALTER TABLE cpk_approval_requests
      ADD CONSTRAINT cpk_approval_requests_subject_identity_check
      CHECK (
        (subject_kind = 'activity-plan' AND plan_id IS NOT NULL AND rotation_id IS NULL)
        OR
        (subject_kind = 'gateway-key-rotation' AND plan_id IS NULL AND rotation_id IS NOT NULL)
      );
  END IF;
END
$$;

CREATE UNIQUE INDEX IF NOT EXISTS cpk_approval_requests_idempotency
  ON cpk_approval_requests (session_id, idempotency_key)
  WHERE idempotency_key IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS cpk_approval_requests_rotation_identity
  ON cpk_approval_requests (rotation_id)
  WHERE rotation_id IS NOT NULL;

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

CREATE TABLE IF NOT EXISTS cpk_observations (
  observation_id text PRIMARY KEY,
  workspace_id text NOT NULL REFERENCES cpk_workspaces(workspace_id),
  subject_id text NOT NULL,
  status text NOT NULL,
  observed_at text NOT NULL,
  evidence jsonb NOT NULL DEFAULT '{}'::jsonb,
  freshness text NOT NULL,
  graph_id text,
  probe_kind text,
  probe_outcome text,
  endpoint_context text,
  CONSTRAINT cpk_observations_status_check
    CHECK (status IN ({{ observation_statuses | sql_values }})),
  CONSTRAINT cpk_observations_freshness_check
    CHECK (freshness IN ({{ observation_freshnesses | sql_values }})),
  CONSTRAINT cpk_observations_probe_kind_check
    CHECK (probe_kind IS NULL OR probe_kind IN ({{ probe_kinds | sql_values }})),
  CONSTRAINT cpk_observations_probe_outcome_check
    CHECK (probe_outcome IS NULL OR probe_outcome IN ({{ probe_outcomes | sql_values }})),
  CONSTRAINT cpk_observations_endpoint_context_check
    CHECK (endpoint_context IS NULL OR endpoint_context IN ({{ endpoint_contexts | sql_values }})),
  CONSTRAINT cpk_observations_correlation_check
    CHECK (
      (
        graph_id IS NULL
        AND probe_kind IS NULL
        AND probe_outcome IS NULL
      )
      OR
      (
        graph_id IS NOT NULL
        AND probe_kind IS NOT NULL
        AND probe_outcome IS NOT NULL
      )
    ),
  CONSTRAINT cpk_observations_process_endpoint_check
    CHECK (
      endpoint_context IS NULL
      OR probe_kind NOT IN ('process', 'readiness')
    )
);

CREATE INDEX IF NOT EXISTS cpk_observations_latest_subject
  ON cpk_observations (workspace_id, subject_id, observed_at DESC, observation_id DESC);

ALTER TABLE cpk_workspaces
  ADD COLUMN IF NOT EXISTS current_realized_projection_id text;
ALTER TABLE cpk_workspaces
  ADD COLUMN IF NOT EXISTS desired_realized_projection_id text;
ALTER TABLE cpk_workspaces
  ADD COLUMN IF NOT EXISTS desired_graph_revision bigint NOT NULL DEFAULT 0;

ALTER TABLE cpk_activity_plans
  ADD COLUMN IF NOT EXISTS base_realized_projection_id text;
ALTER TABLE cpk_activity_plans
  ADD COLUMN IF NOT EXISTS desired_realized_projection_id text;
ALTER TABLE cpk_activity_plans
  ADD COLUMN IF NOT EXISTS desired_graph_revision bigint NOT NULL DEFAULT 0;
"""


_GRAPH_LINEAGE_CONSTRAINTS = """
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname = 'cpk_realized_graph_projection_workspace_identity'
  ) THEN
    ALTER TABLE cpk_realized_graph_projections
      ADD CONSTRAINT cpk_realized_graph_projection_workspace_identity
      UNIQUE (projection_id, workspace_id);
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname = 'cpk_realized_graph_projection_source_identity'
  ) THEN
    ALTER TABLE cpk_realized_graph_projections
      ADD CONSTRAINT cpk_realized_graph_projection_source_identity
      UNIQUE (projection_id, source_authored_graph_id);
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname = 'cpk_workspaces_current_realized_projection_fk'
  ) THEN
    ALTER TABLE cpk_workspaces
      ADD CONSTRAINT cpk_workspaces_current_realized_projection_fk
      FOREIGN KEY (current_realized_projection_id, workspace_id)
      REFERENCES cpk_realized_graph_projections(projection_id, workspace_id);
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname = 'cpk_workspaces_desired_realized_projection_fk'
  ) THEN
    ALTER TABLE cpk_workspaces
      ADD CONSTRAINT cpk_workspaces_desired_realized_projection_fk
      FOREIGN KEY (desired_realized_projection_id, workspace_id)
      REFERENCES cpk_realized_graph_projections(projection_id, workspace_id);
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname = 'cpk_workspaces_current_projection_source_fk'
  ) THEN
    ALTER TABLE cpk_workspaces
      ADD CONSTRAINT cpk_workspaces_current_projection_source_fk
      FOREIGN KEY (current_realized_projection_id, current_graph_id)
      REFERENCES cpk_realized_graph_projections(
        projection_id, source_authored_graph_id
      );
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname = 'cpk_workspaces_desired_projection_source_fk'
  ) THEN
    ALTER TABLE cpk_workspaces
      ADD CONSTRAINT cpk_workspaces_desired_projection_source_fk
      FOREIGN KEY (desired_realized_projection_id, desired_graph_id)
      REFERENCES cpk_realized_graph_projections(
        projection_id, source_authored_graph_id
      );
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname = 'cpk_workspaces_current_lineage_check'
  ) THEN
    ALTER TABLE cpk_workspaces
      ADD CONSTRAINT cpk_workspaces_current_lineage_check
      CHECK ((current_graph_id IS NULL) = (current_realized_projection_id IS NULL));
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname = 'cpk_workspaces_desired_lineage_check'
  ) THEN
    ALTER TABLE cpk_workspaces
      ADD CONSTRAINT cpk_workspaces_desired_lineage_check
      CHECK ((desired_graph_id IS NULL) = (desired_realized_projection_id IS NULL));
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname = 'cpk_activity_plans_base_projection_source_fk'
  ) THEN
    ALTER TABLE cpk_activity_plans
      ADD CONSTRAINT cpk_activity_plans_base_projection_source_fk
      FOREIGN KEY (base_realized_projection_id, base_graph_id)
      REFERENCES cpk_realized_graph_projections(
        projection_id, source_authored_graph_id
      );
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname = 'cpk_activity_plans_desired_projection_source_fk'
  ) THEN
    ALTER TABLE cpk_activity_plans
      ADD CONSTRAINT cpk_activity_plans_desired_projection_source_fk
      FOREIGN KEY (desired_realized_projection_id, desired_graph_id)
      REFERENCES cpk_realized_graph_projections(
        projection_id, source_authored_graph_id
      );
  END IF;
END
$$;
"""


POSTGRES_SCHEMA = _SQL_ENVIRONMENT.from_string(_POSTGRES_SCHEMA_TEMPLATE).render(
    activity_event_kinds=tuple(ActivityEventKind),
    activity_event_run_kinds=_RUN_EVENT_KINDS,
    activity_event_step_kinds=_ACTIVITY_EVENT_KINDS,
    activity_run_statuses=tuple(ActivityRunStatus),
    approval_decision_kinds=tuple(_ApprovalDecisionKind),
    approval_subject_kinds=tuple(ApprovalSubjectKind),
    execution_request_statuses=tuple(ExecutionRequestStatus),
    operation_action_kinds=tuple(OperatorCommandKind) + tuple(LifecycleOperationKind),
    operation_session_statuses=tuple(_OperationsSessionStatus),
    operator_command_kinds=tuple(OperatorCommandKind),
    observation_freshnesses=tuple(ObservationFreshness),
    observation_statuses=tuple(ObservationStatus),
    public_ingress_lifecycles=tuple(PublicIngressLifecycle),
    owned_ingress_resource_statuses=tuple(OwnedIngressResourceStatus),
    policy_scopes=tuple(PolicyScope),
    endpoint_contexts=tuple(EndpointContext),
    probe_kinds=tuple(ProbeKind),
    probe_outcomes=tuple(ProbeOutcome),
    registered_product_statuses=tuple(_RegisteredProductStatus),
    registered_image_pull_authority_statuses=tuple(
        _RegisteredImagePullAuthorityStatus
    ),
    registered_runtime_authority_statuses=tuple(RegisteredRuntimeAuthorityStatus),
    registered_runtime_authority_delivery_statuses=tuple(
        RegisteredRuntimeAuthorityDeliveryStatus
    ),
    registered_ingress_authority_statuses=tuple(RegisteredIngressAuthorityStatus),
    registered_secret_provider_statuses=tuple(RegisteredSecretProviderStatus),
    registered_secret_reference_statuses=tuple(RegisteredSecretReferenceStatus),
    generated_secret_purposes=tuple(GeneratedSecretPurpose),
    ingress_authority_provider_kinds=tuple(IngressAuthorityProviderKind),
    secret_provider_kinds=tuple(SecretProviderKind),
    secret_use_intents=tuple(SecretUseIntent),
    gateway_probe_attempt_statuses=tuple(GatewayProbeAttemptStatus),
    gateway_probe_access_paths=tuple(GatewayProbeAccessPath),
    gateway_probe_command_kinds=tuple(GatewayProbeCommandKind),
    gateway_key_rotation_statuses=tuple(GatewayKeyRotationStatus),
    gateway_key_rotation_deployment_phases=tuple(GatewayKeyRotationDeploymentPhase),
    gateway_key_rotation_deployment_statuses=tuple(GatewayKeyRotationDeploymentStatus),
    delegation_key_algorithms=tuple(DelegationKeyAlgorithm),
    delegation_key_purposes=tuple(DelegationKeyPurpose),
    delegation_signing_key_statuses=tuple(RegisteredDelegationSigningKeyStatus),
    risk_levels=tuple(RiskLevel),
    realized_graph_projection_kinds=tuple(RealizedGraphProjectionKind),
    runtime_authority_kinds=tuple(RuntimeAuthorityKind),
    runtime_kinds=tuple(RuntimeKind),
    settled_run_statuses=_SETTLED_RUN_STATUSES,
    started_run_statuses=_STARTED_RUN_STATUSES,
    workspace_lifecycles=tuple(WorkspaceLifecycle),
)

_POSTGRES_SCHEMA_V2_SQL = r"""
DO $cpk$
BEGIN
  IF EXISTS (
    SELECT 1
    FROM (
      SELECT created_at AS value FROM cpk_operation_sessions
      UNION ALL SELECT closed_at FROM cpk_operation_sessions
      UNION ALL SELECT created_at FROM cpk_operation_actions
      UNION ALL SELECT created_at FROM cpk_activity_plans
      UNION ALL SELECT requested_at FROM cpk_approval_requests
      UNION ALL SELECT decided_at FROM cpk_approval_decisions
      UNION ALL SELECT requested_at FROM cpk_execution_requests
      UNION ALL SELECT claimed_at FROM cpk_execution_requests
      UNION ALL SELECT lease_expires_at FROM cpk_execution_requests
      UNION ALL SELECT created_at FROM cpk_activity_runs
      UNION ALL SELECT started_at FROM cpk_activity_runs
      UNION ALL SELECT settled_at FROM cpk_activity_runs
      UNION ALL SELECT occurred_at FROM cpk_activity_events
      UNION ALL SELECT observed_at FROM cpk_observations
    ) AS retained
    WHERE value IS NOT NULL
      AND CASE
        WHEN octet_length(value) > 27 THEN TRUE
        WHEN value !~ '^[0-9]{4}-(0[1-9]|1[0-2])-(0[1-9]|[12][0-9]|3[01])T([01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9]([.][0-9]{6})?Z$'
          THEN TRUE
        WHEN value ~ '[.]000000Z$' THEN TRUE
        ELSE FALSE
      END
  ) THEN
    RAISE EXCEPTION USING
      ERRCODE = 'P1110',
      MESSAGE = 'coordination timestamps are not canonical UTC';
  END IF;
END
$cpk$;

ALTER TABLE cpk_operation_sessions
  ALTER COLUMN created_at TYPE timestamptz USING created_at::timestamptz,
  ALTER COLUMN closed_at TYPE timestamptz USING closed_at::timestamptz;
ALTER TABLE cpk_operation_actions
  ALTER COLUMN created_at TYPE timestamptz USING created_at::timestamptz;
ALTER TABLE cpk_activity_plans
  ALTER COLUMN created_at TYPE timestamptz USING created_at::timestamptz;
ALTER TABLE cpk_approval_requests
  ALTER COLUMN requested_at TYPE timestamptz USING requested_at::timestamptz;
ALTER TABLE cpk_approval_decisions
  ALTER COLUMN decided_at TYPE timestamptz USING decided_at::timestamptz;
ALTER TABLE cpk_execution_requests
  ALTER COLUMN requested_at TYPE timestamptz USING requested_at::timestamptz,
  ALTER COLUMN claimed_at TYPE timestamptz USING claimed_at::timestamptz,
  ALTER COLUMN lease_expires_at TYPE timestamptz USING lease_expires_at::timestamptz;
ALTER TABLE cpk_activity_runs
  ALTER COLUMN created_at TYPE timestamptz USING created_at::timestamptz,
  ALTER COLUMN started_at TYPE timestamptz USING started_at::timestamptz,
  ALTER COLUMN settled_at TYPE timestamptz USING settled_at::timestamptz;
ALTER TABLE cpk_activity_events
  ALTER COLUMN occurred_at TYPE timestamptz USING occurred_at::timestamptz;
ALTER TABLE cpk_observations
  ALTER COLUMN observed_at TYPE timestamptz USING observed_at::timestamptz;
"""

_POSTGRES_SCHEMA_V3_SQL = r"""
DO $cpk$
BEGIN
  IF EXISTS (
    SELECT 1
    FROM (
      SELECT created_at AS value FROM cpk_graph_versions
      UNION ALL SELECT admitted_at FROM cpk_image_pull_authorities
      UNION ALL SELECT admitted_at FROM cpk_ingress_authorities
      UNION ALL SELECT created_at FROM cpk_realized_graph_projections
      UNION ALL SELECT imported_at FROM cpk_registered_products
      UNION ALL SELECT admitted_at FROM cpk_runtime_authorities
      UNION ALL SELECT admitted_at FROM cpk_runtime_authority_deliveries
    ) AS retained
    WHERE CASE
      WHEN octet_length(value) > 27 THEN TRUE
      WHEN value !~ '^[0-9]{4}-(0[1-9]|1[0-2])-(0[1-9]|[12][0-9]|3[01])T([01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9]([.][0-9]{6})?Z$'
        THEN TRUE
      WHEN value ~ '[.]000000Z$' THEN TRUE
      ELSE FALSE
    END
  ) THEN
    RAISE EXCEPTION USING
      ERRCODE = 'P1110',
      MESSAGE = 'graph, product, and authority timestamps are not canonical UTC';
  END IF;
END
$cpk$;

ALTER TABLE cpk_graph_versions
  ALTER COLUMN created_at TYPE timestamptz(6) USING created_at::timestamptz(6);
ALTER TABLE cpk_image_pull_authorities
  ALTER COLUMN admitted_at TYPE timestamptz(6) USING admitted_at::timestamptz(6);
ALTER TABLE cpk_ingress_authorities
  ALTER COLUMN admitted_at TYPE timestamptz(6) USING admitted_at::timestamptz(6);
ALTER TABLE cpk_realized_graph_projections
  ALTER COLUMN created_at TYPE timestamptz(6) USING created_at::timestamptz(6);
ALTER TABLE cpk_registered_products
  ALTER COLUMN imported_at TYPE timestamptz(6) USING imported_at::timestamptz(6);
ALTER TABLE cpk_runtime_authorities
  ALTER COLUMN admitted_at TYPE timestamptz(6) USING admitted_at::timestamptz(6);
ALTER TABLE cpk_runtime_authority_deliveries
  ALTER COLUMN admitted_at TYPE timestamptz(6) USING admitted_at::timestamptz(6);
"""

POSTGRES_SCHEMA_V1_SHA256 = (
    "fc9b5547fc51ec681130c41facea785dbd24649049417455b184ea05886beed8"
)
_POSTGRES_SCHEMA_V1 = SchemaMigration(
    version=1,
    name="operations-baseline",
    sql=POSTGRES_SCHEMA,
)
if _POSTGRES_SCHEMA_V1.checksum_sha256 != POSTGRES_SCHEMA_V1_SHA256:
    raise SchemaMigrationError(
        "operations baseline V1 content differs from its pinned checksum: "
        f"expected {POSTGRES_SCHEMA_V1_SHA256}, "
        f"observed {_POSTGRES_SCHEMA_V1.checksum_sha256}"
    )
_POSTGRES_SCHEMA_V2 = SchemaMigration(
    version=2,
    name="coordination-timestamps",
    sql=_POSTGRES_SCHEMA_V2_SQL,
)
_POSTGRES_SCHEMA_V2_SHA256 = (
    "95c7782cf66875a3f70c6354b86054ec4ca86f45dca7d2ccb4d971920162c329"
)
if _POSTGRES_SCHEMA_V2.checksum_sha256 != _POSTGRES_SCHEMA_V2_SHA256:
    raise SchemaMigrationError(
        "coordination timestamp V2 content differs from its pinned checksum: "
        f"expected {_POSTGRES_SCHEMA_V2_SHA256}, "
        f"observed {_POSTGRES_SCHEMA_V2.checksum_sha256}"
    )
_POSTGRES_SCHEMA_V3 = SchemaMigration(
    version=3,
    name="graph-product-authority-timestamps",
    sql=_POSTGRES_SCHEMA_V3_SQL,
)
_POSTGRES_SCHEMA_V3_SHA256 = (
    "1f4cf8704affd90ab2ceb17d2a00a62a91e265d2c8c1f49a77c9a6e446cdbdfa"
)
if _POSTGRES_SCHEMA_V3.checksum_sha256 != _POSTGRES_SCHEMA_V3_SHA256:
    raise SchemaMigrationError(
        "graph, product, and authority timestamp V3 content differs from its pinned "
        "checksum: "
        f"expected {_POSTGRES_SCHEMA_V3_SHA256}, "
        f"observed {_POSTGRES_SCHEMA_V3.checksum_sha256}"
    )
POSTGRES_SCHEMA_MIGRATIONS = SchemaMigrationRegistry(
    (_POSTGRES_SCHEMA_V1, _POSTGRES_SCHEMA_V2, _POSTGRES_SCHEMA_V3)
)


def install_schema(connection: MigrationPostgresConnection) -> None:
    """Install through the canonical caller-aware migration interpreter."""

    from control_plane_kit_operations.postgres.migration_runner import (
        install_postgres_schema,
    )

    install_postgres_schema(connection)


def _upgrade_approval_scope_constraints(connection: PostgresConnection) -> None:
    """Evolve closed approval scopes only when an installed check is stale."""

    required = PolicyScope.DELEGATION_KEY_ROTATE_APPROVE.value
    allowed = _sql_values(tuple(PolicyScope))
    for table, column, constraint in (
        (
            "cpk_approval_requests",
            "required_scope",
            "cpk_approval_requests_scope_check",
        ),
        (
            "cpk_approval_decisions",
            "scope",
            "cpk_approval_decisions_scope_check",
        ),
    ):
        row = connection.execute(
            """
            SELECT pg_get_constraintdef(oid)
            FROM pg_constraint
            WHERE conname = %s
              AND conrelid = %s::regclass
            """,
            (constraint, table),
        ).fetchone()
        if row is None or required in row[0]:
            continue
        connection.execute(f"ALTER TABLE {table} DROP CONSTRAINT {constraint}")
        connection.execute(
            f"ALTER TABLE {table} ADD CONSTRAINT {constraint} "
            f"CHECK ({column} IN ({allowed}))"
        )


def _upgrade_gateway_key_rotation_status_constraints(
    connection: PostgresConnection,
) -> None:
    """Expand installed closed rotation-state checks without row loss."""

    statuses = tuple(GatewayKeyRotationStatus)
    allowed = _sql_values(statuses)
    for table, column, constraint in (
        (
            "cpk_gateway_key_rotations",
            "status",
            "cpk_gateway_key_rotations_status_check",
        ),
        (
            "cpk_gateway_key_rotation_transitions",
            "from_status",
            "cpk_gateway_key_rotation_transitions_from_status_check",
        ),
        (
            "cpk_gateway_key_rotation_transitions",
            "to_status",
            "cpk_gateway_key_rotation_transitions_to_status_check",
        ),
    ):
        row = connection.execute(
            """
            SELECT pg_get_constraintdef(oid)
            FROM pg_constraint
            WHERE conname = %s
              AND conrelid = %s::regclass
            """,
            (constraint, table),
        ).fetchone()
        if row is None or all(status.value in row[0] for status in statuses):
            continue
        connection.execute(f"ALTER TABLE {table} DROP CONSTRAINT {constraint}")
        connection.execute(
            f"ALTER TABLE {table} ADD CONSTRAINT {constraint} "
            f"CHECK ({column} IN ({allowed}))"
        )


def _upgrade_gateway_key_rotation_retirement_constraint(
    connection: PostgresConnection,
) -> None:
    """Allow public retirement to precede exact private-version revocation."""

    table = "cpk_gateway_key_rotations"
    constraint = "cpk_gateway_key_rotations_retirement_check"
    row = connection.execute(
        """
        SELECT pg_get_constraintdef(oid)
        FROM pg_constraint
        WHERE conname = %s
          AND conrelid = %s::regclass
        """,
        (constraint, table),
    ).fetchone()
    definition = "" if row is None else row[0].lower()
    if (
        "old_secret_revoked_at is null" in definition
        and "old_key_retired_at is not null" in definition
    ):
        return
    connection.execute(f"ALTER TABLE {table} DROP CONSTRAINT {constraint}")
    connection.execute(
        f"ALTER TABLE {table} ADD CONSTRAINT {constraint} "
        "CHECK (old_secret_revoked_at IS NULL "
        "OR old_key_retired_at IS NOT NULL)"
    )


def _backfill_graph_lineage(connection: PostgresConnection) -> None:
    """Materialize deterministic identity projections for pre-lineage rows."""

    rows = connection.execute(
        """
        SELECT graph_id, workspace_id, version, graph_descriptor,
               created_by, created_at, metadata
        FROM cpk_graph_versions
        WHERE graph_id IN (
          SELECT current_graph_id FROM cpk_workspaces
          WHERE current_graph_id IS NOT NULL
          UNION
          SELECT desired_graph_id FROM cpk_workspaces
          WHERE desired_graph_id IS NOT NULL
          UNION
          SELECT base_graph_id FROM cpk_activity_plans
          UNION
          SELECT desired_graph_id FROM cpk_activity_plans
        )
        ORDER BY workspace_id, version
        """
    ).fetchall()
    for row in rows:
        existing = connection.execute(
            """
            SELECT projection_id
            FROM cpk_realized_graph_projections
            WHERE workspace_id = %s
              AND source_authored_graph_id = %s
              AND projection_kind = 'identity'
              AND projection_key = 'identity'
            """,
            (row[1], row[0]),
        ).fetchone()
        if existing is not None:
            continue
        authored = GraphVersionRecord(
            graph_id=row[0],
            workspace_id=row[1],
            version=row[2],
            graph_descriptor=row[3],
            created_by=row[4],
            created_at=row[5],
            metadata=row[6],
        )
        projection = RealizedGraphProjectionRecord.identity_for_authored(
            authored_record=authored
        )
        connection.execute(
            """
            INSERT INTO cpk_realized_graph_projections
              (projection_id, workspace_id, source_authored_graph_id,
               projection_kind, projection_key, projection_digest,
               graph_descriptor, created_by, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT DO NOTHING
            """,
            (
                projection.projection_id,
                projection.workspace_id,
                projection.source_authored_graph_id,
                projection.projection_kind.value,
                projection.projection_key,
                projection.projection_digest,
                Jsonb(projection.graph_descriptor),
                projection.created_by,
                projection.created_at,
            ),
        )
    connection.execute(
        """
        UPDATE cpk_workspaces AS workspace
        SET current_realized_projection_id = projection.projection_id
        FROM cpk_realized_graph_projections AS projection
        WHERE workspace.current_graph_id = projection.source_authored_graph_id
          AND workspace.workspace_id = projection.workspace_id
          AND projection.projection_kind = 'identity'
          AND projection.projection_key = 'identity'
          AND workspace.current_realized_projection_id IS NULL
        """
    )
    connection.execute(
        """
        UPDATE cpk_workspaces AS workspace
        SET desired_realized_projection_id = projection.projection_id
        FROM cpk_realized_graph_projections AS projection
        WHERE workspace.desired_graph_id = projection.source_authored_graph_id
          AND workspace.workspace_id = projection.workspace_id
          AND projection.projection_kind = 'identity'
          AND projection.projection_key = 'identity'
          AND workspace.desired_realized_projection_id IS NULL
        """
    )
    connection.execute(
        """
        UPDATE cpk_activity_plans AS plan
        SET base_realized_projection_id = projection.projection_id
        FROM cpk_realized_graph_projections AS projection
        WHERE plan.base_graph_id = projection.source_authored_graph_id
          AND projection.projection_kind = 'identity'
          AND projection.projection_key = 'identity'
          AND plan.base_realized_projection_id IS NULL
        """
    )
    connection.execute(
        """
        UPDATE cpk_activity_plans AS plan
        SET desired_realized_projection_id = projection.projection_id
        FROM cpk_realized_graph_projections AS projection
        WHERE plan.desired_graph_id = projection.source_authored_graph_id
          AND projection.projection_kind = 'identity'
          AND projection.projection_key = 'identity'
          AND plan.desired_realized_projection_id IS NULL
        """
    )
