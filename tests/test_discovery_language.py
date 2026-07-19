from __future__ import annotations

from datetime import datetime, timedelta, timezone
import unittest

from control_plane_kit import (
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
    GraphDescriptorCodec,
    HeartbeatDiscoveryInstance,
    LiteralAddress,
    PackageServerProduct,
    Protocol,
    RegisterDiscoveryInstance,
    ResolveDiscoveryService,
    SecretReferenceAddress,
    compile_recipe,
    discovery_command_descriptor,
    discovery_command_from_descriptor,
    discovery_authority_from_descriptor,
    discovery_registration_from_descriptor,
    package_server_contract,
    service_discovery_block,
)
from control_plane_kit.algebra import DeploymentRecipe, DockerRuntime


NOW = datetime(2026, 7, 19, 12, tzinfo=timezone.utc)


class DiscoveryLanguageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.identity = DiscoveryIdentity("workspace-a", "orders", "orders-a")
        self.lease = DiscoveryLease(NOW, NOW + timedelta(seconds=30))
        self.registration = DiscoveryRegistration(
            self.identity,
            Endpoint(
                LiteralAddress("http://orders-a:8080"),
                Protocol.HTTP,
                EndpointScope.PRIVATE,
            ),
            DiscoveryRegistrationMode.CONTROL_PLANE,
            self.lease,
        )

    def test_closed_command_language_round_trips_exhaustively(self) -> None:
        commands = (
            RegisterDiscoveryInstance("register-a", self.registration),
            HeartbeatDiscoveryInstance(
                "heartbeat-a",
                self.identity,
                self.lease.expires_at,
                DiscoveryLease(NOW + timedelta(seconds=20), NOW + timedelta(seconds=60)),
            ),
            DeregisterDiscoveryInstance(
                "deregister-a",
                self.identity,
                self.lease.expires_at,
            ),
            ResolveDiscoveryService(
                "resolve-a",
                "workspace-a",
                "orders",
                NOW,
                25,
            ),
            ExpireDiscoveryLeases("expire-a", "workspace-a", NOW, 250),
        )

        for command in commands:
            with self.subTest(command=type(command).__name__):
                descriptor = discovery_command_descriptor(command)
                self.assertEqual(discovery_command_from_descriptor(descriptor), command)

    def test_unknown_variants_and_fields_fail_closed(self) -> None:
        descriptor = discovery_command_descriptor(
            RegisterDiscoveryInstance("register-a", self.registration)
        )
        descriptor["future"] = True
        with self.assertRaisesRegex(ValueError, "requires exactly"):
            discovery_command_from_descriptor(descriptor)

        descriptor = {"variant": "future", "command_id": "future-a"}
        with self.assertRaisesRegex(ValueError, "unknown discovery command variant"):
            discovery_command_from_descriptor(descriptor)

    def test_registry_endpoint_is_typed_literal_and_not_process_local(self) -> None:
        with self.assertRaisesRegex(ValueError, "literal endpoint"):
            DiscoveryRegistration(
                self.identity,
                Endpoint(
                    SecretReferenceAddress("secret://orders/address"),
                    Protocol.HTTP,
                ),
                DiscoveryRegistrationMode.SELF,
                self.lease,
            )
        with self.assertRaisesRegex(ValueError, "process-local"):
            DiscoveryRegistration(
                self.identity,
                Endpoint(
                    LiteralAddress("http://127.0.0.1:8080"),
                    Protocol.HTTP,
                    EndpointScope.LOCAL,
                ),
                DiscoveryRegistrationMode.SELF,
                self.lease,
            )

    def test_authority_keeps_workspace_and_self_identity_explicit(self) -> None:
        authority = DiscoveryAuthority(
            "orders-a",
            "workspace-a",
            frozenset((DiscoveryScope.REGISTER_SELF,)),
            subject_instance_id="orders-a",
        )

        self.assertEqual(
            authority.descriptor(),
            {
                "actor_id": "orders-a",
                "workspace_id": "workspace-a",
                "scopes": ["discovery:register-self"],
                "subject_instance_id": "orders-a",
            },
        )
        self.assertEqual(
            discovery_authority_from_descriptor(authority.descriptor()),
            authority,
        )
        self.assertEqual(
            discovery_registration_from_descriptor(self.registration.descriptor()),
            self.registration,
        )

    def test_block_is_graph_data_with_explicit_database_requirement(self) -> None:
        block = service_discovery_block("registry")
        graph = compile_recipe(
            DeploymentRecipe("discovery", DockerRuntime(children=(block,)))
        )
        descriptor = GraphDescriptorCodec().encode(graph)
        restored = GraphDescriptorCodec().decode(descriptor)

        self.assertIs(
            restored.node("registry").block_spec.product,
            PackageServerProduct.SERVICE_DISCOVERY,
        )
        self.assertEqual(block.sockets.requirement("database").protocol, Protocol.POSTGRES)
        self.assertEqual(
            block.sockets.requirement("database").env_bindings,
            ("DISCOVERY_DATABASE_URL",),
        )
        self.assertEqual(block.sockets.provider("internal").protocol, Protocol.HTTP)
        self.assertEqual(block.spec.capabilities, ())
        self.assertEqual(
            package_server_contract(PackageServerProduct.SERVICE_DISCOVERY).capabilities,
            (),
        )

    def test_lease_and_identifiers_are_bounded_typed_values(self) -> None:
        with self.assertRaisesRegex(ValueError, "expire after"):
            DiscoveryLease(NOW, NOW)
        with self.assertRaisesRegex(ValueError, "bounded discovery identifier"):
            DiscoveryIdentity("workspace a", "orders", "orders-a")
        with self.assertRaisesRegex(ValueError, "between 1 and 100"):
            ResolveDiscoveryService("resolve-a", "workspace-a", "orders", NOW, 101)


if __name__ == "__main__":
    unittest.main()
