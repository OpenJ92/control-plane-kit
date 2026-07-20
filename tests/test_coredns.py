from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from ipaddress import IPv4Address, IPv6Address
import unittest

from control_plane_kit import (
    CapabilityName,
    DeploymentRecipe,
    DockerRuntime,
    Endpoint,
    EndpointScope,
    GraphDescriptorCodec,
    HostPublication,
    LiteralAddress,
    PackageServerProduct,
    PinnedGraphSet,
    Protocol,
    ReconcileNode,
    ReconcileNodeMaterial,
    compile_activity_plan,
    compile_recipe,
    diff_graphs,
    effect_request_for_activity,
    materialize_effect_request,
    validate_graph,
)
from control_plane_kit.domains.discovery import (
    DiscoveryIdentity,
    DiscoveryLease,
    DiscoveryRegistration,
    DiscoveryRegistrationMode,
    DiscoveryRegistrationRecord,
    DiscoveryRegistrationStatus,
)
from control_plane_kit.products.servers import CapabilityImplementation
from control_plane_kit.servers import (
    CoreDnsConfiguration,
    DnsARecord,
    DnsAaaaRecord,
    DnsName,
    coredns_block,
    default_coredns_configuration,
    package_server_contract,
    project_discovery_to_coredns,
    render_coredns_configuration,
)


NOW = datetime(2026, 7, 20, tzinfo=timezone.utc)


class CoreDnsTests(unittest.TestCase):
    def test_names_and_records_are_closed_typed_values(self) -> None:
        self.assertEqual(DnsName("Orders.CPK.Internal").value, "orders.cpk.internal.")
        with self.assertRaisesRegex(ValueError, "DNS name is invalid"):
            DnsName("bad_name.cpk.internal")
        with self.assertRaisesRegex(TypeError, "wrong address family"):
            DnsARecord(DnsName("orders.cpk.internal"), IPv6Address("::1"))
        with self.assertRaisesRegex(ValueError, "TTL"):
            DnsAaaaRecord(DnsName("orders.cpk.internal"), IPv6Address("::1"), 0)

    def test_configuration_rejects_out_of_zone_and_duplicate_records(self) -> None:
        zone = DnsName("cpk.internal")
        record = DnsARecord(DnsName("orders.cpk.internal"), IPv4Address("10.0.0.42"))
        with self.assertRaisesRegex(ValueError, "authoritative zone"):
            CoreDnsConfiguration(
                zone,
                (DnsARecord(DnsName("orders.example.org"), IPv4Address("10.0.0.42")),),
            )
        with self.assertRaisesRegex(ValueError, "authoritative zone"):
            CoreDnsConfiguration(
                zone,
                (DnsARecord(DnsName("evilcpk.internal"), IPv4Address("10.0.0.42")),),
            )
        with self.assertRaisesRegex(ValueError, "duplicates"):
            CoreDnsConfiguration(zone, (record, record))

    def test_registry_projection_is_deterministic_and_preserves_truth_boundary(self) -> None:
        registration = _record("orders", "blue", "http://10.0.0.42:8080")

        first = project_discovery_to_coredns(
            DnsName("cpk.internal"), (registration,), observed_at=NOW
        )
        second = project_discovery_to_coredns(
            DnsName("CPK.INTERNAL."), (registration,), observed_at=NOW
        )

        self.assertEqual(first, second)
        self.assertEqual(
            tuple(record.name.value for record in first.records),
            ("blue.orders.cpk.internal.", "orders.cpk.internal."),
        )
        self.assertTrue(all(record.address == IPv4Address("10.0.0.42") for record in first.records))
        self.assertEqual(registration.status, DiscoveryRegistrationStatus.ACTIVE)

    def test_configuration_canonicalizes_record_permutations(self) -> None:
        zone = DnsName("cpk.internal")
        first = DnsARecord(DnsName("api.cpk.internal"), IPv4Address("10.0.0.1"))
        second = DnsARecord(DnsName("orders.cpk.internal"), IPv4Address("10.0.0.2"))

        self.assertEqual(
            CoreDnsConfiguration(zone, (first, second)),
            CoreDnsConfiguration(zone, (second, first)),
        )

    def test_projection_rejects_stale_inactive_and_unprojectable_records(self) -> None:
        active = _record("orders", "blue", "http://10.0.0.42:8080")
        with self.assertRaisesRegex(ValueError, "expired"):
            project_discovery_to_coredns(
                DnsName("cpk.internal"),
                (replace(active, registration=replace(active.registration, lease=DiscoveryLease(NOW - timedelta(minutes=2), NOW))),),
                observed_at=NOW,
            )
        with self.assertRaisesRegex(ValueError, "inactive"):
            project_discovery_to_coredns(
                DnsName("cpk.internal"),
                (replace(active, status=DiscoveryRegistrationStatus.DEREGISTERED),),
                observed_at=NOW,
            )
        with self.assertRaisesRegex(ValueError, "future"):
            project_discovery_to_coredns(
                DnsName("cpk.internal"),
                (replace(active, updated_at=NOW + timedelta(seconds=1)),),
                observed_at=NOW,
            )
        with self.assertRaisesRegex(ValueError, "duplicates"):
            project_discovery_to_coredns(
                DnsName("cpk.internal"),
                (active, active),
                observed_at=NOW,
            )
        with self.assertRaisesRegex(ValueError, "literal IP"):
            project_discovery_to_coredns(
                DnsName("cpk.internal"),
                (_record("orders", "blue", "http://orders.internal:8080"),),
                observed_at=NOW,
            )
        with self.assertRaisesRegex(ValueError, "service identity"):
            project_discovery_to_coredns(
                DnsName("cpk.internal"),
                (_record("orders:admin", "blue", "http://10.0.0.42:8080"),),
                observed_at=NOW,
            )

    def test_strict_rendering_is_deterministic_read_only_and_secret_free(self) -> None:
        configuration = CoreDnsConfiguration(
            DnsName("cpk.internal"),
            (
                DnsARecord(DnsName("orders.cpk.internal"), IPv4Address("10.0.0.42")),
                DnsAaaaRecord(DnsName("api.cpk.internal"), IPv6Address("2001:db8::42")),
            ),
        )

        first = render_coredns_configuration(configuration)
        second = render_coredns_configuration(configuration)

        self.assertEqual(first, second)
        self.assertEqual(tuple(value.file_mode.value for value in first), ("0444", "0444"))
        self.assertIn("file /etc/coredns/zones/db.cpk cpk.internal.", first[0].content)
        self.assertIn("orders.cpk.internal. 60 IN A 10.0.0.42", first[1].content)
        self.assertIn("api.cpk.internal. 60 IN AAAA 2001:db8::42", first[1].content)

    def test_block_uses_official_pinned_image_and_exact_socket_products(self) -> None:
        block = coredns_block(
            host_publications={
                "dns-tcp": HostPublication.loopback_v4(),
                "dns-udp": HostPublication.loopback_v4(),
            }
        )

        self.assertIs(block.spec.product, PackageServerProduct.COREDNS)
        self.assertEqual(block.implementation.image, "coredns/coredns:1.14.6")
        self.assertNotIn(":latest", block.implementation.image)
        self.assertEqual(
            tuple((socket.name, socket.protocol) for socket in block.sockets.providers),
            (
                ("dns-tcp", Protocol.DNS_TCP),
                ("dns-udp", Protocol.DNS_UDP),
                ("health", Protocol.HTTP),
                ("ready", Protocol.HTTP),
            ),
        )
        self.assertEqual(block.implementation.ports["dns-tcp"], 53)
        self.assertEqual(block.implementation.ports["dns-udp"], 53)
        self.assertEqual(len(block.spec.verification.checks), 4)

    def test_artifact_change_is_an_explicit_graph_change_and_round_trips(self) -> None:
        current = _graph(default_coredns_configuration())
        desired = _graph(
            CoreDnsConfiguration(
                DnsName("cpk.internal"),
                (DnsARecord(DnsName("orders.cpk.internal"), IPv4Address("10.0.0.42")),),
            )
        )
        codec = GraphDescriptorCodec()
        descriptor = codec.encode(desired)

        self.assertEqual(codec.encode(codec.decode(descriptor)), descriptor)
        difference = diff_graphs(validate_graph(current), validate_graph(desired))
        plan = compile_activity_plan(difference)
        activity = next(
            value for value in plan.activities if isinstance(value.operation, ReconcileNode)
        )
        request = effect_request_for_activity(
            activity,
            run_id="coredns-run",
            attempt=1,
            idempotency_key="coredns:reconcile:1",
        )
        materialized = materialize_effect_request(
            request,
            activity,
            PinnedGraphSet("workspace", "plan", "current", "desired"),
            base_graph_id="current",
            base_graph=current,
            desired_graph_id="desired",
            desired_graph=desired,
        )

        self.assertTrue(difference.changes)
        self.assertIsInstance(materialized.material, ReconcileNodeMaterial)
        self.assertEqual(
            materialized.material.after.implementation.configuration_artifacts,
            desired.node("coredns").configuration_artifacts,
        )

    def test_catalogue_exposes_probe_and_runtime_restart_evidence(self) -> None:
        contract = package_server_contract(PackageServerProduct.COREDNS)

        self.assertEqual(
            tuple(value.capability for value in contract.capabilities),
            (CapabilityName.HEALTH_CHECKABLE, CapabilityName.RESTARTABLE),
        )
        self.assertIs(
            contract.capabilities[1].implementation,
            CapabilityImplementation.RUNTIME_LIFECYCLE,
        )


def _record(service: str, instance: str, url: str) -> DiscoveryRegistrationRecord:
    return DiscoveryRegistrationRecord(
        DiscoveryRegistration(
            DiscoveryIdentity("workspace", service, instance),
            Endpoint(LiteralAddress(url), Protocol.HTTP, EndpointScope.PRIVATE),
            DiscoveryRegistrationMode.CONTROL_PLANE,
            DiscoveryLease(NOW, NOW + timedelta(minutes=5)),
        ),
        DiscoveryRegistrationStatus.ACTIVE,
        1,
        NOW,
    )


def _graph(configuration: CoreDnsConfiguration):
    return compile_recipe(
        DeploymentRecipe(
            "coredns-test",
            DockerRuntime(children=(coredns_block(configuration=configuration),)),
        )
    )
