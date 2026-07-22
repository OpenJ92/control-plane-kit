"""Postgres schema foundation for durable control-plane operations."""

from control_plane_kit_operations.postgres.schema import (
    POSTGRES_SCHEMA,
    PostgresConnection,
    install_schema,
)
from control_plane_kit_operations.postgres.product_store import RegisteredProductStore
from control_plane_kit_operations.postgres.stores import PostgresStoreBundle
from control_plane_kit_operations.postgres.unit_of_work import (
    PostgresConnectionFactory,
    PostgresUnitOfWork,
    TransactionalPostgresConnection,
    UnitOfWorkStateError,
)

__all__ = [
    "POSTGRES_SCHEMA",
    "PostgresConnection",
    "PostgresConnectionFactory",
    "PostgresStoreBundle",
    "PostgresUnitOfWork",
    "RegisteredProductStore",
    "TransactionalPostgresConnection",
    "UnitOfWorkStateError",
    "install_schema",
]
