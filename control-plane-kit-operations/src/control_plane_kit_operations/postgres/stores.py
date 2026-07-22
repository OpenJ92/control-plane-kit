"""Store bundle boundary for Postgres-backed operations."""

from __future__ import annotations

from dataclasses import dataclass, field

from control_plane_kit_operations.postgres.activity_history import (
    PostgresActivityHistoryStore,
)
from control_plane_kit_operations.postgres.execution import PostgresExecutionStore
from control_plane_kit_operations.postgres.graph_store import (
    PostgresGraphTopologyStore,
    PostgresWorkspaceStore,
)
from control_plane_kit_operations.postgres.product_store import RegisteredProductStore
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
    registered_products: RegisteredProductStore = field(init=False)
    activity_history: PostgresActivityHistoryStore = field(init=False)
    execution: PostgresExecutionStore = field(init=False)

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
            "registered_products",
            RegisteredProductStore(self.connection),
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
