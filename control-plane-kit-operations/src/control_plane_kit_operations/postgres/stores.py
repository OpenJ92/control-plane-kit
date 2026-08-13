"""Store bundle boundary for Postgres-backed operations."""

from __future__ import annotations

from dataclasses import dataclass, field

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
)
from control_plane_kit_operations.postgres.gateway_probe_store import GatewayProbeStore
from control_plane_kit_operations.postgres.node_control_attempt_store import (
    NodeControlAttemptStore,
)
from control_plane_kit_operations.postgres.node_control_signing_authority_store import (
    _NodeControlSigningAuthorityStore,
)
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
from control_plane_kit_operations.postgres.schema import PostgresConnection


@dataclass(frozen=True)
class PostgresStoreBundle:
    """Stores bound to one caller-owned Postgres connection.

    Domain stores are added in later issues. The bundle already preserves the
    important ownership law: every future store is constructed from this single
    connection and cannot commit independently through the bundle.
    """

    connection: PostgresConnection
    workspaces: PostgresWorkspaceStore = field(init=False)
    graphs: PostgresGraphTopologyStore = field(init=False)
    realized_graphs: PostgresRealizedGraphProjectionStore = field(init=False)
    registered_products: RegisteredProductStore = field(init=False)
    image_pull_authorities: ImagePullAuthorityStore = field(init=False)
    ingress_authorities: IngressAuthorityStore = field(init=False)
    ingress_resources: IngressResourceStore = field(init=False)
    generated_ingress_secrets: GeneratedIngressSecretReferenceStore = field(init=False)
    runtime_authorities: RuntimeAuthorityStore = field(init=False)
    runtime_authority_deliveries: RuntimeAuthorityDeliveryStore = field(init=False)
    secret_providers: SecretProviderStore = field(init=False)
    secret_references: SecretReferenceStore = field(init=False)
    secret_use_authorizations: SecretUseAuthorizationStore = field(init=False)
    activity_history: PostgresActivityHistoryStore = field(init=False)
    execution: PostgresExecutionStore = field(init=False)
    observed_state: PostgresObservedStateStore = field(init=False)
    gateway_probes: GatewayProbeStore = field(init=False)
    node_control_attempts: NodeControlAttemptStore = field(init=False)
    node_control_signing_authority: _NodeControlSigningAuthorityStore = field(
        init=False
    )
    delegation_signing_keys: DelegationSigningKeyStore = field(init=False)
    gateway_key_rotations: GatewayKeyRotationStore = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "workspaces",
            PostgresWorkspaceStore(self.connection),
        )
        object.__setattr__(
            self,
            "graphs",
            PostgresGraphTopologyStore(self.connection),
        )
        object.__setattr__(
            self,
            "realized_graphs",
            PostgresRealizedGraphProjectionStore(self.connection),
        )
        object.__setattr__(
            self,
            "registered_products",
            RegisteredProductStore(self.connection),
        )
        object.__setattr__(
            self,
            "image_pull_authorities",
            ImagePullAuthorityStore(self.connection),
        )
        object.__setattr__(
            self,
            "ingress_authorities",
            IngressAuthorityStore(self.connection),
        )
        object.__setattr__(
            self,
            "ingress_resources",
            IngressResourceStore(self.connection),
        )
        object.__setattr__(
            self,
            "generated_ingress_secrets",
            GeneratedIngressSecretReferenceStore(self.connection),
        )
        object.__setattr__(
            self,
            "runtime_authorities",
            RuntimeAuthorityStore(self.connection),
        )
        object.__setattr__(
            self,
            "runtime_authority_deliveries",
            RuntimeAuthorityDeliveryStore(self.connection),
        )
        object.__setattr__(
            self,
            "secret_providers",
            SecretProviderStore(self.connection),
        )
        object.__setattr__(
            self,
            "secret_references",
            SecretReferenceStore(self.connection),
        )
        object.__setattr__(
            self,
            "secret_use_authorizations",
            SecretUseAuthorizationStore(self.connection),
        )
        object.__setattr__(
            self,
            "activity_history",
            PostgresActivityHistoryStore(self.connection),
        )
        object.__setattr__(
            self,
            "execution",
            PostgresExecutionStore(self.connection),
        )
        object.__setattr__(
            self,
            "observed_state",
            PostgresObservedStateStore(self.connection),
        )
        object.__setattr__(
            self,
            "gateway_probes",
            GatewayProbeStore(self.connection),
        )
        object.__setattr__(
            self,
            "node_control_attempts",
            NodeControlAttemptStore(self.connection),
        )
        object.__setattr__(
            self,
            "node_control_signing_authority",
            _NodeControlSigningAuthorityStore(self.connection),
        )
        object.__setattr__(
            self,
            "delegation_signing_keys",
            DelegationSigningKeyStore(self.connection),
        )
        object.__setattr__(
            self,
            "gateway_key_rotations",
            GatewayKeyRotationStore(self.connection),
        )
