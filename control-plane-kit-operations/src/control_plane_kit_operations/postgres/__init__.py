"""Postgres schema foundation for durable control-plane operations."""

from control_plane_kit_operations.postgres.schema import (
    POSTGRES_SCHEMA,
    POSTGRES_SCHEMA_MIGRATIONS,
    POSTGRES_SCHEMA_V1_SHA256,
    PostgresConnection,
    install_schema,
)
from control_plane_kit_operations.postgres.migrations import (
    AppliedSchemaMigration,
    ObservedSchemaKind,
    ObservedSchemaState,
    SchemaMigration,
    SchemaMigrationAction,
    SchemaMigrationActionKind,
    SchemaMigrationError,
    SchemaMigrationPlan,
    SchemaMigrationRegistry,
)
from control_plane_kit_operations.postgres.migration_inspection import (
    POSTGRES_SCHEMA_MIGRATION_LEDGER_COLUMNS,
    POSTGRES_SCHEMA_MIGRATION_LEDGER_TABLE,
    POSTGRES_SCHEMA_V1_TABLE_COLUMNS,
    inspect_postgres_schema,
    verify_postgres_schema,
)
from control_plane_kit_operations.postgres.migration_runner import (
    MigrationPostgresConnection,
    install_postgres_schema,
    plan_postgres_schema_install,
)
from control_plane_kit_operations.postgres.activity_history import (
    PostgresActivityHistoryStore,
)
from control_plane_kit_operations.postgres.execution import PostgresExecutionStore
from control_plane_kit_operations.postgres.delegation_signing_key_store import (
    DelegationSigningKeyStore,
)
from control_plane_kit_operations.postgres.graph_store import (
    PostgresGraphTopologyStore,
    PostgresRealizedGraphProjectionStore,
    PostgresWorkspaceStore,
    RealizedGraphProjectionConflict,
)
from control_plane_kit_operations.postgres.gateway_probe_store import GatewayProbeStore
from control_plane_kit_operations.postgres.gateway_key_rotation_store import (
    GatewayKeyRotationStore,
)
from control_plane_kit_operations.postgres.image_pull_authority_store import (
    ImagePullAuthorityStore,
)
from control_plane_kit_operations.postgres.ingress_authority_store import (
    GeneratedIngressSecretReferenceStore,
    IngressAuthorityStore,
    IngressResourceStore,
)
from control_plane_kit_operations.postgres.observed_state import (
    PostgresObservedStateStore,
)
from control_plane_kit_operations.postgres.product_store import RegisteredProductStore
from control_plane_kit_operations.postgres.runtime_authority_store import (
    RuntimeAuthorityDeliveryStore,
    RuntimeAuthorityStore,
)
from control_plane_kit_operations.postgres.secret_provider_store import (
    SecretProviderStore,
    SecretReferenceStore,
    SecretUseAuthorizationStore,
)
from control_plane_kit_operations.postgres.stores import PostgresStoreBundle
from control_plane_kit_operations.postgres.unit_of_work import (
    PostgresConnectionFactory,
    PostgresUnitOfWork,
    TransactionalPostgresConnection,
    UnitOfWorkStateError,
)

__all__ = [
    "POSTGRES_SCHEMA",
    "POSTGRES_SCHEMA_MIGRATIONS",
    "POSTGRES_SCHEMA_MIGRATION_LEDGER_COLUMNS",
    "POSTGRES_SCHEMA_MIGRATION_LEDGER_TABLE",
    "POSTGRES_SCHEMA_V1_TABLE_COLUMNS",
    "POSTGRES_SCHEMA_V1_SHA256",
    "AppliedSchemaMigration",
    "PostgresActivityHistoryStore",
    "PostgresExecutionStore",
    "DelegationSigningKeyStore",
    "PostgresGraphTopologyStore",
    "PostgresRealizedGraphProjectionStore",
    "RealizedGraphProjectionConflict",
    "GatewayProbeStore",
    "GatewayKeyRotationStore",
    "ImagePullAuthorityStore",
    "MigrationPostgresConnection",
    "GeneratedIngressSecretReferenceStore",
    "IngressAuthorityStore",
    "IngressResourceStore",
    "PostgresObservedStateStore",
    "PostgresConnection",
    "PostgresConnectionFactory",
    "PostgresStoreBundle",
    "PostgresUnitOfWork",
    "PostgresWorkspaceStore",
    "RegisteredProductStore",
    "RuntimeAuthorityStore",
    "RuntimeAuthorityDeliveryStore",
    "SecretProviderStore",
    "SecretReferenceStore",
    "SecretUseAuthorizationStore",
    "ObservedSchemaKind",
    "ObservedSchemaState",
    "SchemaMigration",
    "SchemaMigrationAction",
    "SchemaMigrationActionKind",
    "SchemaMigrationError",
    "SchemaMigrationPlan",
    "SchemaMigrationRegistry",
    "TransactionalPostgresConnection",
    "UnitOfWorkStateError",
    "install_schema",
    "install_postgres_schema",
    "inspect_postgres_schema",
    "plan_postgres_schema_install",
    "verify_postgres_schema",
]
