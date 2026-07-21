from __future__ import annotations

from dataclasses import dataclass, replace
import unittest

from control_plane_kit_core.algebra import (
    ApplicationBlock,
    BlockSockets,
    BlockSpec,
    DeploymentTopology,
    DockerRuntime,
    ProviderSocket,
    ProxyBlock,
)
from control_plane_kit_core.capabilities import (
    CACHE_PURGEABLE,
    CACHE_STATE_READABLE,
    CIRCUIT_RESETTABLE,
    CIRCUIT_STATE_READABLE,
    DISCOVERY_MUTABLE,
    DISCOVERY_READABLE,
    DRAINABLE,
    FAULT_MUTABLE,
    FAULT_STATE_READABLE,
    HEALTH_CHECKABLE,
    LOAD_MUTABLE,
    LOAD_STATE_READABLE,
    LOG_READABLE,
    METRICS_READABLE,
    OBSERVER_MUTABLE,
    RESTARTABLE,
    SWITCHABLE,
    TARGET_MUTABLE,
    TRAFFIC_EVIDENCE_READABLE,
    CapabilityName,
    capability_named,
)
from control_plane_kit_core.control_routes import ControlRouteSetName
from control_plane_kit_core.lifecycle import OWNED_EPHEMERAL
from control_plane_kit_core.topology import Endpoint, GraphDescriptorCodec, compile_topology
from control_plane_kit_core.topology.graph import LiteralAddress
from control_plane_kit_core.types import Protocol
from control_plane_kit_core.verification import (
    BrokerRoundTripCheck,
    DnsRecordType,
    DnsResolveCheck,
    HttpCheck,
    HttpVerificationEvidence,
    ObjectStorageRoundTripCheck,
    PostgresQueryCheck,
    RedisCheck,
    RedisVerificationEvidence,
    SmtpAcceptanceCheck,
    VerificationCapability,
    VerificationCompleted,
    VerificationContract,
    VerificationContractError,
    VerificationIdentity,
    VerificationOutcome,
    VerificationPolicy,
    VerificationUnsupported,
    expected_protocols,
    verification_capability,
    verification_check_from_descriptor,
)


@dataclass(frozen=True)
class MaterializedBlock:
    kind: str
    endpoints: dict[str, Endpoint]
    public_environment: tuple[object, ...] = ()
    metadata: dict[str, object] | None = None
    lifecycle: object = OWNED_EPHEMERAL
    configuration_artifacts: tuple[object, ...] = ()
    secret_deliveries: tuple[object, ...] = ()

    def __post_init__(self) -> None:
        if self.metadata is None:
            object.__setattr__(self, "metadata", {})


@dataclass(frozen=True)
class VerificationImplementation:
    kind: str = "verification-fixture"
    output_urls: dict[str, str] | None = None

    def materialize(
        self,
        block_id: str,
        sockets: BlockSockets,
        runtime: object,
    ) -> MaterializedBlock:
        return MaterializedBlock(
            self.kind,
            {
                name: Endpoint(LiteralAddress(address), sockets.provider(name).protocol)
                for name, address in (self.output_urls or {}).items()
            },
        )


def complete_contract() -> VerificationContract:
    policy = VerificationPolicy(
        timeout_seconds=3.5,
        maximum_attempts=2,
        maximum_evidence_bytes=2048,
    )
    return VerificationContract(
        (
            HttpCheck(
                check_id="http-orders",
                provider_socket="public",
                path="/internal/tests/orders",
                expected_statuses=(204, 200, 200),
                policy=policy,
            ),
            DnsResolveCheck(
                check_id="dns-service",
                provider_socket="dns",
                query_name="service.internal",
                record_type=DnsRecordType.AAAA,
                policy=policy,
            ),
            PostgresQueryCheck(
                check_id="postgres-connectivity",
                provider_socket="postgres",
                policy=policy,
            ),
            RedisCheck(
                check_id="redis-ping",
                provider_socket="redis",
                policy=policy,
            ),
            BrokerRoundTripCheck(
                check_id="broker-round-trip",
                provider_socket="events",
                channel="cpk.verification",
                policy=policy,
            ),
            ObjectStorageRoundTripCheck(
                check_id="object-storage-round-trip",
                provider_socket="objects",
                bucket="verification-bucket",
                policy=policy,
            ),
            SmtpAcceptanceCheck(
                check_id="smtp-acceptance",
                provider_socket="smtp",
                recipient_reference="verification-recipient",
                policy=policy,
            ),
        )
    )


class VerificationContractTests(unittest.TestCase):
    def test_identity_accepts_bounded_canonical_durable_graph_ids(self) -> None:
        identity = VerificationIdentity(
            "api",
            "workspace:graph:version:1",
            "semantic-http",
        )

        self.assertEqual(
            identity.descriptor(),
            {
                "node_id": "api",
                "graph_id": "workspace:graph:version:1",
                "check_id": "semantic-http",
            },
        )
        for invalid in ("", "   ", "graph\nidentity", "g" * 257):
            with self.subTest(invalid=invalid), self.assertRaisesRegex(
                VerificationContractError,
                "verification graph identity is invalid",
            ):
                VerificationIdentity("api", invalid, "semantic-http")

    def test_complete_closed_language_round_trips_exactly(self) -> None:
        contract = complete_contract()

        descriptor = contract.descriptor()
        restored = VerificationContract.from_descriptor(descriptor)

        self.assertEqual(restored, contract)
        self.assertEqual(restored.descriptor(), descriptor)
        self.assertEqual(restored.checks[0].expected_statuses, (200, 204))

    def test_each_check_variant_has_an_explicit_protocol_set(self) -> None:
        checks = complete_contract().checks

        self.assertEqual(expected_protocols(checks[0]), frozenset((Protocol.HTTP,)))
        self.assertEqual(
            expected_protocols(checks[1]),
            frozenset((Protocol.DNS_TCP, Protocol.DNS_UDP)),
        )
        self.assertEqual(expected_protocols(checks[2]), frozenset((Protocol.POSTGRES,)))
        self.assertEqual(expected_protocols(checks[3]), frozenset((Protocol.REDIS,)))
        self.assertEqual(
            expected_protocols(checks[4]),
            frozenset((Protocol.NATS, Protocol.AMQP, Protocol.KAFKA)),
        )
        self.assertEqual(expected_protocols(checks[5]), frozenset((Protocol.S3,)))
        self.assertEqual(expected_protocols(checks[6]), frozenset((Protocol.SMTP,)))
        self.assertEqual(
            tuple(verification_capability(check) for check in checks),
            (
                VerificationCapability.HTTP,
                VerificationCapability.DNS,
                VerificationCapability.POSTGRES,
                VerificationCapability.REDIS,
                VerificationCapability.BROKER,
                VerificationCapability.OBJECT_STORAGE,
                VerificationCapability.SMTP,
            ),
        )

    def test_result_values_are_closed_bounded_and_deterministic(self) -> None:
        identity = VerificationIdentity("api", "graph-1", "semantic-http")
        observation = VerificationCompleted(
            identity,
            VerificationCapability.HTTP,
            VerificationOutcome.PASSED,
            2,
            HttpVerificationEvidence(200, 128),
        )

        self.assertEqual(
            observation.descriptor(),
            {
                "type": "verification-completed",
                "identity": {
                    "node_id": "api",
                    "graph_id": "graph-1",
                    "check_id": "semantic-http",
                },
                "capability": "http",
                "outcome": "passed",
                "attempts": 2,
                "evidence": {
                    "kind": "http",
                    "status_code": 200,
                    "response_bytes": 128,
                },
            },
        )
        self.assertEqual(
            RedisVerificationEvidence(7).descriptor(),
            {"kind": "redis", "response_bytes": 7},
        )
        self.assertEqual(
            VerificationUnsupported(
                identity,
                VerificationCapability.HTTP,
            ).descriptor(),
            {
                "type": "verification-unsupported",
                "identity": identity.descriptor(),
                "capability": "http",
            },
        )
        with self.assertRaises(VerificationContractError):
            HttpVerificationEvidence(200, 65_537)
        with self.assertRaises(VerificationContractError):
            VerificationCompleted(
                identity,
                VerificationCapability.HTTP,
                VerificationOutcome.PASSED,
                11,
            )
        with self.assertRaises(TypeError):
            VerificationUnsupported(identity, "http")  # type: ignore[arg-type]

    def test_block_contract_survives_the_authoritative_graph_codec(self) -> None:
        contract = VerificationContract(
            (
                HttpCheck(
                    check_id="hello-response",
                    provider_socket="public",
                    path="/hello",
                ),
            )
        )
        block = ApplicationBlock(
            spec=BlockSpec("hello", verification=contract),
            implementation=VerificationImplementation(
                "hello",
                {"public": "http://hello:8080"},
            ),
            sockets=BlockSockets(providers=(ProviderSocket("public", Protocol.HTTP),)),
        )
        graph = compile_topology(
            DeploymentTopology("verification", DockerRuntime(children=(block,)))
        )

        descriptor = GraphDescriptorCodec().encode(graph)
        restored = GraphDescriptorCodec().decode(descriptor)

        self.assertEqual(restored.node("hello").block_spec.verification, contract)
        self.assertEqual(
            descriptor["nodes"]["hello"]["block_spec"]["verification"],
            contract.descriptor(),
        )

    def test_block_spec_rejects_an_untyped_contract(self) -> None:
        with self.assertRaises(TypeError):
            BlockSpec("hello", verification={})  # type: ignore[arg-type]

    def test_http_target_is_a_socket_and_relative_path_not_an_arbitrary_url(self) -> None:
        with self.assertRaisesRegex(VerificationContractError, "relative absolute path"):
            HttpCheck(
                check_id="unsafe-http",
                provider_socket="public",
                path="https://attacker.example/check",
            )

        with self.assertRaisesRegex(VerificationContractError, "relative absolute path"):
            HttpCheck(
                check_id="unsafe-query",
                provider_socket="public",
                path="/check?token=value",
            )

    def test_postgres_language_has_no_arbitrary_query_field(self) -> None:
        descriptor = PostgresQueryCheck(
            check_id="postgres-connectivity",
            provider_socket="postgres",
        ).descriptor()
        descriptor["query"] = "DROP TABLE activity_runs"

        with self.assertRaisesRegex(VerificationContractError, "unknown or missing"):
            verification_check_from_descriptor(descriptor)

    def test_unknown_variants_fields_and_closed_values_fail_closed(self) -> None:
        valid = complete_contract().checks[0].descriptor()
        invalid = (
            {**valid, "kind": "future-check"},
            {**valid, "future": "value"},
            {key: value for key, value in valid.items() if key != "provider_socket"},
        )

        for descriptor in invalid:
            with self.subTest(descriptor=descriptor), self.assertRaises(
                VerificationContractError,
            ):
                verification_check_from_descriptor(descriptor)

        dns = complete_contract().checks[1].descriptor()
        dns["record_type"] = "mx"
        with self.assertRaises(VerificationContractError):
            verification_check_from_descriptor(dns)

    def test_policy_and_evidence_are_bounded(self) -> None:
        with self.assertRaises(VerificationContractError):
            VerificationPolicy(timeout_seconds=61)
        with self.assertRaises(VerificationContractError):
            VerificationPolicy(maximum_attempts=11)
        with self.assertRaises(VerificationContractError):
            VerificationPolicy(maximum_evidence_bytes=65_537)

    def test_contract_requires_unique_closed_checks(self) -> None:
        check = complete_contract().checks[0]
        with self.assertRaisesRegex(VerificationContractError, "identities"):
            VerificationContract((check, replace(check, path="/other")))

        with self.assertRaises(TypeError):
            VerificationContract((object(),))  # type: ignore[arg-type]


class CapabilityTests(unittest.TestCase):
    def test_capabilities_reference_expected_route_sets(self) -> None:
        self.assertEqual(HEALTH_CHECKABLE.route_set, ControlRouteSetName.COMMON_STATUS)
        self.assertEqual(LOG_READABLE.route_set, ControlRouteSetName.LOGS)
        self.assertEqual(TARGET_MUTABLE.route_set, ControlRouteSetName.TARGETS)
        self.assertEqual(SWITCHABLE.route_set, ControlRouteSetName.TARGETS)
        self.assertEqual(DRAINABLE.route_set, ControlRouteSetName.TARGETS)
        self.assertEqual(OBSERVER_MUTABLE.route_set, ControlRouteSetName.OBSERVERS)
        self.assertEqual(METRICS_READABLE.route_set, ControlRouteSetName.METRICS)
        self.assertEqual(CIRCUIT_STATE_READABLE.route_set, ControlRouteSetName.CIRCUIT)
        self.assertEqual(CIRCUIT_RESETTABLE.route_set, ControlRouteSetName.CIRCUIT)
        self.assertEqual(
            TRAFFIC_EVIDENCE_READABLE.route_set,
            ControlRouteSetName.TRAFFIC_EVIDENCE,
        )
        self.assertEqual(FAULT_STATE_READABLE.route_set, ControlRouteSetName.FAULTS)
        self.assertEqual(FAULT_MUTABLE.route_set, ControlRouteSetName.FAULTS)
        self.assertEqual(CACHE_STATE_READABLE.route_set, ControlRouteSetName.CACHE)
        self.assertEqual(CACHE_PURGEABLE.route_set, ControlRouteSetName.CACHE)
        self.assertEqual(LOAD_STATE_READABLE.route_set, ControlRouteSetName.LOADS)
        self.assertEqual(LOAD_MUTABLE.route_set, ControlRouteSetName.LOADS)
        self.assertEqual(DISCOVERY_READABLE.route_set, ControlRouteSetName.DISCOVERY)
        self.assertEqual(DISCOVERY_MUTABLE.route_set, ControlRouteSetName.DISCOVERY)

    def test_lifecycle_does_not_claim_a_route_yet(self) -> None:
        self.assertIsNone(RESTARTABLE.route_set)

    def test_capability_descriptor_is_json_friendly(self) -> None:
        self.assertEqual(
            SWITCHABLE.as_descriptor(),
            {
                "name": "switchable",
                "label": "Switch",
                "description": "Node can switch one active downstream target.",
                "route_set": "targets",
            },
        )

    def test_capability_named_accepts_string_or_enum(self) -> None:
        self.assertIs(
            capability_named("switchable"),
            capability_named(CapabilityName.SWITCHABLE),
        )

    def test_unknown_capability_fails_loudly(self) -> None:
        with self.assertRaises(KeyError):
            capability_named("teleportable")

    def test_compiled_metadata_contains_route_backed_capability_descriptors(self) -> None:
        router = ProxyBlock(
            spec=BlockSpec(
                role_id="api-router",
                display_name="API Router",
                capabilities=(
                    CapabilityName.HEALTH_CHECKABLE,
                    CapabilityName.TARGET_MUTABLE,
                    CapabilityName.SWITCHABLE,
                ),
            ),
            implementation=VerificationImplementation("plan-router"),
            sockets=BlockSockets(providers=(ProviderSocket("internal", Protocol.HTTP),)),
        )
        graph = compile_topology(
            DeploymentTopology(
                "capability-demo",
                DockerRuntime(children=(router,)),
            )
        )

        self.assertEqual(
            graph.node("api-router").metadata["capabilities"],
            [
                {
                    "name": "health-checkable",
                    "label": "Health",
                    "description": "Node exposes health and status state through the control protocol.",
                    "route_set": "common-status",
                },
                {
                    "name": "target-mutable",
                    "label": "Targets",
                    "description": "Node can register or replace downstream targets.",
                    "route_set": "targets",
                },
                {
                    "name": "switchable",
                    "label": "Switch",
                    "description": "Node can switch one active downstream target.",
                    "route_set": "targets",
                },
            ],
        )


if __name__ == "__main__":
    unittest.main()
