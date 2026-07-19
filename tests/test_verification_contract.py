from __future__ import annotations

from dataclasses import replace
import unittest

from control_plane_kit import (
    ApplicationBlock,
    BlockSockets,
    BlockSpec,
    BrokerRoundTripCheck,
    DeploymentRecipe,
    DnsRecordType,
    DnsResolveCheck,
    DockerRuntime,
    GraphDescriptorCodec,
    HttpCheck,
    ObjectStorageRoundTripCheck,
    PlanOnlyImplementation,
    PostgresQueryCheck,
    Protocol,
    ProviderSocket,
    RedisCheck,
    SmtpAcceptanceCheck,
    VerificationContract,
    VerificationContractError,
    VerificationPolicy,
    expected_protocols,
    compile_recipe,
    verification_check_from_descriptor,
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
    def test_complete_closed_language_round_trips_exactly(self) -> None:
        contract = complete_contract()

        descriptor = contract.descriptor()
        restored = VerificationContract.from_descriptor(descriptor)

        self.assertEqual(restored, contract)
        self.assertEqual(restored.descriptor(), descriptor)
        self.assertEqual(
            restored.checks[0].expected_statuses,
            (200, 204),
        )

    def test_each_check_variant_has_an_explicit_protocol_set(self) -> None:
        checks = complete_contract().checks

        self.assertEqual(expected_protocols(checks[0]), frozenset((Protocol.HTTP,)))
        self.assertEqual(
            expected_protocols(checks[1]),
            frozenset((Protocol.DNS_TCP, Protocol.DNS_UDP)),
        )
        self.assertEqual(
            expected_protocols(checks[2]), frozenset((Protocol.POSTGRES,))
        )
        self.assertEqual(expected_protocols(checks[3]), frozenset((Protocol.REDIS,)))
        self.assertEqual(
            expected_protocols(checks[4]),
            frozenset((Protocol.NATS, Protocol.AMQP, Protocol.KAFKA)),
        )
        self.assertEqual(expected_protocols(checks[5]), frozenset((Protocol.S3,)))
        self.assertEqual(expected_protocols(checks[6]), frozenset((Protocol.SMTP,)))

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
            implementation=PlanOnlyImplementation(
                "hello", output_urls={"public": "http://hello:8080"}
            ),
            sockets=BlockSockets(
                providers=(ProviderSocket("public", Protocol.HTTP),)
            ),
        )
        graph = compile_recipe(
            DeploymentRecipe("verification", DockerRuntime(children=(block,)))
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
            with self.subTest(descriptor=descriptor):
                with self.assertRaises(VerificationContractError):
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


if __name__ == "__main__":
    unittest.main()
