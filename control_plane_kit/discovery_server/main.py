"""Process composition root for one durable service-discovery server."""

from __future__ import annotations

import psycopg
import uvicorn

from control_plane_kit.contracts import (
    EnvironmentContract,
    PostgresVariable,
    SecretVariable,
    TextVariable,
)
from control_plane_kit.discovery_registry import (
    DiscoveryRegistryService,
    PostgresDiscoveryUnitOfWork,
    install_discovery_schema,
)
from control_plane_kit.discovery_server.app import create_service_discovery_app


class ServiceDiscoveryEnvironment(EnvironmentContract):
    database_url = PostgresVariable(
        "database_url", metadata={"env": "DISCOVERY_DATABASE_URL"}
    )
    identity_token = SecretVariable(
        "identity_token", metadata={"env": "CPK_DISCOVERY_IDENTITY_TOKEN"}
    )
    port = TextVariable(
        "port", required=False, metadata={"env": "CPK_DISCOVERY_PORT"}
    )


def psycopg_connection_string(value: str) -> str:
    """Interpret graph Postgres URL identity for the direct psycopg driver."""

    prefix = "postgresql+psycopg://"
    return (
        "postgresql://" + value[len(prefix) :]
        if value.startswith(prefix)
        else value
    )


def main() -> None:
    environment = ServiceDiscoveryEnvironment.from_process()
    database_url = psycopg_connection_string(environment.get("database_url"))
    token = environment.get("identity_token")
    port = int(environment.get("port") or "8080")
    if port < 1 or port > 65_535:
        raise SystemExit("CPK_DISCOVERY_PORT must be between 1 and 65535")
    connection_factory = lambda: psycopg.connect(database_url)
    install_discovery_schema(connection_factory)
    service = DiscoveryRegistryService(
        lambda: PostgresDiscoveryUnitOfWork(connection_factory)
    )

    def readiness() -> bool:
        connection = connection_factory()
        try:
            return connection.execute("SELECT 1").fetchone() == (1,)
        finally:
            connection.rollback()
            connection.close()

    app = create_service_discovery_app(
        service,
        identity_attestation_token=token,
        readiness=readiness,
    )
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="warning")


if __name__ == "__main__":
    main()
