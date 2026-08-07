"""Read-only Postgres interpretation of CPK schema migration truth."""

from __future__ import annotations

from control_plane_kit_operations.postgres.migrations import (
    AppliedSchemaMigration,
    ObservedSchemaKind,
    ObservedSchemaState,
    SchemaMigrationError,
)
from control_plane_kit_operations.postgres.schema import (
    POSTGRES_SCHEMA_MIGRATIONS,
    PostgresConnection,
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
_V1_COLUMNS_BY_TABLE = dict(POSTGRES_SCHEMA_V1_TABLE_COLUMNS)


def _historical_append_order(
    table: str,
    appended_columns: tuple[str, ...],
) -> tuple[str, ...]:
    appended = frozenset(appended_columns)
    return (
        *(column for column in _V1_COLUMNS_BY_TABLE[table] if column not in appended),
        *appended_columns,
    )


_V1_HISTORICAL_COLUMN_ORDERS = {
    "cpk_registered_products": _historical_append_order(
        "cpk_registered_products", ("descriptor_content",)
    ),
    "cpk_gateway_probe_attempts": _historical_append_order(
        "cpk_gateway_probe_attempts", ("access_path",)
    ),
    "cpk_gateway_key_rotations": _historical_append_order(
        "cpk_gateway_key_rotations",
        ("generation_provider_registration_id", "generation_action_digest"),
    ),
    "cpk_approval_requests": _historical_append_order(
        "cpk_approval_requests",
        ("rotation_id", "subject_kind", "subject_payload", "review_digest"),
    ),
    "cpk_workspaces": _historical_append_order(
        "cpk_workspaces",
        (
            "current_realized_projection_id",
            "desired_realized_projection_id",
            "desired_graph_revision",
        ),
    ),
    "cpk_activity_plans": _historical_append_order(
        "cpk_activity_plans",
        (
            "base_realized_projection_id",
            "desired_realized_projection_id",
            "desired_graph_revision",
        ),
    ),
}
_MAX_CATALOG_TABLES = len(POSTGRES_SCHEMA_V1_TABLE_COLUMNS) + 2
_MAX_CATALOG_COLUMNS = (
    sum(len(columns) for _, columns in POSTGRES_SCHEMA_V1_TABLE_COLUMNS)
    + len(POSTGRES_SCHEMA_MIGRATION_LEDGER_COLUMNS)
    + 1
)


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


def verify_postgres_schema(connection: PostgresConnection) -> ObservedSchemaState:
    """Require canonical current migration history and exact V1 structure."""

    observed = inspect_postgres_schema(connection)
    if observed.kind is not ObservedSchemaKind.VERSIONED:
        raise SchemaMigrationError("database schema is not versioned")
    application_manifest, ledger_contract, ledger_primary_key = _read_catalog(
        connection
    )
    if not _is_accepted_current_manifest(application_manifest):
        raise SchemaMigrationError("database schema manifest is not current")
    if (
        ledger_contract != _POSTGRES_SCHEMA_MIGRATION_LEDGER_CONTRACT
        or ledger_primary_key is not True
    ):
        raise SchemaMigrationError("schema migration ledger contract is not current")
    if POSTGRES_SCHEMA_MIGRATIONS.plan(observed).actions:
        raise SchemaMigrationError("database schema has pending migrations")
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
    return observed


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


def _is_accepted_current_manifest(
    observed: tuple[tuple[str, tuple[str, ...]], ...],
) -> bool:
    if tuple(table for table, _ in observed) != tuple(
        table for table, _ in POSTGRES_SCHEMA_V1_TABLE_COLUMNS
    ):
        return False
    for table, columns in observed:
        canonical = _V1_COLUMNS_BY_TABLE[table]
        historical = _V1_HISTORICAL_COLUMN_ORDERS.get(table)
        if columns != canonical and columns != historical:
            return False
    return True


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
