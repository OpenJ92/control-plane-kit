"""Read-only Postgres interpretation of CPK schema migration truth."""

from __future__ import annotations

import json
from dataclasses import asdict

from control_plane_kit_operations.postgres.current_schema_contract import (
    CURRENT_POSTGRES_SCHEMA_CONTRACT,
    CURRENT_SCHEMA_LOCK_PLAN,
    PENDING_SCHEMA_LOCK_PLAN,
    SchemaLockPlan,
)
from control_plane_kit_operations.postgres.migrations import (
    AppliedSchemaMigration,
    ObservedSchemaKind,
    ObservedSchemaState,
    SchemaMigrationError,
)
from control_plane_kit_operations.postgres.schema import (
    POSTGRES_SCHEMA_MIGRATIONS,
    MigrationPostgresConnection,
    PostgresConnection,
    _POSTGRES_SCHEMA_V17_CONSTRAINTS,
    _POSTGRES_SCHEMA_V17_DEPENDENCIES,
)
from control_plane_kit_operations.postgres.graph_lineage_backfill import (
    lock_graph_lineage_v1,
    verify_graph_lineage_v1,
)


POSTGRES_SCHEMA_MIGRATION_LEDGER_TABLE = "cpk_schema_migrations"
POSTGRES_SCHEMA_MIGRATION_LEDGER_COLUMNS = (
    "version",
    "name",
    "checksum_sha256",
    "applied_at",
)
_POSTGRES_SCHEMA_MIGRATION_LEDGER_CONTRACT = (
    ("version", "integer", "NO", True),
    ("name", "text", "NO", True),
    ("checksum_sha256", "text", "NO", True),
    ("applied_at", "timestamp with time zone", "NO", True),
)
_MAX_MIGRATION_NAME_BYTES = 128
_MIGRATION_CHECKSUM_BYTES = 64
_PRODUCT_DESCRIPTOR_CONTENT_COLUMN_CONTRACT = ("text", "NO", True)
_PRODUCT_DESCRIPTOR_CONTENT_CONSTRAINT = (
    "cpk_registered_products",
    "cpk_registered_products_content_digest_check",
    "c",
    True,
    True,
)
_PRODUCT_DESCRIPTOR_CONTENT_CONSTRAINT_DEFINITION = (
    "CHECK ((descriptor_sha256 = encode(sha256(convert_to(descriptor_content, "
    "'UTF8'::name)), 'hex'::text)))"
)
_GATEWAY_PROBE_ACCESS_PATH_COLUMN_CONTRACT = ("text", "NO", True)
_GATEWAY_PROBE_ACCESS_PATH_CONSTRAINT = (
    "cpk_gateway_probe_attempts",
    "cpk_gateway_probe_access_path_check",
    "c",
    True,
    True,
)
_GATEWAY_PROBE_ACCESS_PATH_CONSTRAINT_DEFINITION = (
    "CHECK ((access_path = ANY (ARRAY['runtime-private'::text, "
    "'named-public-ingress'::text])))"
)
_GATEWAY_KEY_ROTATION_GENERATION_EVIDENCE_COLUMNS = (
    ("generation_action_digest", "text", "YES", True),
    ("generation_provider_registration_id", "text", "YES", True),
)
_GATEWAY_KEY_ROTATION_GENERATION_EVIDENCE_CONSTRAINTS = (
    (
        "cpk_gateway_key_rotations_generation_checkpoint_check",
        "c",
        True,
        True,
    ),
    ("cpk_gateway_key_rotations_generation_digest_check", "c", True, True),
    ("cpk_gateway_key_rotations_generation_provider_check", "c", True, True),
)
_GATEWAY_KEY_ROTATION_GENERATION_CHECKPOINT_DEFINITION = (
    "CHECK (((generation_provider_registration_id IS NULL) = "
    "(generation_action_digest IS NULL)))"
)
_GATEWAY_KEY_ROTATION_GENERATION_DIGEST_DEFINITION = (
    "CHECK (((generation_action_digest IS NULL) OR "
    '((generation_action_digest COLLATE "C") ~ '
    "'^[0-9a-f]{64}$'::text)))"
)
_GATEWAY_KEY_ROTATION_GENERATION_PROVIDER_DEFINITION = (
    'CHECK (((generation_provider_registration_id IS NULL) OR '
    "(((octet_length(generation_provider_registration_id) >= 1) AND "
    "(octet_length(generation_provider_registration_id) <= 200)) AND "
    '((generation_provider_registration_id COLLATE "C") ~ '
    "'^[A-Za-z0-9]'::text) AND "
    '((generation_provider_registration_id COLLATE "C") !~ '
    "'[^A-Za-z0-9._:-]'::text))))"
)
_GATEWAY_KEY_ROTATION_STATUS_CONSTRAINTS = (
    ("cpk_gateway_key_rotations_status_check", "c", True, True),
    (
        "cpk_gateway_key_rotation_transitions_from_status_check",
        "c",
        True,
        True,
    ),
    (
        "cpk_gateway_key_rotation_transitions_to_status_check",
        "c",
        True,
        True,
    ),
)
_GATEWAY_KEY_ROTATION_STATUS_DEFINITION = (
    "CHECK ((status = ANY (ARRAY['requested'::text, 'awaiting-approval'::text, "
    "'approved'::text, 'generation-prepared'::text, 'key-generated'::text, "
    "'overlap-deploying'::text, 'overlap-ready'::text, "
    "'new-key-active'::text, 'draining-old-grants'::text, "
    "'retirement-deploying'::text, 'retirement-ready'::text, "
    "'old-key-retired'::text, 'revocation-prepared'::text, "
    "'completed'::text, 'blocked'::text, 'rejected'::text])))"
)
_GATEWAY_KEY_ROTATION_FROM_STATUS_DEFINITION = (
    "CHECK ((from_status = ANY (ARRAY['requested'::text, "
    "'awaiting-approval'::text, 'approved'::text, "
    "'generation-prepared'::text, 'key-generated'::text, "
    "'overlap-deploying'::text, 'overlap-ready'::text, "
    "'new-key-active'::text, 'draining-old-grants'::text, "
    "'retirement-deploying'::text, 'retirement-ready'::text, "
    "'old-key-retired'::text, 'revocation-prepared'::text, "
    "'completed'::text, 'blocked'::text, 'rejected'::text])))"
)
_GATEWAY_KEY_ROTATION_TO_STATUS_DEFINITION = (
    "CHECK ((to_status = ANY (ARRAY['requested'::text, 'awaiting-approval'::text, "
    "'approved'::text, 'generation-prepared'::text, 'key-generated'::text, "
    "'overlap-deploying'::text, 'overlap-ready'::text, "
    "'new-key-active'::text, 'draining-old-grants'::text, "
    "'retirement-deploying'::text, 'retirement-ready'::text, "
    "'old-key-retired'::text, 'revocation-prepared'::text, "
    "'completed'::text, 'blocked'::text, 'rejected'::text])))"
)
_GATEWAY_KEY_ROTATION_RETIREMENT_EVIDENCE_COLUMNS = (
    ("old_key_retired_at", "timestamp with time zone", 6, "YES", True),
    ("old_secret_revoked_at", "timestamp with time zone", 6, "YES", True),
)
_GATEWAY_KEY_ROTATION_RETIREMENT_CONSTRAINT = (
    "cpk_gateway_key_rotations_retirement_check",
    "c",
    True,
    True,
)
_GATEWAY_KEY_ROTATION_RETIREMENT_DEFINITION = (
    "CHECK (((old_secret_revoked_at IS NULL) OR "
    "(old_key_retired_at IS NOT NULL)))"
)
_APPROVAL_SUBJECT_EVIDENCE_COLUMNS = (
    ("plan_id", "text", "YES", True),
    ("review_digest", "text", "NO", True),
    ("rotation_id", "text", "YES", True),
    ("subject_kind", "text", "NO", True),
    ("subject_payload", "jsonb", "NO", True),
)
_APPROVAL_SUBJECT_EVIDENCE_CONSTRAINTS = (
    ("cpk_approval_requests_review_digest_check", "c", True, True),
    ("cpk_approval_requests_rotation_fk", "f", True, True),
    ("cpk_approval_requests_subject_identity_check", "c", True, True),
    ("cpk_approval_requests_subject_kind_check", "c", True, True),
)
_APPROVAL_SUBJECT_EVIDENCE_DEFINITIONS = (
    "CHECK (((review_digest COLLATE \"C\") ~ '^[0-9a-f]{64}$'::text))",
    "FOREIGN KEY (rotation_id) REFERENCES cpk_gateway_key_rotations(rotation_id)",
    "CHECK ((((subject_kind = 'activity-plan'::text) AND "
    "(plan_id IS NOT NULL) AND (rotation_id IS NULL)) OR "
    "((subject_kind = 'gateway-key-rotation'::text) AND "
    "(plan_id IS NULL) AND (rotation_id IS NOT NULL))))",
    "CHECK ((subject_kind = ANY (ARRAY['activity-plan'::text, "
    "'gateway-key-rotation'::text])))",
)
_APPROVAL_SCOPE_VALUES = (
    "hub:instance:create",
    "hub:instance:read",
    "instance:workspace:read",
    "instance:workspace:edit",
    "plan:request",
    "plan:approve",
    "plan:approve-destructive",
    "plan:execute",
    "execution:operate",
    "runtime-authority:register",
    "runtime-authority:read",
    "runtime-authority:revoke",
    "runtime-authority:use",
    "runtime-authority-delivery:register",
    "runtime-authority-delivery:read",
    "runtime-authority-delivery:revoke",
    "ingress-authority:register",
    "ingress-authority:read",
    "ingress-authority:revoke",
    "ingress-authority:use",
    "secret-provider:register",
    "secret-provider:read",
    "secret-provider:use",
    "secret-provider:revoke",
    "delegation-key:generate",
    "delegation-key:register",
    "delegation-key:read",
    "delegation-key:activate",
    "delegation-key:retire",
    "delegation-key:revoke",
    "delegation-key:use",
    "delegation-key:rotate",
    "delegation-key:rotate-approve",
    "gateway-probe:use",
)


def _approval_scope_definition(column: str) -> str:
    values = ", ".join(f"'{value}'::text" for value in _APPROVAL_SCOPE_VALUES)
    return f"CHECK (({column} = ANY (ARRAY[{values}])))"


_APPROVAL_SCOPE_CONTRACTS = (
    (
        "cpk_approval_decisions",
        "cpk_approval_decisions_scope_check",
        "c",
        True,
        True,
    ),
    (
        "cpk_approval_requests",
        "cpk_approval_requests_scope_check",
        "c",
        True,
        True,
    ),
)
_APPROVAL_REQUEST_SCOPE_DEFINITION = _approval_scope_definition("required_scope")
_APPROVAL_DECISION_SCOPE_DEFINITION = _approval_scope_definition("scope")
_DELEGATION_KEY_PURPOSE_VALUES = (
    "gateway-probe",
    "workload-node-control",
    "workload-node-control-surface-read",
)
_DELEGATION_KEY_PURPOSE_DEFINITION = (
    "CHECK ((purpose = ANY (ARRAY["
    + ", ".join(f"'{value}'::text" for value in _DELEGATION_KEY_PURPOSE_VALUES)
    + "])))"
)
_DELEGATION_KEY_PURPOSE_CONTRACTS = (
    (
        "cpk_delegation_signing_keys",
        "cpk_delegation_signing_keys_purpose_check",
        "c",
        True,
        True,
    ),
    (
        "cpk_gateway_key_rotations",
        "cpk_gateway_key_rotations_purpose_check",
        "c",
        True,
        True,
    ),
)
_COORDINATION_TEMPORAL_CONTRACT = (
    ("cpk_activity_events", "occurred_at", "timestamp with time zone", 6, "NO", True),
    ("cpk_activity_plans", "created_at", "timestamp with time zone", 6, "NO", True),
    ("cpk_activity_runs", "created_at", "timestamp with time zone", 6, "NO", True),
    ("cpk_activity_runs", "settled_at", "timestamp with time zone", 6, "YES", True),
    ("cpk_activity_runs", "started_at", "timestamp with time zone", 6, "YES", True),
    (
        "cpk_approval_decisions",
        "decided_at",
        "timestamp with time zone",
        6,
        "NO",
        True,
    ),
    (
        "cpk_approval_requests",
        "requested_at",
        "timestamp with time zone",
        6,
        "NO",
        True,
    ),
    (
        "cpk_execution_requests",
        "claimed_at",
        "timestamp with time zone",
        6,
        "YES",
        True,
    ),
    (
        "cpk_execution_requests",
        "lease_expires_at",
        "timestamp with time zone",
        6,
        "YES",
        True,
    ),
    (
        "cpk_execution_requests",
        "requested_at",
        "timestamp with time zone",
        6,
        "NO",
        True,
    ),
    ("cpk_observations", "observed_at", "timestamp with time zone", 6, "NO", True),
    (
        "cpk_operation_actions",
        "created_at",
        "timestamp with time zone",
        6,
        "NO",
        True,
    ),
    (
        "cpk_operation_sessions",
        "closed_at",
        "timestamp with time zone",
        6,
        "YES",
        True,
    ),
    (
        "cpk_operation_sessions",
        "created_at",
        "timestamp with time zone",
        6,
        "NO",
        True,
    ),
)
_GRAPH_PRODUCT_AUTHORITY_TEMPORAL_CONTRACT = (
    ("cpk_graph_versions", "created_at", "timestamp with time zone", 6, "NO", True),
    (
        "cpk_image_pull_authorities",
        "admitted_at",
        "timestamp with time zone",
        6,
        "NO",
        True,
    ),
    (
        "cpk_ingress_authorities",
        "admitted_at",
        "timestamp with time zone",
        6,
        "NO",
        True,
    ),
    (
        "cpk_realized_graph_projections",
        "created_at",
        "timestamp with time zone",
        6,
        "NO",
        True,
    ),
    (
        "cpk_registered_products",
        "imported_at",
        "timestamp with time zone",
        6,
        "NO",
        True,
    ),
    (
        "cpk_runtime_authorities",
        "admitted_at",
        "timestamp with time zone",
        6,
        "NO",
        True,
    ),
    (
        "cpk_runtime_authority_deliveries",
        "admitted_at",
        "timestamp with time zone",
        6,
        "NO",
        True,
    ),
)
_SECRET_REGISTRATION_TEMPORAL_CONTRACT = (
    (
        "cpk_secret_providers",
        "admitted_at",
        "timestamp with time zone",
        6,
        "NO",
        True,
    ),
    (
        "cpk_secret_providers",
        "revoked_at",
        "timestamp with time zone",
        6,
        "YES",
        True,
    ),
    (
        "cpk_secret_references",
        "admitted_at",
        "timestamp with time zone",
        6,
        "NO",
        True,
    ),
    (
        "cpk_secret_references",
        "revoked_at",
        "timestamp with time zone",
        6,
        "YES",
        True,
    ),
)
_DELEGATION_SIGNING_KEY_TEMPORAL_CONTRACT = (
    (
        "cpk_delegation_signing_keys",
        "activated_at",
        "timestamp with time zone",
        6,
        "YES",
        True,
    ),
    (
        "cpk_delegation_signing_keys",
        "admitted_at",
        "timestamp with time zone",
        6,
        "NO",
        True,
    ),
    (
        "cpk_delegation_signing_keys",
        "retired_at",
        "timestamp with time zone",
        6,
        "YES",
        True,
    ),
    (
        "cpk_delegation_signing_keys",
        "revoked_at",
        "timestamp with time zone",
        6,
        "YES",
        True,
    ),
)
_GATEWAY_PROBE_TEMPORAL_CONTRACT = (
    (
        "cpk_gateway_probe_attempts",
        "completed_at",
        "timestamp with time zone",
        6,
        "YES",
        True,
    ),
    (
        "cpk_gateway_probe_attempts",
        "requested_at",
        "timestamp with time zone",
        6,
        "NO",
        True,
    ),
)
_GATEWAY_KEY_ROTATION_TEMPORAL_CONTRACT = (
    (
        "cpk_gateway_key_rotation_deployments",
        "accepted_at",
        "timestamp with time zone",
        6,
        "YES",
        True,
    ),
    (
        "cpk_gateway_key_rotation_deployments",
        "prepared_at",
        "timestamp with time zone",
        6,
        "NO",
        True,
    ),
    (
        "cpk_gateway_key_rotation_revocations",
        "prepared_at",
        "timestamp with time zone",
        6,
        "NO",
        True,
    ),
    (
        "cpk_gateway_key_rotation_transitions",
        "advanced_at",
        "timestamp with time zone",
        6,
        "NO",
        True,
    ),
    (
        "cpk_gateway_key_rotations",
        "new_key_activated_at",
        "timestamp with time zone",
        6,
        "YES",
        True,
    ),
    (
        "cpk_gateway_key_rotations",
        "old_key_retired_at",
        "timestamp with time zone",
        6,
        "YES",
        True,
    ),
    (
        "cpk_gateway_key_rotations",
        "old_secret_revoked_at",
        "timestamp with time zone",
        6,
        "YES",
        True,
    ),
    (
        "cpk_gateway_key_rotations",
        "requested_at",
        "timestamp with time zone",
        6,
        "NO",
        True,
    ),
    (
        "cpk_gateway_key_rotations",
        "updated_at",
        "timestamp with time zone",
        6,
        "YES",
        True,
    ),
)
_INGRESS_EVIDENCE_TEMPORAL_CONTRACT = (
    (
        "cpk_cloudflare_ingress_resources",
        "created_at",
        "timestamp with time zone",
        6,
        "NO",
        True,
    ),
    (
        "cpk_cloudflare_ingress_resources",
        "observed_at",
        "timestamp with time zone",
        6,
        "NO",
        True,
    ),
    (
        "cpk_cloudflare_ingress_resources",
        "removed_at",
        "timestamp with time zone",
        6,
        "YES",
        True,
    ),
    (
        "cpk_generated_ingress_secret_references",
        "recorded_at",
        "timestamp with time zone",
        6,
        "NO",
        True,
    ),
)
_SECRET_USE_AUTHORIZATION_TEMPORAL_CONTRACT = (
    (
        "cpk_secret_use_authorizations",
        "requested_at",
        "timestamp with time zone",
        6,
        "NO",
        True,
    ),
)

# This explicit projection is verified against the checksum-pinned V1 artifact.
POSTGRES_SCHEMA_V1_TABLE_COLUMNS = (
    (
        "cpk_activity_events",
        ("event_id", "run_id", "ordinal", "event_type", "occurred_at", "payload"),
    ),
    (
        "cpk_activity_plans",
        (
            "plan_id",
            "session_id",
            "base_graph_id",
            "desired_graph_id",
            "base_realized_projection_id",
            "desired_realized_projection_id",
            "desired_graph_revision",
            "status",
            "created_at",
            "payload",
        ),
    ),
    (
        "cpk_activity_runs",
        (
            "run_id",
            "plan_id",
            "request_id",
            "attempt",
            "prior_run_id",
            "status",
            "created_at",
            "started_at",
            "settled_at",
            "metadata",
        ),
    ),
    (
        "cpk_approval_decisions",
        (
            "decision_id",
            "request_id",
            "actor_id",
            "decision",
            "scope",
            "decided_at",
            "comment",
            "idempotency_key",
            "intent_fingerprint",
        ),
    ),
    (
        "cpk_approval_requests",
        (
            "request_id",
            "session_id",
            "plan_id",
            "rotation_id",
            "subject_kind",
            "subject_payload",
            "review_digest",
            "requested_by",
            "requested_at",
            "required_scope",
            "max_risk",
            "destructive",
            "comment",
            "idempotency_key",
            "intent_fingerprint",
        ),
    ),
    (
        "cpk_cloudflare_ingress_resources",
        (
            "workspace_id",
            "runtime_id",
            "ingress_id",
            "epoch",
            "status",
            "authority_ref",
            "provider_kind",
            "tunnel_name",
            "tunnel_id",
            "dns_record_id",
            "hostname",
            "zone_id",
            "lifecycle",
            "created_at",
            "observed_at",
            "source_run_id",
            "source_activity_id",
            "source_event_id",
            "removed_at",
            "removed_by_run_id",
            "metadata",
        ),
    ),
    (
        "cpk_delegation_signing_keys",
        (
            "registration_id",
            "workspace_id",
            "purpose",
            "issuer",
            "key_id",
            "algorithm",
            "public_key_pem",
            "public_fingerprint_sha256",
            "private_key_reference",
            "admitted_by",
            "admitted_at",
            "status",
            "activated_by",
            "activated_at",
            "retired_by",
            "retired_at",
            "revoked_by",
            "revoked_at",
        ),
    ),
    (
        "cpk_execution_requests",
        (
            "request_id",
            "workspace_id",
            "session_id",
            "plan_id",
            "status",
            "requested_by",
            "requested_at",
            "approval_request_id",
            "approval_decision_id",
            "idempotency_key",
            "intent_fingerprint",
            "claim_worker_id",
            "claimed_at",
            "lease_expires_at",
        ),
    ),
    (
        "cpk_gateway_key_rotation_deployments",
        (
            "rotation_id",
            "phase",
            "status",
            "session_id",
            "plan_id",
            "approval_request_id",
            "approval_decision_id",
            "execution_request_id",
            "run_id",
            "base_authored_graph_id",
            "base_realized_projection_id",
            "desired_authored_graph_id",
            "desired_realized_projection_id",
            "desired_revision",
            "prepared_at",
            "accepted_current_graph_id",
            "accepted_current_projection_id",
            "accepted_at",
        ),
    ),
    (
        "cpk_gateway_key_rotation_revocations",
        (
            "rotation_id",
            "provider_registration_id",
            "secret_reference",
            "provider_version_id",
            "provider_version_number",
            "revocation_id",
            "correlation_id",
            "action_digest",
            "prepared_at",
        ),
    ),
    (
        "cpk_gateway_key_rotation_transitions",
        (
            "rotation_id",
            "transition_id",
            "from_status",
            "to_status",
            "from_version",
            "to_version",
            "transition_fingerprint",
            "advanced_by",
            "advanced_at",
            "failure_code",
        ),
    ),
    (
        "cpk_gateway_key_rotations",
        (
            "rotation_id",
            "workspace_id",
            "gateway_node_id",
            "purpose",
            "issuer",
            "old_key_id",
            "new_secret_reference",
            "key_generation_correlation",
            "maximum_grant_lifetime_seconds",
            "clock_skew_seconds",
            "correlation_id",
            "requested_by",
            "requested_at",
            "intent_fingerprint",
            "status",
            "version",
            "approval_request_id",
            "approval_decision_id",
            "generation_provider_registration_id",
            "generation_action_digest",
            "new_key_id",
            "new_secret_version_id",
            "new_secret_version_number",
            "new_key_activated_at",
            "drain_deadline_epoch",
            "old_key_retired_at",
            "old_secret_revoked_at",
            "failure_code",
            "updated_by",
            "updated_at",
        ),
    ),
    (
        "cpk_gateway_probe_attempts",
        (
            "probe_id",
            "workspace_id",
            "request_id",
            "actor_id",
            "current_graph_id",
            "gateway_node_id",
            "gateway_runtime_id",
            "access_path",
            "probe_kind",
            "target_id",
            "request_digest",
            "issuer",
            "key_id",
            "audience",
            "grant_jti",
            "issued_at",
            "expires_at",
            "status",
            "requested_at",
            "intent_fingerprint",
            "completed_at",
            "result_code",
            "evidence",
        ),
    ),
    (
        "cpk_generated_ingress_secret_references",
        (
            "workspace_id",
            "purpose",
            "secret_ref",
            "recorded_at",
            "source_run_id",
            "source_activity_id",
            "source_event_id",
            "metadata",
        ),
    ),
    (
        "cpk_graph_versions",
        (
            "graph_id",
            "workspace_id",
            "version",
            "graph_descriptor",
            "created_by",
            "created_at",
            "metadata",
        ),
    ),
    (
        "cpk_image_pull_authorities",
        (
            "authority_id",
            "workspace_id",
            "authority",
            "registry",
            "repository",
            "credential_reference",
            "admitted_by",
            "admitted_at",
            "status",
            "metadata",
        ),
    ),
    (
        "cpk_ingress_authorities",
        (
            "registration_id",
            "workspace_id",
            "authority_ref",
            "provider_kind",
            "authority",
            "credential_references",
            "allowed_hostname_pattern",
            "admitted_by",
            "admitted_at",
            "status",
            "metadata",
        ),
    ),
    (
        "cpk_observations",
        (
            "observation_id",
            "workspace_id",
            "subject_id",
            "status",
            "observed_at",
            "evidence",
            "freshness",
            "graph_id",
            "probe_kind",
            "probe_outcome",
            "endpoint_context",
        ),
    ),
    (
        "cpk_operation_actions",
        (
            "action_id",
            "session_id",
            "ordinal",
            "action_type",
            "actor_id",
            "payload",
            "created_at",
            "idempotency_key",
            "intent_fingerprint",
        ),
    ),
    (
        "cpk_operation_sessions",
        (
            "session_id",
            "workspace_id",
            "actor_id",
            "title",
            "status",
            "created_at",
            "closed_at",
            "metadata",
            "idempotency_key",
            "intent_fingerprint",
        ),
    ),
    (
        "cpk_realized_graph_projections",
        (
            "projection_id",
            "workspace_id",
            "source_authored_graph_id",
            "projection_kind",
            "projection_key",
            "projection_digest",
            "graph_descriptor",
            "created_by",
            "created_at",
        ),
    ),
    (
        "cpk_registered_products",
        (
            "registration_id",
            "workspace_id",
            "product_reference",
            "descriptor_sha256",
            "descriptor_document",
            "descriptor_content",
            "source",
            "imported_by",
            "imported_at",
            "status",
            "metadata",
        ),
    ),
    (
        "cpk_runtime_authorities",
        (
            "registration_id",
            "workspace_id",
            "authority_ref",
            "runtime_kind",
            "authority_kind",
            "authority",
            "credential_references",
            "admitted_by",
            "admitted_at",
            "status",
            "metadata",
        ),
    ),
    (
        "cpk_runtime_authority_deliveries",
        (
            "delivery_id",
            "workspace_id",
            "authority_ref",
            "delivery_kind",
            "delivery",
            "secret_references",
            "admitted_by",
            "admitted_at",
            "status",
            "metadata",
        ),
    ),
    (
        "cpk_secret_providers",
        (
            "registration_id",
            "workspace_id",
            "provider_id",
            "provider_kind",
            "display_name",
            "endpoint_reference",
            "credential_reference",
            "allowed_reference_prefixes",
            "allowed_intents",
            "admitted_by",
            "admitted_at",
            "status",
            "supersedes_registration_id",
            "revoked_by",
            "revoked_at",
            "metadata",
        ),
    ),
    (
        "cpk_secret_references",
        (
            "registration_id",
            "workspace_id",
            "secret_reference",
            "provider_registration_id",
            "allowed_intents",
            "admitted_by",
            "admitted_at",
            "status",
            "supersedes_registration_id",
            "revoked_by",
            "revoked_at",
            "metadata",
        ),
    ),
    (
        "cpk_secret_use_authorizations",
        (
            "authorization_id",
            "workspace_id",
            "reference_registration_id",
            "provider_registration_id",
            "secret_reference",
            "use_intent",
            "actor_subject",
            "correlation_id",
            "requested_at",
            "intent_fingerprint",
            "operation_id",
            "session_id",
            "run_id",
            "activity_id",
            "effect_id",
            "probe_id",
        ),
    ),
    (
        "cpk_workspaces",
        (
            "workspace_id",
            "name",
            "lifecycle",
            "current_graph_id",
            "desired_graph_id",
            "current_realized_projection_id",
            "desired_realized_projection_id",
            "desired_graph_revision",
            "metadata",
        ),
    ),
)

_V1_TABLE_NAMES = frozenset(table for table, _ in POSTGRES_SCHEMA_V1_TABLE_COLUMNS)
_MAX_CATALOG_TABLES = len(POSTGRES_SCHEMA_V1_TABLE_COLUMNS) + 2
_MAX_CATALOG_COLUMNS = (
    sum(len(columns) for _, columns in POSTGRES_SCHEMA_V1_TABLE_COLUMNS)
    + len(POSTGRES_SCHEMA_MIGRATION_LEDGER_COLUMNS)
    + 1
)
_CURRENT_CONTRACT_ERROR = "database schema contract is not current"
_CURRENT_SCHEMA_RELATIONS_JSON = json.dumps(
    tuple(asdict(item) for item in CURRENT_POSTGRES_SCHEMA_CONTRACT.relations),
    ensure_ascii=True,
    sort_keys=True,
    separators=(",", ":"),
)
_CURRENT_SCHEMA_COLUMNS_JSON = json.dumps(
    tuple(asdict(item) for item in CURRENT_POSTGRES_SCHEMA_CONTRACT.columns),
    ensure_ascii=True,
    sort_keys=True,
    separators=(",", ":"),
)
_CURRENT_SCHEMA_CONSTRAINTS_JSON = json.dumps(
    tuple(asdict(item) for item in CURRENT_POSTGRES_SCHEMA_CONTRACT.constraints),
    ensure_ascii=True,
    sort_keys=True,
    separators=(",", ":"),
)
_CURRENT_SCHEMA_INDEXES_JSON = json.dumps(
    tuple(asdict(item) for item in CURRENT_POSTGRES_SCHEMA_CONTRACT.indexes),
    ensure_ascii=True,
    sort_keys=True,
    separators=(",", ":"),
)
_CURRENT_SCHEMA_HISTORY_JSON = json.dumps(
    tuple(asdict(item) for item in CURRENT_POSTGRES_SCHEMA_CONTRACT.history),
    ensure_ascii=True,
    sort_keys=True,
    separators=(",", ":"),
)


_CURRENT_SCHEMA_CONTRACT_QUERY = """
WITH
candidate_relations AS MATERIALIZED (
  SELECT relation.oid,
         relation.relname,
         relation.relkind::text AS kind,
         relation.relpersistence::text AS persistence,
         access_method.amname AS access_method,
         relation.relreplident::text AS replica_identity,
         relation.relispartition AS is_partition,
         relation.relrowsecurity AS row_security,
         relation.relforcerowsecurity AS force_row_security
  FROM pg_class AS relation
  JOIN pg_namespace AS namespace
    ON namespace.oid = relation.relnamespace
  LEFT JOIN pg_am AS access_method
    ON access_method.oid = relation.relam
  WHERE namespace.nspname = current_schema()
    AND relation.relkind IN ('r', 'p', 'v', 'm', 'f')
  ORDER BY relation.relname
  LIMIT %s
),
candidate_columns AS MATERIALIZED (
  SELECT relation.oid AS relation_oid,
         relation.relname,
         attribute.attnum,
         attribute.attname,
         type_namespace.nspname AS type_namespace,
         attribute.atttypid,
         attribute.atttypmod,
         attribute.attnotnull,
         attribute.attidentity::text AS identity,
         attribute.attgenerated::text AS generated,
         collation_namespace.nspname AS collation_namespace,
         owned_collation.collname AS collation_name,
         default_value.adbin,
         default_value.adrelid
  FROM candidate_relations AS relation
  JOIN pg_attribute AS attribute
    ON attribute.attrelid = relation.oid
  JOIN pg_type AS owned_type
    ON owned_type.oid = attribute.atttypid
  JOIN pg_namespace AS type_namespace
    ON type_namespace.oid = owned_type.typnamespace
  LEFT JOIN pg_collation AS owned_collation
    ON owned_collation.oid = attribute.attcollation
  LEFT JOIN pg_namespace AS collation_namespace
    ON collation_namespace.oid = owned_collation.collnamespace
  LEFT JOIN pg_attrdef AS default_value
    ON default_value.adrelid = attribute.attrelid
   AND default_value.adnum = attribute.attnum
  WHERE relation.kind = 'r'
    AND attribute.attnum > 0
    AND attribute.attisdropped IS FALSE
  ORDER BY relation.relname, attribute.attname
  LIMIT %s
),
candidate_constraints AS MATERIALIZED (
  SELECT relation.relname,
         owned_constraint.conname,
         owned_constraint.contype::text AS kind,
         owned_constraint.convalidated,
         owned_constraint.condeferrable,
         owned_constraint.condeferred,
         owned_constraint.connoinherit,
         owned_constraint.conrelid,
         owned_constraint.confrelid,
         owned_constraint.conkey,
         owned_constraint.confkey,
         owned_constraint.confupdtype::text AS update_action,
         owned_constraint.confdeltype::text AS delete_action,
         owned_constraint.confmatchtype::text AS match_type,
         owned_constraint.conbin,
         referenced_relation.relname AS referenced_relation,
         referenced_namespace.nspname AS referenced_namespace
  FROM pg_constraint AS owned_constraint
  JOIN candidate_relations AS relation
    ON relation.oid = owned_constraint.conrelid
   AND relation.kind = 'r'
  LEFT JOIN pg_class AS referenced_relation
    ON referenced_relation.oid = owned_constraint.confrelid
  LEFT JOIN pg_namespace AS referenced_namespace
    ON referenced_namespace.oid = referenced_relation.relnamespace
  ORDER BY relation.relname, owned_constraint.conname
  LIMIT %s
),
candidate_indexes AS MATERIALIZED (
  SELECT relation.relname,
         index_relation.relname AS index_name,
         owner.conname AS owning_constraint,
         access_method.amname AS access_method,
         index.indexrelid,
         index.indrelid,
         index.indisunique,
         index.indisprimary,
         index.indisvalid,
         index.indisready,
         index.indislive,
         index.indimmediate,
         index.indisclustered,
         index.indisreplident,
         index.indnullsnotdistinct,
         index.indnkeyatts,
         index.indnatts,
         index.indclass,
         cardinality(index.indclass::oid[]) AS indclass_cardinality,
         index.indcollation,
         cardinality(index.indcollation::oid[]) AS indcollation_cardinality,
         index.indoption,
         cardinality(index.indoption::smallint[]) AS indoption_cardinality,
         index.indpred,
         index.indexprs
  FROM pg_index AS index
  JOIN candidate_relations AS relation
    ON relation.oid = index.indrelid
   AND relation.kind = 'r'
  JOIN pg_class AS index_relation
    ON index_relation.oid = index.indexrelid
  JOIN pg_am AS access_method
    ON access_method.oid = index_relation.relam
  LEFT JOIN pg_constraint AS owner
    ON owner.conindid = index.indexrelid
   AND owner.contype IN ('p', 'u', 'x')
  ORDER BY relation.relname, index_relation.relname
  LIMIT %s
),
candidate_history AS MATERIALIZED (
  SELECT version,
         CASE WHEN octet_length(name) <= %s THEN name ELSE NULL END AS name,
         CASE WHEN octet_length(checksum_sha256) <= %s
              THEN checksum_sha256 ELSE NULL END AS checksum_sha256
  FROM cpk_schema_migrations
  ORDER BY version
  LIMIT %s
),
semantic_relations AS (
  SELECT relation.relname,
         jsonb_build_object(
           'name', relation.relname,
           'kind', relation.kind,
           'persistence', relation.persistence,
           'access_method', relation.access_method,
           'replica_identity', relation.replica_identity,
           'is_partition', relation.is_partition,
           'row_security', relation.row_security,
           'force_row_security', relation.force_row_security,
           'non_internal_triggers', CASE WHEN EXISTS (
             SELECT 1
             FROM pg_trigger AS trigger
             WHERE trigger.tgrelid = relation.oid
               AND trigger.tgisinternal IS FALSE
             LIMIT 1
           ) THEN 1 ELSE 0 END,
           'policies', CASE WHEN EXISTS (
             SELECT 1
             FROM pg_policy AS policy
             WHERE policy.polrelid = relation.oid
             LIMIT 1
           ) THEN 1 ELSE 0 END,
           'user_rules', CASE WHEN EXISTS (
             SELECT 1
             FROM pg_rewrite AS rule
             WHERE rule.ev_class = relation.oid
               AND rule.rulename <> '_RETURN'
             LIMIT 1
           ) THEN 1 ELSE 0 END
         ) AS value
  FROM candidate_relations AS relation
),
semantic_columns AS (
  SELECT column_value.relname,
         column_value.attname,
         jsonb_build_object(
           'relation', column_value.relname,
           'name', column_value.attname,
           'type_namespace', column_value.type_namespace,
           'formatted_type', format_type(
             column_value.atttypid,
             column_value.atttypmod
           ),
           'not_null', column_value.attnotnull,
           'identity', column_value.identity,
           'generated', column_value.generated,
           'collation_namespace', column_value.collation_namespace,
           'collation_name', column_value.collation_name,
           'default_expression', pg_get_expr(
             column_value.adbin,
             column_value.adrelid,
             false
           )
         ) AS value
  FROM candidate_columns AS column_value
),
semantic_constraints AS (
  SELECT owned_constraint.relname,
         owned_constraint.conname,
         jsonb_build_object(
           'relation', owned_constraint.relname,
           'name', owned_constraint.conname,
           'kind', owned_constraint.kind,
           'validated', owned_constraint.convalidated,
           'deferrable', owned_constraint.condeferrable,
           'deferred', owned_constraint.condeferred,
           'no_inherit', owned_constraint.connoinherit,
           'local_columns', CASE
             WHEN owned_constraint.conkey IS NULL THEN NULL
             ELSE ARRAY(
               SELECT CASE
                 WHEN key.attnum = 0 THEN NULL
                 ELSE attribute.attname
               END
               FROM unnest(owned_constraint.conkey)
                    WITH ORDINALITY AS key(attnum, position)
               LEFT JOIN pg_attribute AS attribute
                 ON attribute.attrelid = owned_constraint.conrelid
                AND attribute.attnum = key.attnum
                AND attribute.attisdropped IS FALSE
               ORDER BY key.position
             )
           END,
           'referenced_relation', CASE
             WHEN owned_constraint.referenced_namespace = current_schema()
               THEN owned_constraint.referenced_relation
             ELSE NULL
           END,
           'referenced_columns', CASE
             WHEN owned_constraint.confkey IS NULL THEN NULL
             ELSE ARRAY(
               SELECT CASE
                 WHEN key.attnum = 0 THEN NULL
                 ELSE attribute.attname
               END
               FROM unnest(owned_constraint.confkey)
                    WITH ORDINALITY AS key(attnum, position)
               LEFT JOIN pg_attribute AS attribute
                 ON attribute.attrelid = owned_constraint.confrelid
                AND attribute.attnum = key.attnum
                AND attribute.attisdropped IS FALSE
               ORDER BY key.position
             )
           END,
           'update_action', NULLIF(owned_constraint.update_action, ' '),
           'delete_action', NULLIF(owned_constraint.delete_action, ' '),
           'match_type', NULLIF(owned_constraint.match_type, ' '),
           'check_expression', CASE
             WHEN owned_constraint.kind = 'c'
               THEN pg_get_expr(
                 owned_constraint.conbin,
                 owned_constraint.conrelid,
                 false
               )
             ELSE NULL
           END
         ) AS value,
         cardinality(owned_constraint.conkey) AS local_cardinality,
         cardinality(owned_constraint.confkey) AS referenced_cardinality
  FROM candidate_constraints AS owned_constraint
),
semantic_indexes AS (
  SELECT owned_index.relname,
         owned_index.index_name,
         jsonb_build_object(
           'relation', owned_index.relname,
           'name', owned_index.index_name,
           'owning_constraint', owned_index.owning_constraint,
           'access_method', owned_index.access_method,
           'unique', owned_index.indisunique,
           'primary', owned_index.indisprimary,
           'valid', owned_index.indisvalid,
           'ready', owned_index.indisready,
           'live', owned_index.indislive,
           'immediate', owned_index.indimmediate,
           'clustered', owned_index.indisclustered,
           'replica_identity', owned_index.indisreplident,
           'nulls_not_distinct', owned_index.indnullsnotdistinct,
           'key_entries', ARRAY(
             SELECT pg_get_indexdef(owned_index.indexrelid, position, false)
             FROM generate_series(1, owned_index.indnkeyatts) AS position
             ORDER BY position
           ),
           'include_entries', ARRAY(
             SELECT pg_get_indexdef(owned_index.indexrelid, position, false)
             FROM generate_series(
               owned_index.indnkeyatts + 1,
               owned_index.indnatts
             ) AS position
             ORDER BY position
           ),
           'opclasses', ARRAY(
             SELECT opclass_namespace.nspname || '.' || opclass.opcname
             FROM unnest(owned_index.indclass::oid[])
                  WITH ORDINALITY AS item(opclass_oid, position)
             JOIN pg_opclass AS opclass
               ON opclass.oid = item.opclass_oid
             JOIN pg_namespace AS opclass_namespace
               ON opclass_namespace.oid = opclass.opcnamespace
             ORDER BY item.position
           ),
           'collations', ARRAY(
             SELECT CASE
               WHEN item.collation_oid = 0 THEN NULL
               ELSE collation_namespace.nspname || '.' || owned_collation.collname
             END
             FROM unnest(owned_index.indcollation::oid[])
                  WITH ORDINALITY AS item(collation_oid, position)
             LEFT JOIN pg_collation AS owned_collation
               ON owned_collation.oid = item.collation_oid
             LEFT JOIN pg_namespace AS collation_namespace
               ON collation_namespace.oid = owned_collation.collnamespace
             ORDER BY item.position
           ),
           'options', owned_index.indoption::smallint[],
           'predicate', pg_get_expr(
             owned_index.indpred,
             owned_index.indrelid,
             false
           ),
           'expressions', pg_get_expr(
             owned_index.indexprs,
             owned_index.indrelid,
             false
           )
         ) AS value,
         owned_index.indclass_cardinality AS opclass_cardinality,
         owned_index.indcollation_cardinality AS collation_cardinality,
         owned_index.indoption_cardinality AS option_cardinality,
         owned_index.indnkeyatts
  FROM candidate_indexes AS owned_index
)
SELECT
  COALESCE(
    (SELECT count(*) = %s AND jsonb_agg(value ORDER BY relname) = %s::jsonb
     FROM semantic_relations),
    FALSE
  ),
  COALESCE(
    (SELECT count(*) = %s
            AND jsonb_agg(value ORDER BY relname, attname) = %s::jsonb
     FROM semantic_columns),
    FALSE
  ),
  COALESCE(
    (SELECT count(*) = %s
            AND bool_and(
              (local_cardinality IS NULL OR local_cardinality =
                jsonb_array_length(value -> 'local_columns'))
              AND (referenced_cardinality IS NULL OR referenced_cardinality =
                jsonb_array_length(value -> 'referenced_columns'))
            )
            AND jsonb_agg(value ORDER BY relname, conname) = %s::jsonb
     FROM semantic_constraints),
    FALSE
  ),
  COALESCE(
    (SELECT count(*) = %s
            AND bool_and(
              opclass_cardinality = indnkeyatts
              AND collation_cardinality = indnkeyatts
              AND option_cardinality = indnkeyatts
            )
            AND jsonb_agg(value ORDER BY relname, index_name) = %s::jsonb
     FROM semantic_indexes),
    FALSE
  ),
  COALESCE(
    (SELECT count(*) = %s
            AND jsonb_agg(
              jsonb_build_object(
                'version', version,
                'name', name,
                'checksum_sha256', checksum_sha256
              ) ORDER BY version
            ) = %s::jsonb
     FROM candidate_history),
    FALSE
  )
"""


def _acquire_schema_lock_plan(
    connection: PostgresConnection,
    plan: SchemaLockPlan,
) -> None:
    if plan is not CURRENT_SCHEMA_LOCK_PLAN and plan is not PENDING_SCHEMA_LOCK_PLAN:
        raise SchemaMigrationError(_CURRENT_CONTRACT_ERROR) from None
    statements = "\n".join(
        f'LOCK TABLE ONLY "{item.relation}" IN {item.mode} MODE;'
        for item in plan.relations
    )
    failed = False
    try:
        connection.execute(statements)
    except Exception:
        failed = True
    if failed:
        raise SchemaMigrationError(_CURRENT_CONTRACT_ERROR) from None


def _verify_current_schema_contract(connection: PostgresConnection) -> None:
    rows = None
    try:
        rows = connection.execute(
            _CURRENT_SCHEMA_CONTRACT_QUERY,
            (
                30,
                358,
                233,
                79,
                _MAX_MIGRATION_NAME_BYTES,
                _MIGRATION_CHECKSUM_BYTES,
                19,
                29,
                _CURRENT_SCHEMA_RELATIONS_JSON,
                357,
                _CURRENT_SCHEMA_COLUMNS_JSON,
                232,
                _CURRENT_SCHEMA_CONSTRAINTS_JSON,
                78,
                _CURRENT_SCHEMA_INDEXES_JSON,
                18,
                _CURRENT_SCHEMA_HISTORY_JSON,
            ),
        ).fetchall()
    except Exception:
        rows = None
    if (
        not isinstance(rows, (list, tuple))
        or len(rows) != 1
        or not isinstance(rows[0], (list, tuple))
        or len(rows[0]) != 5
        or any(type(value) is not bool for value in rows[0])
        or not all(rows[0])
    ):
        raise SchemaMigrationError(_CURRENT_CONTRACT_ERROR) from None


def inspect_postgres_schema(connection: PostgresConnection) -> ObservedSchemaState:
    """Read and validate the current schema without performing mutation."""

    application_manifest, ledger_contract, ledger_primary_key = _read_catalog(
        connection
    )
    if ledger_contract is None:
        if not application_manifest:
            return ObservedSchemaState(kind=ObservedSchemaKind.EMPTY)
        if application_manifest == POSTGRES_SCHEMA_V1_TABLE_COLUMNS:
            return ObservedSchemaState(kind=ObservedSchemaKind.CURRENT_BASELINE)
        raise SchemaMigrationError("database schema manifest is not accepted")

    if (
        ledger_contract != _POSTGRES_SCHEMA_MIGRATION_LEDGER_CONTRACT
        or ledger_primary_key is not True
    ):
        raise SchemaMigrationError("schema migration ledger contract is not accepted")
    if frozenset(table for table, _ in application_manifest) != _V1_TABLE_NAMES:
        raise SchemaMigrationError("versioned database table manifest is not accepted")

    rows = _read_rows(
        connection,
        """
        SELECT version,
               CASE WHEN octet_length(name) <= %s THEN name ELSE NULL END,
               CASE WHEN octet_length(checksum_sha256) <= %s
                    THEN checksum_sha256 ELSE NULL END
        FROM cpk_schema_migrations
        ORDER BY version
        LIMIT %s
        """,
        (
            _MAX_MIGRATION_NAME_BYTES,
            _MIGRATION_CHECKSUM_BYTES,
            POSTGRES_SCHEMA_MIGRATIONS.target_version + 1,
        ),
        "schema migration ledger read failed",
    )
    if not rows:
        raise SchemaMigrationError("schema migration ledger must not be empty")
    try:
        applied = tuple(
            AppliedSchemaMigration(
                version=row[0],
                name=row[1],
                checksum_sha256=row[2],
            )
            for row in rows
        )
        observed = ObservedSchemaState(
            kind=ObservedSchemaKind.VERSIONED,
            applied_migrations=applied,
        )
        POSTGRES_SCHEMA_MIGRATIONS.plan(observed)
    except SchemaMigrationError:
        raise
    except (IndexError, TypeError) as error:
        raise SchemaMigrationError(
            "schema migration ledger rows are not accepted"
        ) from error
    return observed


def verify_postgres_schema(
    connection: MigrationPostgresConnection,
) -> ObservedSchemaState:
    """Require current migration truth inside one coherent transaction."""

    with connection.transaction():
        _acquire_schema_lock_plan(connection, CURRENT_SCHEMA_LOCK_PLAN)
        return _verify_postgres_schema_under_transaction(connection)


def _verify_postgres_schema_under_transaction(
    connection: PostgresConnection,
) -> ObservedSchemaState:
    """Verify current migration history and exact retained structure."""

    _verify_current_schema_contract(connection)
    observed = inspect_postgres_schema(connection)
    if observed.kind is not ObservedSchemaKind.VERSIONED:
        raise SchemaMigrationError("database schema is not versioned")
    application_manifest, ledger_contract, ledger_primary_key = _read_catalog(
        connection
    )
    if (
        ledger_contract != _POSTGRES_SCHEMA_MIGRATION_LEDGER_CONTRACT
        or ledger_primary_key is not True
    ):
        raise SchemaMigrationError("schema migration ledger contract is not current")
    if POSTGRES_SCHEMA_MIGRATIONS.plan(observed).actions:
        raise SchemaMigrationError("database schema has pending migrations")
    if POSTGRES_SCHEMA_MIGRATIONS.target_version >= 10:
        _verify_product_descriptor_content_contract(connection)
    if POSTGRES_SCHEMA_MIGRATIONS.target_version >= 11:
        _verify_gateway_probe_access_path_contract(connection)
    if POSTGRES_SCHEMA_MIGRATIONS.target_version >= 12:
        _verify_gateway_key_rotation_generation_evidence_contract(connection)
    if POSTGRES_SCHEMA_MIGRATIONS.target_version >= 13:
        _verify_gateway_key_rotation_status_contracts(connection)
    if POSTGRES_SCHEMA_MIGRATIONS.target_version >= 14:
        _verify_gateway_key_rotation_retirement_evidence_contract(connection)
    if POSTGRES_SCHEMA_MIGRATIONS.target_version >= 15:
        _verify_approval_subject_evidence_contract(connection)
    if POSTGRES_SCHEMA_MIGRATIONS.target_version >= 16:
        _verify_approval_scope_contracts(connection)
    if POSTGRES_SCHEMA_MIGRATIONS.target_version >= 17:
        _verify_graph_lineage_contracts(connection)
    if POSTGRES_SCHEMA_MIGRATIONS.target_version >= 18:
        _verify_delegation_key_purpose_contracts(connection)
    if _read_coordination_temporal_contract(connection) != (
        _COORDINATION_TEMPORAL_CONTRACT
    ):
        raise SchemaMigrationError("coordination temporal schema is not current")
    if _read_graph_product_authority_temporal_contract(connection) != (
        _GRAPH_PRODUCT_AUTHORITY_TEMPORAL_CONTRACT
    ):
        raise SchemaMigrationError(
            "graph, product, and authority temporal schema is not current"
        )
    if _read_secret_registration_temporal_contract(connection) != (
        _SECRET_REGISTRATION_TEMPORAL_CONTRACT
    ):
        raise SchemaMigrationError(
            "secret registration temporal schema is not current"
        )
    if _read_delegation_signing_key_temporal_contract(connection) != (
        _DELEGATION_SIGNING_KEY_TEMPORAL_CONTRACT
    ):
        raise SchemaMigrationError(
            "delegation signing-key temporal schema is not current"
        )
    if _read_gateway_probe_temporal_contract(connection) != (
        _GATEWAY_PROBE_TEMPORAL_CONTRACT
    ):
        raise SchemaMigrationError("gateway probe temporal schema is not current")
    if _read_gateway_key_rotation_temporal_contract(connection) != (
        _GATEWAY_KEY_ROTATION_TEMPORAL_CONTRACT
    ):
        raise SchemaMigrationError(
            "gateway key rotation temporal schema is not current"
        )
    if _read_ingress_evidence_temporal_contract(connection) != (
        _INGRESS_EVIDENCE_TEMPORAL_CONTRACT
    ):
        raise SchemaMigrationError(
            "ingress evidence temporal schema is not current"
        )
    if _read_secret_use_authorization_temporal_contract(connection) != (
        _SECRET_USE_AUTHORIZATION_TEMPORAL_CONTRACT
    ):
        raise SchemaMigrationError(
            "secret-use authorization temporal schema is not current"
        )
    return observed


def _verify_product_descriptor_content_contract(
    connection: PostgresConnection,
) -> None:
    column_rows = _read_rows(
        connection,
        """
        SELECT data_type, is_nullable, column_default IS NULL
        FROM information_schema.columns
        WHERE table_schema = current_schema()
          AND table_name = 'cpk_registered_products'
          AND column_name = 'descriptor_content'
        LIMIT 2
        """,
        (),
        "product descriptor content schema read failed",
    )
    if column_rows != [_PRODUCT_DESCRIPTOR_CONTENT_COLUMN_CONTRACT]:
        raise SchemaMigrationError("product descriptor content schema is not current")
    constraint_rows = _read_rows(
        connection,
        """
        SELECT relation.relname,
               constraints.conname,
               constraints.contype::text,
               constraints.convalidated,
               pg_get_constraintdef(constraints.oid, false) = %s
        FROM pg_constraint AS constraints
        JOIN pg_class AS relation
          ON relation.oid = constraints.conrelid
        JOIN pg_namespace AS namespace
          ON namespace.oid = constraints.connamespace
        WHERE namespace.nspname = current_schema()
          AND relation.relname = 'cpk_registered_products'
          AND constraints.conname =
            'cpk_registered_products_content_digest_check'
        ORDER BY relation.relname, constraints.oid
        LIMIT 2
        """,
        (_PRODUCT_DESCRIPTOR_CONTENT_CONSTRAINT_DEFINITION,),
        "product descriptor content schema read failed",
    )
    if constraint_rows != [_PRODUCT_DESCRIPTOR_CONTENT_CONSTRAINT]:
        raise SchemaMigrationError("product descriptor content schema is not current")


def _verify_gateway_probe_access_path_contract(
    connection: PostgresConnection,
) -> None:
    column_rows = _read_rows(
        connection,
        """
        SELECT data_type,
               is_nullable,
               column_default = %s
        FROM information_schema.columns
        WHERE table_schema = current_schema()
          AND table_name = 'cpk_gateway_probe_attempts'
          AND column_name = 'access_path'
        LIMIT 2
        """,
        ("'runtime-private'::text",),
        "gateway probe access path schema read failed",
    )
    if column_rows != [_GATEWAY_PROBE_ACCESS_PATH_COLUMN_CONTRACT]:
        raise SchemaMigrationError(
            "gateway probe access path schema is not current"
        )
    constraint_rows = _read_rows(
        connection,
        """
        SELECT relation.relname,
               constraints.conname,
               constraints.contype::text,
               constraints.convalidated,
               pg_get_constraintdef(constraints.oid, false) = %s
        FROM pg_constraint AS constraints
        JOIN pg_class AS relation
          ON relation.oid = constraints.conrelid
        JOIN pg_namespace AS namespace
          ON namespace.oid = constraints.connamespace
        WHERE namespace.nspname = current_schema()
          AND relation.relname = 'cpk_gateway_probe_attempts'
          AND constraints.conname = 'cpk_gateway_probe_access_path_check'
        ORDER BY relation.relname, constraints.oid
        LIMIT 2
        """,
        (_GATEWAY_PROBE_ACCESS_PATH_CONSTRAINT_DEFINITION,),
        "gateway probe access path schema read failed",
    )
    if constraint_rows != [_GATEWAY_PROBE_ACCESS_PATH_CONSTRAINT]:
        raise SchemaMigrationError(
            "gateway probe access path schema is not current"
        )


def _verify_gateway_key_rotation_generation_evidence_contract(
    connection: PostgresConnection,
) -> None:
    column_rows = _read_rows(
        connection,
        """
        SELECT column_name, data_type, is_nullable, column_default IS NULL
        FROM information_schema.columns
        WHERE table_schema = current_schema()
          AND table_name = 'cpk_gateway_key_rotations'
          AND column_name IN (
            'generation_provider_registration_id',
            'generation_action_digest'
          )
        ORDER BY column_name
        LIMIT 3
        """,
        (),
        "gateway key rotation generation evidence schema read failed",
    )
    if column_rows != list(_GATEWAY_KEY_ROTATION_GENERATION_EVIDENCE_COLUMNS):
        raise SchemaMigrationError(
            "gateway key rotation generation evidence schema is not current"
        )
    constraint_rows = _read_rows(
        connection,
        """
        SELECT constraints.conname,
               constraints.contype::text,
               constraints.convalidated,
               CASE constraints.conname
                 WHEN 'cpk_gateway_key_rotations_generation_checkpoint_check'
                   THEN pg_get_constraintdef(constraints.oid, false) = %s
                 WHEN 'cpk_gateway_key_rotations_generation_digest_check'
                   THEN pg_get_constraintdef(constraints.oid, false) = %s
                 WHEN 'cpk_gateway_key_rotations_generation_provider_check'
                   THEN pg_get_constraintdef(constraints.oid, false) = %s
                 ELSE false
               END
        FROM pg_constraint AS constraints
        JOIN pg_class AS relation
          ON relation.oid = constraints.conrelid
        JOIN pg_namespace AS namespace
          ON namespace.oid = relation.relnamespace
        WHERE namespace.nspname = current_schema()
          AND relation.relname = 'cpk_gateway_key_rotations'
          AND constraints.conname IN (
            'cpk_gateway_key_rotations_generation_checkpoint_check',
            'cpk_gateway_key_rotations_generation_digest_check',
            'cpk_gateway_key_rotations_generation_provider_check'
          )
        ORDER BY constraints.conname, constraints.oid
        LIMIT 4
        """,
        (
            _GATEWAY_KEY_ROTATION_GENERATION_CHECKPOINT_DEFINITION,
            _GATEWAY_KEY_ROTATION_GENERATION_DIGEST_DEFINITION,
            _GATEWAY_KEY_ROTATION_GENERATION_PROVIDER_DEFINITION,
        ),
        "gateway key rotation generation evidence schema read failed",
    )
    if constraint_rows != list(
        _GATEWAY_KEY_ROTATION_GENERATION_EVIDENCE_CONSTRAINTS
    ):
        raise SchemaMigrationError(
            "gateway key rotation generation evidence schema is not current"
        )


def _verify_gateway_key_rotation_status_contracts(
    connection: PostgresConnection,
) -> None:
    constraint_rows = _read_rows(
        connection,
        """
        SELECT constraints.conname,
               constraints.contype::text,
               constraints.convalidated,
               CASE constraints.conname
                 WHEN 'cpk_gateway_key_rotations_status_check'
                   THEN pg_get_constraintdef(constraints.oid, false) = %s
                 WHEN 'cpk_gateway_key_rotation_transitions_from_status_check'
                   THEN pg_get_constraintdef(constraints.oid, false) = %s
                 WHEN 'cpk_gateway_key_rotation_transitions_to_status_check'
                   THEN pg_get_constraintdef(constraints.oid, false) = %s
                 ELSE false
               END
        FROM pg_constraint AS constraints
        JOIN pg_class AS relation
          ON relation.oid = constraints.conrelid
        JOIN pg_namespace AS namespace
          ON namespace.oid = relation.relnamespace
        WHERE namespace.nspname = current_schema()
          AND (
            (relation.relname = 'cpk_gateway_key_rotations'
             AND constraints.conname =
               'cpk_gateway_key_rotations_status_check')
            OR
            (relation.relname = 'cpk_gateway_key_rotation_transitions'
             AND constraints.conname IN (
               'cpk_gateway_key_rotation_transitions_from_status_check',
               'cpk_gateway_key_rotation_transitions_to_status_check'
             ))
          )
        ORDER BY relation.relname DESC, constraints.conname, constraints.oid
        LIMIT 4
        """,
        (
            _GATEWAY_KEY_ROTATION_STATUS_DEFINITION,
            _GATEWAY_KEY_ROTATION_FROM_STATUS_DEFINITION,
            _GATEWAY_KEY_ROTATION_TO_STATUS_DEFINITION,
        ),
        "gateway key rotation status schema read failed",
    )
    if constraint_rows != list(_GATEWAY_KEY_ROTATION_STATUS_CONSTRAINTS):
        raise SchemaMigrationError(
            "gateway key rotation status schema is not current"
        )


def _verify_gateway_key_rotation_retirement_evidence_contract(
    connection: PostgresConnection,
) -> None:
    column_rows = _read_rows(
        connection,
        """
        SELECT column_name, data_type, datetime_precision, is_nullable,
               column_default IS NULL
        FROM information_schema.columns
        WHERE table_schema = current_schema()
          AND table_name = 'cpk_gateway_key_rotations'
          AND column_name IN ('old_key_retired_at', 'old_secret_revoked_at')
        ORDER BY column_name
        LIMIT 3
        """,
        (),
        "gateway key rotation retirement evidence schema read failed",
    )
    if column_rows != list(_GATEWAY_KEY_ROTATION_RETIREMENT_EVIDENCE_COLUMNS):
        raise SchemaMigrationError(
            "gateway key rotation retirement evidence schema is not current"
        )

    constraint_rows = _read_rows(
        connection,
        """
        SELECT constraints.conname, constraints.contype::text,
               constraints.convalidated,
               pg_get_constraintdef(constraints.oid, false) = %s
        FROM pg_constraint AS constraints
        JOIN pg_class AS relation ON relation.oid = constraints.conrelid
        JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace
        WHERE namespace.nspname = current_schema()
          AND relation.relname = 'cpk_gateway_key_rotations'
          AND constraints.conname =
            'cpk_gateway_key_rotations_retirement_check'
        ORDER BY constraints.conname, constraints.oid
        LIMIT 2
        """,
        (_GATEWAY_KEY_ROTATION_RETIREMENT_DEFINITION,),
        "gateway key rotation retirement evidence schema read failed",
    )
    if constraint_rows != [_GATEWAY_KEY_ROTATION_RETIREMENT_CONSTRAINT]:
        raise SchemaMigrationError(
            "gateway key rotation retirement evidence schema is not current"
        )


def _verify_approval_subject_evidence_contract(
    connection: PostgresConnection,
) -> None:
    column_rows = _read_rows(
        connection,
        """
        SELECT column_name, data_type, is_nullable, column_default IS NULL
        FROM information_schema.columns
        WHERE table_schema = current_schema()
          AND table_name = 'cpk_approval_requests'
          AND column_name IN (
            'plan_id', 'rotation_id', 'subject_kind', 'subject_payload',
            'review_digest'
          )
        ORDER BY column_name
        LIMIT 6
        """,
        (),
        "approval subject evidence schema read failed",
    )
    if column_rows != list(_APPROVAL_SUBJECT_EVIDENCE_COLUMNS):
        raise SchemaMigrationError("approval subject evidence schema is not current")

    constraint_rows = _read_rows(
        connection,
        """
        SELECT constraints.conname, constraints.contype::text,
               constraints.convalidated,
               CASE constraints.conname
                 WHEN 'cpk_approval_requests_review_digest_check' THEN
                   pg_get_constraintdef(constraints.oid, false) = %s
                 WHEN 'cpk_approval_requests_rotation_fk' THEN
                   pg_get_constraintdef(constraints.oid, false) = %s
                 WHEN 'cpk_approval_requests_subject_identity_check' THEN
                   pg_get_constraintdef(constraints.oid, false) = %s
                 WHEN 'cpk_approval_requests_subject_kind_check' THEN
                   pg_get_constraintdef(constraints.oid, false) = %s
                 ELSE false
               END
        FROM pg_constraint AS constraints
        JOIN pg_class AS relation ON relation.oid = constraints.conrelid
        JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace
        WHERE namespace.nspname = current_schema()
          AND relation.relname = 'cpk_approval_requests'
          AND constraints.conname IN (
            'cpk_approval_requests_review_digest_check',
            'cpk_approval_requests_rotation_fk',
            'cpk_approval_requests_subject_identity_check',
            'cpk_approval_requests_subject_kind_check'
          )
        ORDER BY constraints.conname, constraints.oid
        LIMIT 5
        """,
        _APPROVAL_SUBJECT_EVIDENCE_DEFINITIONS,
        "approval subject evidence schema read failed",
    )
    if constraint_rows != list(_APPROVAL_SUBJECT_EVIDENCE_CONSTRAINTS):
        raise SchemaMigrationError("approval subject evidence schema is not current")

    index_rows = _read_rows(
        connection,
        """
        SELECT indexes.relname, indexes.relkind::text,
               access_method.amname,
               index_contract.indisunique,
               index_contract.indisvalid, index_contract.indisready,
               index_contract.indislive,
               index_contract.indnkeyatts = 1,
               index_contract.indnatts = 1,
               pg_get_indexdef(indexes.oid, 1, false) = 'rotation_id',
               pg_get_expr(
                 index_contract.indpred, index_contract.indrelid, false
               ) = '(rotation_id IS NOT NULL)'
        FROM pg_class AS indexes
        JOIN pg_namespace AS namespace ON namespace.oid = indexes.relnamespace
        JOIN pg_am AS access_method ON access_method.oid = indexes.relam
        JOIN pg_index AS index_contract
          ON index_contract.indexrelid = indexes.oid
        JOIN pg_class AS relation ON relation.oid = index_contract.indrelid
        WHERE namespace.nspname = current_schema()
          AND indexes.relname = 'cpk_approval_requests_rotation_identity'
          AND relation.relname = 'cpk_approval_requests'
        ORDER BY indexes.oid
        LIMIT 2
        """,
        (),
        "approval subject evidence schema read failed",
    )
    if index_rows != [
        (
            "cpk_approval_requests_rotation_identity",
            "i",
            "btree",
            True,
            True,
            True,
            True,
            True,
            True,
            True,
            True,
        )
    ]:
        raise SchemaMigrationError("approval subject evidence schema is not current")

    semantic_rows = _read_rows(
        connection,
        """
        SELECT NOT EXISTS (
          SELECT 1
          FROM cpk_approval_requests AS approvals
          LEFT JOIN cpk_gateway_key_rotations AS rotations
            ON rotations.rotation_id = approvals.rotation_id
          WHERE
            (approvals.review_digest COLLATE "C") !~ '^[0-9a-f]{64}$'
            OR CASE
              WHEN (approvals.subject_kind COLLATE "C") = 'activity-plan' THEN NOT (
                approvals.plan_id IS NOT NULL
                AND approvals.rotation_id IS NULL
                AND octet_length(approvals.plan_id) BETWEEN 1 AND 200
                AND (approvals.plan_id COLLATE "C") ~ '^[A-Za-z0-9]'
                AND (approvals.plan_id COLLATE "C") !~ '[^A-Za-z0-9._:-]'
                AND approvals.subject_payload = jsonb_build_object(
                  'kind', 'activity-plan', 'plan_id', approvals.plan_id
                )
                AND (approvals.review_digest COLLATE "C") = encode(
                  sha256(convert_to(
                    'activity-plan:' || approvals.plan_id, 'UTF8'
                  )),
                  'hex'
                )
              )
              WHEN (approvals.subject_kind COLLATE "C") =
                   'gateway-key-rotation' THEN NOT (
                approvals.plan_id IS NULL
                AND approvals.rotation_id IS NOT NULL
                AND rotations.rotation_id IS NOT NULL
                AND octet_length(rotations.rotation_id) BETWEEN 1 AND 200
                AND (rotations.rotation_id COLLATE "C") ~ '^[A-Za-z0-9]'
                AND (rotations.rotation_id COLLATE "C") !~ '[^A-Za-z0-9._:-]'
                AND octet_length(rotations.workspace_id) BETWEEN 1 AND 200
                AND (rotations.workspace_id COLLATE "C") ~ '^[A-Za-z0-9]'
                AND (rotations.workspace_id COLLATE "C") !~ '[^A-Za-z0-9._:-]'
                AND octet_length(rotations.gateway_node_id) BETWEEN 1 AND 200
                AND (rotations.gateway_node_id COLLATE "C") ~ '^[A-Za-z0-9]'
                AND (rotations.gateway_node_id COLLATE "C") !~
                      '[^A-Za-z0-9._:-]'
                AND octet_length(rotations.issuer) BETWEEN 1 AND 200
                AND (rotations.issuer COLLATE "C") ~ '^[A-Za-z0-9]'
                AND (rotations.issuer COLLATE "C") !~ '[^A-Za-z0-9._:-]'
                AND octet_length(rotations.old_key_id) BETWEEN 1 AND 200
                AND (rotations.old_key_id COLLATE "C") ~ '^[A-Za-z0-9]'
                AND (rotations.old_key_id COLLATE "C") !~ '[^A-Za-z0-9._:-]'
                AND (rotations.purpose COLLATE "C") IN (
                  'gateway-probe', 'workload-node-control',
                  'workload-node-control-surface-read'
                )
                AND rotations.maximum_grant_lifetime_seconds BETWEEN 1 AND 300
                AND rotations.clock_skew_seconds BETWEEN 0 AND 60
                AND (rotations.intent_fingerprint COLLATE "C")
                      ~ '^[0-9a-f]{64}$'
                AND approvals.subject_payload = jsonb_build_object(
                  'kind', 'gateway-key-rotation',
                  'rotation_id', rotations.rotation_id,
                  'workspace_id', rotations.workspace_id,
                  'gateway_node_id', rotations.gateway_node_id,
                  'purpose', rotations.purpose,
                  'issuer', rotations.issuer,
                  'old_key_id', rotations.old_key_id,
                  'overlap_verifier_roles', jsonb_build_array('old', 'new'),
                  'retirement_verifier_roles', jsonb_build_array('new'),
                  'maximum_grant_lifetime_seconds',
                    rotations.maximum_grant_lifetime_seconds,
                  'clock_skew_seconds', rotations.clock_skew_seconds,
                  'rotation_intent_digest', rotations.intent_fingerprint
                )
                AND (approvals.review_digest COLLATE "C") = encode(
                  sha256(convert_to(
                    '{"clock_skew_seconds":' ||
                      rotations.clock_skew_seconds::text ||
                      ',"gateway_node_id":' ||
                      to_jsonb(rotations.gateway_node_id)::text ||
                      ',"issuer":' || to_jsonb(rotations.issuer)::text ||
                      ',"kind":"gateway-key-rotation"' ||
                      ',"maximum_grant_lifetime_seconds":' ||
                      rotations.maximum_grant_lifetime_seconds::text ||
                      ',"old_key_id":' || to_jsonb(rotations.old_key_id)::text ||
                      ',"overlap_verifier_roles":["old","new"]' ||
                      ',"purpose":' || to_jsonb(rotations.purpose)::text ||
                      ',"retirement_verifier_roles":["new"]' ||
                      ',"rotation_id":' ||
                      to_jsonb(rotations.rotation_id)::text ||
                      ',"rotation_intent_digest":' ||
                      to_jsonb(rotations.intent_fingerprint)::text ||
                      ',"workspace_id":' ||
                      to_jsonb(rotations.workspace_id)::text || '}',
                    'UTF8'
                  )),
                  'hex'
                )
              )
              ELSE true
            END
        )
        """,
        (),
        "approval subject evidence schema read failed",
    )
    if semantic_rows != [(True,)]:
        raise SchemaMigrationError("approval subject evidence is not current")


def _verify_approval_scope_contracts(connection: PostgresConnection) -> None:
    constraint_rows = _read_rows(
        connection,
        """
        SELECT relation.relname, constraints.conname,
               constraints.contype::text, constraints.convalidated,
               CASE
                 WHEN relation.relname = 'cpk_approval_requests'
                  AND constraints.conname =
                    'cpk_approval_requests_scope_check'
                 THEN pg_get_constraintdef(constraints.oid, false) = %s
                 WHEN relation.relname = 'cpk_approval_decisions'
                  AND constraints.conname =
                    'cpk_approval_decisions_scope_check'
                 THEN pg_get_constraintdef(constraints.oid, false) = %s
                 ELSE false
               END
        FROM pg_constraint AS constraints
        JOIN pg_class AS relation ON relation.oid = constraints.conrelid
        JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace
        WHERE namespace.nspname = current_schema()
          AND (
            (relation.relname = 'cpk_approval_requests'
             AND constraints.conname =
               'cpk_approval_requests_scope_check')
            OR
            (relation.relname = 'cpk_approval_decisions'
             AND constraints.conname =
               'cpk_approval_decisions_scope_check')
          )
        ORDER BY relation.relname, constraints.conname, constraints.oid
        LIMIT 3
        """,
        (
            _APPROVAL_REQUEST_SCOPE_DEFINITION,
            _APPROVAL_DECISION_SCOPE_DEFINITION,
        ),
        "approval scope schema read failed",
    )
    if constraint_rows != list(_APPROVAL_SCOPE_CONTRACTS):
        raise SchemaMigrationError("approval scope schema is not current")


def _verify_delegation_key_purpose_contracts(
    connection: PostgresConnection,
) -> None:
    constraint_rows = _read_rows(
        connection,
        """
        SELECT relation.relname, constraints.conname,
               constraints.contype::text, constraints.convalidated,
               pg_get_constraintdef(constraints.oid, false) = %s
        FROM pg_constraint AS constraints
        JOIN pg_class AS relation ON relation.oid = constraints.conrelid
        JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace
        WHERE namespace.nspname = current_schema()
          AND (
            (relation.relname = 'cpk_delegation_signing_keys'
             AND constraints.conname =
               'cpk_delegation_signing_keys_purpose_check')
            OR
            (relation.relname = 'cpk_gateway_key_rotations'
             AND constraints.conname =
               'cpk_gateway_key_rotations_purpose_check')
          )
        ORDER BY relation.relname, constraints.conname, constraints.oid
        LIMIT 3
        """,
        (_DELEGATION_KEY_PURPOSE_DEFINITION,),
        "delegation key purpose schema read failed",
    )
    if constraint_rows != list(_DELEGATION_KEY_PURPOSE_CONTRACTS):
        raise SchemaMigrationError("delegation key purpose schema is not current")


def _verify_graph_lineage_contracts(connection: PostgresConnection) -> None:
    lock_graph_lineage_v1(connection)
    column_rows = _read_rows(
        connection,
        """
        SELECT table_name, column_name,
               CASE
                 WHEN table_name = 'cpk_workspaces'
                      AND column_name IN ('current_realized_projection_id',
                                          'desired_realized_projection_id')
                 THEN data_type = 'text' AND is_nullable = 'YES'
                      AND column_default IS NULL
                 WHEN table_name = 'cpk_activity_plans'
                      AND column_name IN ('base_realized_projection_id',
                                          'desired_realized_projection_id')
                 THEN data_type = 'text' AND is_nullable = 'NO'
                      AND column_default IS NULL
                 WHEN column_name = 'desired_graph_revision'
                 THEN data_type = 'bigint' AND is_nullable = 'NO'
                      AND column_default IS NOT DISTINCT FROM '0'
                 ELSE false
               END
        FROM information_schema.columns
        WHERE table_schema = current_schema()
          AND (table_name, column_name) IN (
            ('cpk_workspaces', 'current_realized_projection_id'),
            ('cpk_workspaces', 'desired_realized_projection_id'),
            ('cpk_workspaces', 'desired_graph_revision'),
            ('cpk_activity_plans', 'base_realized_projection_id'),
            ('cpk_activity_plans', 'desired_realized_projection_id'),
            ('cpk_activity_plans', 'desired_graph_revision')
          )
        ORDER BY table_name, column_name
        LIMIT 7
        """,
        (),
        "graph lineage schema read failed",
    )
    expected_columns = [
        ("cpk_activity_plans", "base_realized_projection_id", True),
        ("cpk_activity_plans", "desired_graph_revision", True),
        ("cpk_activity_plans", "desired_realized_projection_id", True),
        ("cpk_workspaces", "current_realized_projection_id", True),
        ("cpk_workspaces", "desired_graph_revision", True),
        ("cpk_workspaces", "desired_realized_projection_id", True),
    ]
    if column_rows != expected_columns:
        raise SchemaMigrationError("graph lineage schema is not current")

    target_contracts = tuple(
        (table, name, kind, definition)
        for table, name, kind, _ddl, definition in _POSTGRES_SCHEMA_V17_CONSTRAINTS
    )
    if not _graph_lineage_contracts_are_current(connection, target_contracts):
        raise SchemaMigrationError("graph lineage schema is not current")
    if not _graph_lineage_contracts_are_current(
        connection,
        _POSTGRES_SCHEMA_V17_DEPENDENCIES,
    ):
        raise SchemaMigrationError("graph lineage schema is not current")
    verify_graph_lineage_v1(connection)


def _graph_lineage_contracts_are_current(
    connection: PostgresConnection,
    contracts: tuple[tuple[str, str, str, str], ...],
) -> bool:
    rows = _read_rows(
        connection,
        """
        WITH expected(relation_name, constraint_name, constraint_kind,
                      definition) AS (
          SELECT * FROM unnest(%s::text[], %s::text[], %s::text[], %s::text[])
        )
        SELECT expected.relation_name, expected.constraint_name,
               count(constraints.oid) = 1
               AND COALESCE(bool_and(
                     constraints.contype::text = expected.constraint_kind
                     AND constraints.convalidated IS TRUE
                     AND pg_get_constraintdef(constraints.oid, false)
                           = expected.definition
                   ), false)
        FROM expected
        LEFT JOIN pg_namespace AS namespace
          ON namespace.nspname = current_schema()
        LEFT JOIN pg_class AS relation
          ON relation.relnamespace = namespace.oid
         AND relation.relname = expected.relation_name
        LEFT JOIN pg_constraint AS constraints
          ON constraints.conrelid = relation.oid
         AND constraints.conname = expected.constraint_name
        GROUP BY expected.relation_name, expected.constraint_name,
                 expected.constraint_kind, expected.definition
        ORDER BY expected.relation_name, expected.constraint_name
        LIMIT %s
        """,
        (
            [contract[0] for contract in contracts],
            [contract[1] for contract in contracts],
            [contract[2] for contract in contracts],
            [contract[3] for contract in contracts],
            len(contracts) + 1,
        ),
        "graph lineage schema read failed",
    )
    return rows == sorted(
        (table, name, True) for table, name, _kind, _definition in contracts
    )


def _read_coordination_temporal_contract(
    connection: PostgresConnection,
) -> tuple[tuple[str, str, str, int, str, bool], ...]:
    rows = _read_rows(
        connection,
        """
        SELECT table_name, column_name, data_type, datetime_precision, is_nullable,
               column_default IS NULL
        FROM information_schema.columns
        WHERE table_schema = current_schema()
          AND (table_name, column_name) IN (
            ('cpk_activity_events', 'occurred_at'),
            ('cpk_activity_plans', 'created_at'),
            ('cpk_activity_runs', 'created_at'),
            ('cpk_activity_runs', 'settled_at'),
            ('cpk_activity_runs', 'started_at'),
            ('cpk_approval_decisions', 'decided_at'),
            ('cpk_approval_requests', 'requested_at'),
            ('cpk_execution_requests', 'claimed_at'),
            ('cpk_execution_requests', 'lease_expires_at'),
            ('cpk_execution_requests', 'requested_at'),
            ('cpk_observations', 'observed_at'),
            ('cpk_operation_actions', 'created_at'),
            ('cpk_operation_sessions', 'closed_at'),
            ('cpk_operation_sessions', 'created_at')
          )
        ORDER BY table_name, column_name
        LIMIT %s
        """,
        (len(_COORDINATION_TEMPORAL_CONTRACT) + 1,),
        "coordination temporal schema read failed",
    )
    if len(rows) != len(_COORDINATION_TEMPORAL_CONTRACT):
        raise SchemaMigrationError("coordination temporal schema is not current")
    return tuple(rows)


def _read_graph_product_authority_temporal_contract(
    connection: PostgresConnection,
) -> tuple[tuple[str, str, str, int, str, bool], ...]:
    rows = _read_rows(
        connection,
        """
        SELECT table_name, column_name, data_type, datetime_precision, is_nullable,
               column_default IS NULL
        FROM information_schema.columns
        WHERE table_schema = current_schema()
          AND (table_name, column_name) IN (
            ('cpk_graph_versions', 'created_at'),
            ('cpk_image_pull_authorities', 'admitted_at'),
            ('cpk_ingress_authorities', 'admitted_at'),
            ('cpk_realized_graph_projections', 'created_at'),
            ('cpk_registered_products', 'imported_at'),
            ('cpk_runtime_authorities', 'admitted_at'),
            ('cpk_runtime_authority_deliveries', 'admitted_at')
          )
        ORDER BY table_name, column_name
        LIMIT %s
        """,
        (len(_GRAPH_PRODUCT_AUTHORITY_TEMPORAL_CONTRACT) + 1,),
        "graph, product, and authority temporal schema read failed",
    )
    if len(rows) != len(_GRAPH_PRODUCT_AUTHORITY_TEMPORAL_CONTRACT):
        raise SchemaMigrationError(
            "graph, product, and authority temporal schema is not current"
        )
    return tuple(rows)


def _read_secret_registration_temporal_contract(
    connection: PostgresConnection,
) -> tuple[tuple[str, str, str, int, str, bool], ...]:
    rows = _read_rows(
        connection,
        """
        SELECT table_name, column_name, data_type, datetime_precision, is_nullable,
               column_default IS NULL
        FROM information_schema.columns
        WHERE table_schema = current_schema()
          AND (table_name, column_name) IN (
            ('cpk_secret_providers', 'admitted_at'),
            ('cpk_secret_providers', 'revoked_at'),
            ('cpk_secret_references', 'admitted_at'),
            ('cpk_secret_references', 'revoked_at')
          )
        ORDER BY table_name, column_name
        LIMIT %s
        """,
        (len(_SECRET_REGISTRATION_TEMPORAL_CONTRACT) + 1,),
        "secret registration temporal schema read failed",
    )
    if len(rows) != len(_SECRET_REGISTRATION_TEMPORAL_CONTRACT):
        raise SchemaMigrationError(
            "secret registration temporal schema is not current"
        )
    return tuple(rows)


def _read_delegation_signing_key_temporal_contract(
    connection: PostgresConnection,
) -> tuple[tuple[str, str, str, int, str, bool], ...]:
    rows = _read_rows(
        connection,
        """
        SELECT table_name, column_name, data_type, datetime_precision, is_nullable,
               column_default IS NULL
        FROM information_schema.columns
        WHERE table_schema = current_schema()
          AND table_name = 'cpk_delegation_signing_keys'
          AND column_name IN (
            'admitted_at', 'activated_at', 'retired_at', 'revoked_at'
          )
        ORDER BY table_name, column_name
        LIMIT %s
        """,
        (len(_DELEGATION_SIGNING_KEY_TEMPORAL_CONTRACT) + 1,),
        "delegation signing-key temporal schema read failed",
    )
    if len(rows) != len(_DELEGATION_SIGNING_KEY_TEMPORAL_CONTRACT):
        raise SchemaMigrationError(
            "delegation signing-key temporal schema is not current"
        )
    return tuple(rows)


def _read_gateway_probe_temporal_contract(
    connection: PostgresConnection,
) -> tuple[tuple[str, str, str, int, str, bool], ...]:
    rows = _read_rows(
        connection,
        """
        SELECT table_name, column_name, data_type, datetime_precision, is_nullable,
               column_default IS NULL
        FROM information_schema.columns
        WHERE table_schema = current_schema()
          AND table_name = 'cpk_gateway_probe_attempts'
          AND column_name IN ('completed_at', 'requested_at')
        ORDER BY table_name, column_name
        LIMIT %s
        """,
        (len(_GATEWAY_PROBE_TEMPORAL_CONTRACT) + 1,),
        "gateway probe temporal schema read failed",
    )
    if len(rows) != len(_GATEWAY_PROBE_TEMPORAL_CONTRACT):
        raise SchemaMigrationError("gateway probe temporal schema is not current")
    return tuple(rows)


def _read_gateway_key_rotation_temporal_contract(
    connection: PostgresConnection,
) -> tuple[tuple[str, str, str, int, str, bool], ...]:
    rows = _read_rows(
        connection,
        """
        SELECT table_name, column_name, data_type, datetime_precision, is_nullable,
               column_default IS NULL
        FROM information_schema.columns
        WHERE table_schema = current_schema()
          AND (table_name, column_name) IN (
            ('cpk_gateway_key_rotation_deployments', 'accepted_at'),
            ('cpk_gateway_key_rotation_deployments', 'prepared_at'),
            ('cpk_gateway_key_rotation_revocations', 'prepared_at'),
            ('cpk_gateway_key_rotation_transitions', 'advanced_at'),
            ('cpk_gateway_key_rotations', 'new_key_activated_at'),
            ('cpk_gateway_key_rotations', 'old_key_retired_at'),
            ('cpk_gateway_key_rotations', 'old_secret_revoked_at'),
            ('cpk_gateway_key_rotations', 'requested_at'),
            ('cpk_gateway_key_rotations', 'updated_at')
          )
        ORDER BY table_name, column_name
        LIMIT %s
        """,
        (len(_GATEWAY_KEY_ROTATION_TEMPORAL_CONTRACT) + 1,),
        "gateway key rotation temporal schema read failed",
    )
    if len(rows) != len(_GATEWAY_KEY_ROTATION_TEMPORAL_CONTRACT):
        raise SchemaMigrationError(
            "gateway key rotation temporal schema is not current"
        )
    return tuple(rows)


def _read_ingress_evidence_temporal_contract(
    connection: PostgresConnection,
) -> tuple[tuple[str, str, str, int, str, bool], ...]:
    rows = _read_rows(
        connection,
        """
        SELECT table_name, column_name, data_type, datetime_precision, is_nullable,
               column_default IS NULL
        FROM information_schema.columns
        WHERE table_schema = current_schema()
          AND (table_name, column_name) IN (
            ('cpk_cloudflare_ingress_resources', 'created_at'),
            ('cpk_cloudflare_ingress_resources', 'observed_at'),
            ('cpk_cloudflare_ingress_resources', 'removed_at'),
            ('cpk_generated_ingress_secret_references', 'recorded_at')
          )
        ORDER BY table_name, column_name
        LIMIT %s
        """,
        (len(_INGRESS_EVIDENCE_TEMPORAL_CONTRACT) + 1,),
        "ingress evidence temporal schema read failed",
    )
    if len(rows) != len(_INGRESS_EVIDENCE_TEMPORAL_CONTRACT):
        raise SchemaMigrationError(
            "ingress evidence temporal schema is not current"
        )
    return tuple(rows)


def _read_secret_use_authorization_temporal_contract(
    connection: PostgresConnection,
) -> tuple[tuple[str, str, str, int, str, bool], ...]:
    rows = _read_rows(
        connection,
        """
        SELECT table_name, column_name, data_type, datetime_precision, is_nullable,
               column_default IS NULL
        FROM information_schema.columns
        WHERE table_schema = current_schema()
          AND table_name = 'cpk_secret_use_authorizations'
          AND column_name = 'requested_at'
        ORDER BY table_name, column_name
        LIMIT %s
        """,
        (len(_SECRET_USE_AUTHORIZATION_TEMPORAL_CONTRACT) + 1,),
        "secret-use authorization temporal schema read failed",
    )
    if len(rows) != len(_SECRET_USE_AUTHORIZATION_TEMPORAL_CONTRACT):
        raise SchemaMigrationError(
            "secret-use authorization temporal schema is not current"
        )
    return tuple(rows)


def _read_catalog(
    connection: PostgresConnection,
) -> tuple[
    tuple[tuple[str, tuple[str, ...]], ...],
    tuple[tuple[str, str, str, bool], ...] | None,
    bool | None,
]:
    table_rows = _read_rows(
        connection,
        """
        SELECT table_name, table_type
        FROM information_schema.tables
        WHERE table_schema = current_schema()
        ORDER BY table_name
        LIMIT %s
        """,
        (_MAX_CATALOG_TABLES,),
        "database schema catalog read failed",
    )
    if len(table_rows) == _MAX_CATALOG_TABLES:
        raise SchemaMigrationError("database schema catalog exceeds inspection bound")
    for _table, table_type in table_rows:
        if table_type != "BASE TABLE":
            raise SchemaMigrationError("database schema objects are not accepted")

    rows = _read_rows(
        connection,
        """
        SELECT columns.table_name, columns.column_name, columns.data_type,
               columns.is_nullable,
               CASE
                 WHEN columns.column_name = 'applied_at'
                   THEN columns.column_default = 'clock_timestamp()'
                 ELSE columns.column_default IS NULL
               END AS default_is_accepted
        FROM information_schema.columns AS columns
        JOIN information_schema.tables AS tables
          ON tables.table_schema = columns.table_schema
         AND tables.table_name = columns.table_name
        WHERE columns.table_schema = current_schema()
        ORDER BY columns.table_name, columns.ordinal_position
        LIMIT %s
        """,
        (_MAX_CATALOG_COLUMNS,),
        "database schema catalog read failed",
    )
    if len(rows) == _MAX_CATALOG_COLUMNS:
        raise SchemaMigrationError("database schema catalog exceeds inspection bound")

    ledger_seen = any(
        table == POSTGRES_SCHEMA_MIGRATION_LEDGER_TABLE for table, _ in table_rows
    )
    application_columns: dict[str, list[str]] = {
        table: []
        for table, _ in table_rows
        if table != POSTGRES_SCHEMA_MIGRATION_LEDGER_TABLE
    }
    ledger_contract: list[tuple[str, str, str, bool]] = []
    for table, column, data_type, is_nullable, default_is_accepted in rows:
        if table == POSTGRES_SCHEMA_MIGRATION_LEDGER_TABLE:
            ledger_contract.append(
                (column, data_type, is_nullable, default_is_accepted)
            )
        else:
            application_columns.setdefault(table, []).append(column)
    application_manifest = tuple(
        (table, tuple(columns)) for table, columns in application_columns.items()
    )
    ledger_primary_key = None
    if ledger_seen:
        primary_key_rows = _read_rows(
            connection,
            """
            SELECT COUNT(*) = 1
               AND COALESCE(
                     BOOL_AND(
                       key_columns.column_name = 'version'
                       AND key_columns.ordinal_position = 1
                     ),
                     FALSE
                   )
            FROM information_schema.table_constraints AS constraints
            JOIN information_schema.key_column_usage AS key_columns
              ON key_columns.constraint_schema = constraints.constraint_schema
             AND key_columns.constraint_name = constraints.constraint_name
             AND key_columns.table_schema = constraints.table_schema
             AND key_columns.table_name = constraints.table_name
            WHERE constraints.table_schema = current_schema()
              AND constraints.table_name = %s
              AND constraints.constraint_type = 'PRIMARY KEY'
            """,
            (POSTGRES_SCHEMA_MIGRATION_LEDGER_TABLE,),
            "database schema catalog read failed",
        )
        if (
            len(primary_key_rows) != 1
            or len(primary_key_rows[0]) != 1
            or type(primary_key_rows[0][0]) is not bool
        ):
            raise SchemaMigrationError(
                "schema migration ledger primary key is not accepted"
            )
        ledger_primary_key = primary_key_rows[0][0]
    return (
        application_manifest,
        tuple(ledger_contract) if ledger_seen else None,
        ledger_primary_key,
    )


def _read_rows(
    connection: PostgresConnection,
    query: str,
    parameters: tuple[object, ...],
    failure_message: str,
) -> list[tuple[object, ...]]:
    try:
        rows = connection.execute(query, parameters).fetchall()
    except Exception:
        rows = None
    if rows is None:
        raise SchemaMigrationError(failure_message)
    return rows
