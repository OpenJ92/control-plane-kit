"""Process composition root for one durable webhook delivery server."""

from __future__ import annotations

import time
from uuid import uuid4

import psycopg
import uvicorn

from control_plane_kit.contracts import (
    EnvironmentContract,
    PostgresVariable,
    SecretVariable,
    TextVariable,
)
from control_plane_kit.core.secrets import (
    LocalDevelopmentSecretResolver,
    SecretProviderAuthority,
    SecretReference,
)
from control_plane_kit.servers.webhook_delivery import parse_webhook_address_policy
from control_plane_kit.operations.webhook import (
    PostgresWebhookUnitOfWork,
    WebhookDeliveryService,
    install_webhook_schema,
)
from control_plane_kit.webhook.app import create_webhook_delivery_app
from control_plane_kit.webhook.http import (
    HttpWebhookDelivery,
    SystemWebhookPublicAddressResolver,
)


class WebhookDeliveryEnvironment(EnvironmentContract):
    database_url = PostgresVariable(
        "database_url", metadata={"env": "WEBHOOK_DATABASE_URL"}
    )
    endpoint_policy = TextVariable(
        "endpoint_policy", metadata={"env": "CPK_WEBHOOK_ENDPOINT_POLICY"}
    )
    identity_token = SecretVariable(
        "identity_token", metadata={"env": "CPK_WEBHOOK_IDENTITY_TOKEN"}
    )
    signing_reference = TextVariable(
        "signing_reference", metadata={"env": "CPK_WEBHOOK_SIGNING_REFERENCE"}
    )
    signing_secret = SecretVariable(
        "signing_secret", metadata={"env": "CPK_WEBHOOK_SIGNING_SECRET"}
    )
    port = TextVariable(
        "port", required=False, metadata={"env": "CPK_WEBHOOK_PORT"}
    )


def psycopg_connection_string(value: str) -> str:
    """Interpret graph Postgres URL identity for the direct psycopg driver."""

    prefix = "postgresql+psycopg://"
    return "postgresql://" + value[len(prefix) :] if value.startswith(prefix) else value


def create_app_from_environment():
    """Build the server from explicit process bootstrap material."""

    environment = WebhookDeliveryEnvironment.from_process()
    database_url = psycopg_connection_string(environment.get("database_url"))
    identity_token = environment.get("identity_token")
    signing_reference = SecretReference(environment.get("signing_reference"))
    signing_secret = environment.get("signing_secret")
    endpoint_policy = parse_webhook_address_policy(environment.get("endpoint_policy"))
    resolver = LocalDevelopmentSecretResolver(
        SecretProviderAuthority(
            signing_reference.provider_id,
            (signing_reference.path,),
        ),
        {signing_reference.reference_id: signing_secret},
    )
    connection_factory = lambda: psycopg.connect(database_url)
    with _await_database(connection_factory) as connection:
        install_webhook_schema(connection)
    service = WebhookDeliveryService(
        lambda: PostgresWebhookUnitOfWork(connection_factory),
        HttpWebhookDelivery(
            resolver,
            endpoint_policy,
            public_resolver=SystemWebhookPublicAddressResolver(),
        ),
        id_factory=lambda: uuid4().hex,
    )

    def readiness() -> bool:
        connection = connection_factory()
        try:
            return connection.execute("SELECT 1").fetchone() == (1,)
        finally:
            connection.rollback()
            connection.close()

    return create_webhook_delivery_app(
        service,
        identity_attestation_token=identity_token,
        readiness=readiness,
    )


def _await_database(connection_factory, *, attempts: int = 30, delay: float = 1.0):
    """Wait boundedly for the graph-ordered Postgres process to become ready."""

    for attempt in range(1, attempts + 1):
        try:
            return connection_factory()
        except psycopg.OperationalError:
            if attempt == attempts:
                raise
            time.sleep(delay)
    raise RuntimeError("unreachable webhook database wait state")


def main() -> None:
    environment = WebhookDeliveryEnvironment.from_process()
    port = int(environment.get("port") or "8080")
    if port < 1 or port > 65_535:
        raise SystemExit("CPK_WEBHOOK_PORT must be between 1 and 65535")
    uvicorn.run(
        create_app_from_environment(),
        host="0.0.0.0",
        port=port,
        log_level="warning",
    )


if __name__ == "__main__":
    main()
