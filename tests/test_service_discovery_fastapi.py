from __future__ import annotations

from datetime import datetime, timedelta, timezone
import os
import unittest

import psycopg
from fastapi.testclient import TestClient

from control_plane_kit import (
    CapabilityName,
    ControlVariableError,
    DeregisterDiscoveryInstance,
    DiscoveryAuthority,
    DiscoveryIdentity,
    DiscoveryLease,
    DiscoveryRegistration,
    DiscoveryRegistrationMode,
    DiscoveryScope,
    Endpoint,
    EndpointScope,
    ExpireDiscoveryLeases,
    HeartbeatDiscoveryInstance,
    LiteralAddress,
    Protocol,
    RegisterDiscoveryInstance,
    discovery_command_descriptor,
)
from control_plane_kit.discovery_registry import (
    DiscoveryRegistryService,
    PostgresDiscoveryUnitOfWork,
    install_discovery_schema,
)
from control_plane_kit.servers import (
    service_discovery_block,
)
from control_plane_kit.discovery_server.main import (
    ServiceDiscoveryEnvironment,
    psycopg_connection_string,
)
from control_plane_kit.implementations import DockerImageImplementation
from control_plane_kit.discovery_server import (
    MAX_DISCOVERY_RESPONSE_BYTES,
    create_service_discovery_app,
)
from tests.postgres_case import PostgresStoreTestCase


NOW = datetime(2026, 7, 19, 14, tzinfo=timezone.utc)
TOKEN = "discovery-attestation"


class ServiceDiscoveryFastAPITests(PostgresStoreTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.database_url = os.environ["CPK_TEST_DATABASE_URL"]
        install_discovery_schema(lambda: psycopg.connect(self.database_url))
        self.connection.execute("DELETE FROM cpk_discovery_commands")
        self.connection.execute("DELETE FROM cpk_discovery_registrations")
        factory = lambda: PostgresDiscoveryUnitOfWork(
            lambda: psycopg.connect(self.database_url)
        )
        self.service = DiscoveryRegistryService(factory, clock=lambda: NOW)
        self.client = TestClient(
            create_service_discovery_app(
                self.service,
                identity_attestation_token=TOKEN,
                readiness=lambda: True,
            )
        )

    def test_health_is_distinct_from_semantic_readiness(self) -> None:
        client = TestClient(
            create_service_discovery_app(
                self.service,
                identity_attestation_token=TOKEN,
                readiness=lambda: False,
            )
        )

        self.assertEqual(client.get("/health").json(), {"status": "healthy"})
        self.assertEqual(client.get("/health/ready").status_code, 503)

    def test_process_boundary_vends_direct_psycopg_dsn_from_graph_identity(
        self,
    ) -> None:
        graph_url = (
            "postgresql+psycopg://registry:password@runtime-db:5432/discovery"
        )
        environment = ServiceDiscoveryEnvironment.from_mapping(
            {
                "DISCOVERY_DATABASE_URL": graph_url,
                "CPK_DISCOVERY_IDENTITY_TOKEN": "attestation",
            }
        )

        self.assertEqual(environment.get("database_url"), graph_url)
        self.assertEqual(
            psycopg_connection_string(environment.get("database_url")),
            "postgresql://registry:password@runtime-db:5432/discovery",
        )
        for direct in (
            "postgresql://registry@runtime-db:5432/discovery",
            "postgres://registry@runtime-db:5432/discovery",
        ):
            with self.subTest(direct=direct):
                self.assertEqual(psycopg_connection_string(direct), direct)

        for invalid in (
            "mysql://registry@runtime-db/discovery",
            "postgresql+asyncpg://registry@runtime-db/discovery",
        ):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ControlVariableError):
                    ServiceDiscoveryEnvironment.from_mapping(
                        {
                            "DISCOVERY_DATABASE_URL": invalid,
                            "CPK_DISCOVERY_IDENTITY_TOKEN": "attestation",
                        }
                    )

    def test_register_and_resolve_use_scoped_authenticated_service_commands(self) -> None:
        descriptor = discovery_command_descriptor(
            RegisterDiscoveryInstance("register-a", _registration())
        )

        denied = self.client.post("/__deploy/discovery/registrations", json=descriptor)
        self.assertEqual(denied.status_code, 401)
        self.assertEqual(_row_count(self.connection, "cpk_discovery_commands"), 0)

        registered = self.client.post(
            "/__deploy/discovery/registrations",
            json=descriptor,
            headers=_headers("discovery:manage"),
        )
        self.assertEqual(registered.status_code, 200)
        self.assertEqual(registered.json()["result"]["outcome"], "registered")

        resolved = self.client.get(
            "/__deploy/discovery/services/orders",
            params={
                "command_id": "resolve-a",
                "workspace_id": "workspace-a",
                "observed_at": NOW.isoformat(),
                "limit": 10,
            },
            headers=_headers("discovery:resolve", actor="reader"),
        )
        self.assertEqual(resolved.status_code, 200)
        self.assertEqual(resolved.json()["result"]["affected_count"], 1)
        self.assertEqual(
            resolved.json()["result"]["registrations"][0]["registration"]["identity"],
            {
                "workspace_id": "workspace-a",
                "service_id": "orders",
                "instance_id": "orders-a",
            },
        )

    def test_under_scoped_mutation_fails_before_registry_write(self) -> None:
        response = self.client.post(
            "/__deploy/discovery/registrations",
            json=discovery_command_descriptor(
                RegisterDiscoveryInstance("register-a", _registration())
            ),
            headers=_headers("discovery:resolve", actor="reader"),
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(_row_count(self.connection, "cpk_discovery_commands"), 0)
        self.assertNotIn(TOKEN, response.text)

    def test_heartbeat_and_deregister_enforce_path_identity(self) -> None:
        self._register()
        expected = _registration().lease.expires_at
        heartbeat = HeartbeatDiscoveryInstance(
            "heartbeat-a",
            DiscoveryIdentity("workspace-a", "orders", "orders-a"),
            expected,
            DiscoveryLease(NOW + timedelta(seconds=10), NOW + timedelta(seconds=60)),
        )
        mismatch = self.client.post(
            "/__deploy/discovery/registrations/orders-b/heartbeat",
            json=discovery_command_descriptor(heartbeat),
            headers=_headers("discovery:manage"),
        )
        self.assertEqual(mismatch.status_code, 409)

        renewed = self.client.post(
            "/__deploy/discovery/registrations/orders-a/heartbeat",
            json=discovery_command_descriptor(heartbeat),
            headers=_headers("discovery:manage"),
        )
        self.assertEqual(renewed.status_code, 200)
        replacement_expiry = heartbeat.replacement_lease.expires_at
        deregistered = self.client.post(
            "/__deploy/discovery/registrations/orders-a/deregister",
            json=discovery_command_descriptor(
                DeregisterDiscoveryInstance(
                    "deregister-a", heartbeat.identity, replacement_expiry
                )
            ),
            headers=_headers("discovery:manage"),
        )
        self.assertEqual(deregistered.status_code, 200)
        self.assertEqual(deregistered.json()["result"]["outcome"], "deregistered")

    def test_body_bounds_and_closed_descriptors_fail_before_service(self) -> None:
        malformed = self.client.post(
            "/__deploy/discovery/registrations",
            content=b"not-json",
            headers=_headers("discovery:manage"),
        )
        oversized = self.client.post(
            "/__deploy/discovery/registrations",
            content=b"x" * 16_385,
            headers=_headers("discovery:manage"),
        )

        self.assertEqual(malformed.status_code, 400)
        self.assertEqual(oversized.status_code, 413)
        self.assertEqual(_row_count(self.connection, "cpk_discovery_commands"), 0)

    def test_expiry_route_requires_management_scope(self) -> None:
        self._register()
        command = ExpireDiscoveryLeases(
            "expire-a",
            "workspace-a",
            _registration().lease.expires_at,
            10,
        )

        denied = self.client.post(
            "/__deploy/discovery/expiry",
            json=discovery_command_descriptor(command),
            headers=_headers("discovery:resolve", actor="reader"),
        )
        expired = self.client.post(
            "/__deploy/discovery/expiry",
            json=discovery_command_descriptor(command),
            headers=_headers("discovery:manage"),
        )

        self.assertEqual(denied.status_code, 403)
        self.assertEqual(expired.status_code, 200)
        self.assertEqual(expired.json()["result"]["outcome"], "expired")
        self.assertEqual(expired.json()["result"]["affected_count"], 1)

    def test_self_registration_is_bound_to_service_and_instance_identity(self) -> None:
        registration = _registration(mode=DiscoveryRegistrationMode.SELF)
        descriptor = discovery_command_descriptor(
            RegisterDiscoveryInstance("register-self", registration)
        )

        wrong_service = self.client.post(
            "/__deploy/discovery/registrations",
            json=descriptor,
            headers=_headers(
                "discovery:register-self",
                actor="orders-a",
                service="payments",
                instance="orders-a",
            ),
        )
        accepted = self.client.post(
            "/__deploy/discovery/registrations",
            json=descriptor,
            headers=_headers(
                "discovery:register-self",
                actor="orders-a",
                service="orders",
                instance="orders-a",
            ),
        )

        self.assertEqual(wrong_service.status_code, 403)
        self.assertEqual(accepted.status_code, 200)
        self.assertEqual(_row_count(self.connection, "cpk_discovery_registrations"), 1)

    def test_rejected_endpoint_and_identity_material_are_redacted(self) -> None:
        descriptor = discovery_command_descriptor(
            RegisterDiscoveryInstance("register-secret", _registration())
        )
        endpoint = descriptor["registration"]["endpoint"]
        endpoint["address"]["value"] = "http://operator:must-not-leak@orders-a:8080"

        response = self.client.post(
            "/__deploy/discovery/registrations",
            json=descriptor,
            headers=_headers("discovery:manage"),
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), {"detail": "invalid discovery command"})
        self.assertNotIn("must-not-leak", response.text)
        self.assertNotIn(TOKEN, response.text)
        self.assertEqual(_row_count(self.connection, "cpk_discovery_commands"), 0)

    def test_maximum_resolution_page_has_a_bounded_response(self) -> None:
        manager = DiscoveryAuthority(
            "manager",
            "workspace-a",
            frozenset((DiscoveryScope.MANAGE, DiscoveryScope.RESOLVE)),
        )
        suffix = "x" * 1_700
        for index in range(100):
            instance_id = f"orders-{index:03d}"
            self.service.execute(
                RegisterDiscoveryInstance(
                    f"register-{index:03d}",
                    _registration(
                        instance_id=instance_id,
                        address=f"http://{instance_id}:8080/{suffix}",
                    ),
                ),
                manager,
            )

        response = self.client.get(
            "/__deploy/discovery/services/orders",
            params={
                "command_id": "resolve-max-page",
                "workspace_id": "workspace-a",
                "observed_at": NOW.isoformat(),
                "limit": 100,
            },
            headers=_headers("discovery:resolve", actor="reader"),
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["result"]["affected_count"], 100)
        self.assertLessEqual(len(response.content), MAX_DISCOVERY_RESPONSE_BYTES)

    def test_block_now_advertises_only_real_docker_capabilities(self) -> None:
        block = service_discovery_block(
            "registry",
            image="control-plane-kit:test",
            identity_secret_reference="secret://registry/attestation",
        )

        self.assertIsInstance(block.implementation, DockerImageImplementation)
        self.assertEqual(
            block.spec.capabilities,
            (
                CapabilityName.HEALTH_CHECKABLE,
                CapabilityName.DISCOVERY_READABLE,
                CapabilityName.DISCOVERY_MUTABLE,
            ),
        )
        self.assertEqual(
            block.implementation.command,
            ("python", "-m", "control_plane_kit.discovery_server.main"),
        )
        self.assertEqual(
            block.implementation.secret_deliveries[0].environment_name,
            "CPK_DISCOVERY_IDENTITY_TOKEN",
        )
        self.assertNotIn("discovery-attestation", repr(block))

    def _register(self) -> None:
        response = self.client.post(
            "/__deploy/discovery/registrations",
            json=discovery_command_descriptor(
                RegisterDiscoveryInstance("register-a", _registration())
            ),
            headers=_headers("discovery:manage"),
        )
        self.assertEqual(response.status_code, 200)


def _registration(
    *,
    instance_id: str = "orders-a",
    address: str | None = None,
    mode: DiscoveryRegistrationMode = DiscoveryRegistrationMode.CONTROL_PLANE,
) -> DiscoveryRegistration:
    return DiscoveryRegistration(
        DiscoveryIdentity("workspace-a", "orders", instance_id),
        Endpoint(
            LiteralAddress(address or f"http://{instance_id}:8080"),
            Protocol.HTTP,
            EndpointScope.PRIVATE,
        ),
        mode,
        DiscoveryLease(NOW, NOW + timedelta(seconds=30)),
    )


def _headers(
    scope: str,
    *,
    actor: str = "manager",
    service: str | None = None,
    instance: str | None = None,
) -> dict[str, str]:
    headers = {
        "x-cpk-identity-attestation": TOKEN,
        "x-cpk-authenticated-subject": actor,
        "x-cpk-authenticated-workspace": "workspace-a",
        "x-cpk-discovery-scopes": scope,
    }
    if service is not None:
        headers["x-cpk-discovery-service"] = service
    if instance is not None:
        headers["x-cpk-discovery-instance"] = instance
    return headers


def _row_count(connection, table: str) -> int:
    if table not in {"cpk_discovery_commands", "cpk_discovery_registrations"}:
        raise ValueError("unknown discovery table")
    return connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]


if __name__ == "__main__":
    unittest.main()
