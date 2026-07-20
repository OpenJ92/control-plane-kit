"""Live HTTP verification through canonical Postgres observed state."""

from __future__ import annotations

from datetime import datetime, timezone
import os
from uuid import uuid4

import psycopg

from control_plane_kit import (
    EndpointMaterial,
    EndpointScope,
    HttpCheck,
    LiteralEndpointMaterial,
    Protocol,
    VerificationCapability,
    VerificationCheckMaterial,
    VerificationInterpreterRegistry,
)
from control_plane_kit.adapters import (
    HttpVerificationInterpreter,
)
from control_plane_kit.adapters.probes import ProbeAddressPolicy
from control_plane_kit.read_services import InstanceReadService
from control_plane_kit.stores import (
    GraphVersionRecord,
    PostgresStoreBundle,
    PostgresUnitOfWork,
    WorkspaceRecord,
    install_schema,
)
from control_plane_kit.workflows import (
    ExecuteVerification,
    VerificationAuthority,
    VerificationCommandService,
    VerificationScope,
)


def main() -> None:
    database_url = os.environ["CPK_VERIFICATION_DATABASE_URL"]
    with psycopg.connect(database_url) as connection:
        install_schema(connection)
        stores = PostgresStoreBundle(connection)
        stores.workspace.create(
            WorkspaceRecord(
                "verification-live",
                "Verification Live",
                current_graph_id="verification-graph",
                desired_graph_id="verification-graph",
            )
        )
        stores.graph_topology.save(
            GraphVersionRecord(
                graph_id="verification-graph",
                workspace_id="verification-live",
                version=1,
                graph_descriptor={"name": "verification-live"},
                created_by="live-proof",
                created_at=_now(),
            )
        )

    factory = lambda: PostgresUnitOfWork(
        lambda: psycopg.connect(database_url)
    )
    material = VerificationCheckMaterial(
        "http-fixture",
        "verification-graph",
        HttpCheck(
            check_id="serves-http",
            provider_socket="http",
            path="/",
        ),
        EndpointMaterial(
            "http",
            Protocol.HTTP,
            EndpointScope.PRIVATE,
            LiteralEndpointMaterial("http://cpk-verification-live-target:8080"),
        ),
    )
    service = VerificationCommandService(
        factory,
        VerificationInterpreterRegistry(
            {
                VerificationCapability.HTTP: HttpVerificationInterpreter(
                    ProbeAddressPolicy(
                        runtime_private_authorities=frozenset(
                            ("http://cpk-verification-live-target:8080",)
                        )
                    )
                )
            }
        ),
        id_factory=lambda: str(uuid4()),
    )
    result = service.execute(
        ExecuteVerification(
            "verification-live",
            material,
            VerificationAuthority(
                "live-operator",
                frozenset((VerificationScope.EXECUTE,)),
            ),
        )
    )

    with psycopg.connect(database_url, autocommit=True) as connection:
        stores = PostgresStoreBundle(connection)
        projection = InstanceReadService(
            workspace_store=stores.workspace,
            graph_topology_store=stores.graph_topology,
            observed_state_store=stores.observed_state,
            clock=lambda: datetime.now(timezone.utc),
        ).observed_state("verification-live").descriptor()
        history = stores.observed_state.history(
            "verification-live", "verification:http-fixture:serves-http"
        )

    assert result.observation.status.value == "verified"
    assert [value.status.value for value in history] == ["starting", "verified"]
    assert projection["observations"][0]["status"] == "verified"
    assert "cpk-verification-live-target" not in str(projection)
    print(
        "Live verification passed: HTTP 200 -> durable starting/verified "
        "observations -> shared redacted projection."
    )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


if __name__ == "__main__":
    main()
