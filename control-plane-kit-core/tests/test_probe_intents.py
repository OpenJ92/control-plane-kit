from __future__ import annotations

import unittest

from control_plane_kit_core.probe_intents import (
    ApplicationHealthProbeIntent,
    EndpointContext,
    EndpointDeclaration,
    HttpResponseExpectation,
    LiteralEndpointMaterial,
    ProbeConstructionCode,
    ProbeConstructionFailure,
    ProbeKind,
    ProbeObservation,
    ProbeOutcome,
    ProbePolicy,
    ProbeSubject,
    ReadinessProbeIntent,
    RuntimeEndpointObservation,
    SecretEndpointMaterial,
    TimeoutPolicy,
    TransportProbeIntent,
    application_health_probe,
    process_probe,
    protocol_endpoint_schemes,
    transport_probe,
)
from control_plane_kit_core.types import ApplicationProtocol, Protocol, Transport


class ProbeIntentTests(unittest.TestCase):
    def test_process_transport_health_and_readiness_are_distinct_objects(self) -> None:
        subject = _subject()
        endpoint = _endpoint()
        policy = ProbePolicy()

        process = process_probe(subject, "graph-a", policy)
        transport = transport_probe(subject, endpoint, policy)
        health = application_health_probe(subject, endpoint, policy)
        readiness = ReadinessProbeIntent(subject.subject_id, "graph-a")

        self.assertIs(process.kind, ProbeKind.PROCESS)
        self.assertIsInstance(transport, TransportProbeIntent)
        self.assertIs(transport.kind, ProbeKind.TRANSPORT)
        self.assertIsInstance(health, ApplicationHealthProbeIntent)
        self.assertIs(health.kind, ProbeKind.APPLICATION_HEALTH)
        self.assertIs(readiness.kind, ProbeKind.READINESS)
        self.assertNotEqual(process, readiness)

    def test_endpoint_contexts_preserve_private_host_and_public_meaning(self) -> None:
        contexts = tuple(
            RuntimeEndpointObservation(
                "api",
                "internal",
                "graph-a",
                Protocol.HTTP,
                context,
                LiteralEndpointMaterial(address),
            )
            for context, address in (
                (EndpointContext.RUNTIME_PRIVATE, "http://docker-api:8000"),
                (EndpointContext.HOST_LOCAL, "http://127.0.0.1:49152"),
                (EndpointContext.PUBLIC, "https://api.example.test:443"),
            )
        )

        self.assertEqual(
            [value.descriptor()["context"] for value in contexts],
            ["runtime-private", "host-local", "public"],
        )

    def test_application_health_is_derived_only_from_matching_http_material(self) -> None:
        intent = application_health_probe(_subject(), _endpoint(), ProbePolicy())

        self.assertIsInstance(intent, ApplicationHealthProbeIntent)
        self.assertEqual(intent.health_path, "/health")
        self.assertEqual(intent.endpoint.socket_name, "internal")

    def test_missing_health_and_mismatched_protocol_are_typed_construction_failures(self) -> None:
        missing = application_health_probe(
            _subject(health_path=None),
            _endpoint(),
            ProbePolicy(),
        )
        mismatched = transport_probe(
            _subject(),
            RuntimeEndpointObservation(
                "api",
                "internal",
                "graph-a",
                Protocol.TCP,
                EndpointContext.HOST_LOCAL,
                LiteralEndpointMaterial("tcp://127.0.0.1:49152"),
            ),
            ProbePolicy(),
        )

        self.assertEqual(
            missing,
            ProbeConstructionFailure(
                ProbeConstructionCode.MISSING_HEALTH_CONTRACT,
                "api",
            ),
        )
        self.assertEqual(
            mismatched,
            ProbeConstructionFailure(
                ProbeConstructionCode.INCOMPATIBLE_PROTOCOL,
                "api",
            ),
        )

    def test_literal_targets_reject_credentials_query_fragments_and_protocol_lies(self) -> None:
        unsafe = (
            "http://user:password@127.0.0.1:8000",
            "http://127.0.0.1:8000?token=secret",
            "http://127.0.0.1:8000#fragment",
        )
        for address in unsafe:
            with self.subTest(address=address), self.assertRaisesRegex(
                ValueError,
                "safe authority",
            ) as raised:
                RuntimeEndpointObservation(
                    "api",
                    "internal",
                    "graph-a",
                    Protocol.HTTP,
                    EndpointContext.HOST_LOCAL,
                    LiteralEndpointMaterial(address),
                )
            self.assertNotIn("secret", str(raised.exception))
            self.assertNotIn("password", str(raised.exception))

        with self.assertRaisesRegex(ValueError, "safe authority"):
            RuntimeEndpointObservation(
                "api",
                "internal",
                "graph-a",
                Protocol.HTTP,
                EndpointContext.HOST_LOCAL,
                LiteralEndpointMaterial("tcp://127.0.0.1:8000"),
            )

    def test_every_protocol_has_explicit_safe_endpoint_schemes(self) -> None:
        protocols = tuple(
            Protocol(transport, application)
            for application in ApplicationProtocol
            for transport in Transport
            if transport in Protocol.allowed_transports(application)
        )

        for protocol in protocols:
            with self.subTest(protocol=protocol):
                schemes = protocol_endpoint_schemes(protocol)
                self.assertTrue(schemes)
                for scheme in schemes:
                    RuntimeEndpointObservation(
                        "service",
                        "internal",
                        "graph-a",
                        protocol,
                        EndpointContext.RUNTIME_PRIVATE,
                        LiteralEndpointMaterial(f"{scheme}://service:8000"),
                    )

    def test_endpoint_scheme_cannot_lie_about_semantic_protocol(self) -> None:
        invalid = (
            (Protocol.DNS_UDP, "tcp://dns:53"),
            (Protocol.REDIS, "postgres://database:5432"),
            (Protocol.SMTP, "http://mail:25"),
            (Protocol.OTLP_GRPC, "https://collector:4317"),
        )

        for protocol, address in invalid:
            with self.subTest(protocol=protocol, address=address), self.assertRaisesRegex(
                ValueError,
                "safe authority",
            ):
                RuntimeEndpointObservation(
                    "service",
                    "internal",
                    "graph-a",
                    protocol,
                    EndpointContext.RUNTIME_PRIVATE,
                    LiteralEndpointMaterial(address),
                )

    def test_secret_endpoint_descriptors_retain_only_the_reference(self) -> None:
        endpoint = RuntimeEndpointObservation(
            "api",
            "internal",
            "graph-a",
            Protocol.HTTP,
            EndpointContext.PUBLIC,
            SecretEndpointMaterial("secret://workspace/public-api"),
        )

        self.assertEqual(
            endpoint.descriptor()["address"],
            {
                "kind": "secret-reference",
                "reference_id": "secret://workspace/public-api",
            },
        )

    def test_probe_policy_is_finite_and_deterministic(self) -> None:
        policy = ProbePolicy(
            TimeoutPolicy(15, 2),
            maximum_attempts=5,
            maximum_response_bytes=4096,
            http=HttpResponseExpectation((204, 200, 204)),
        )

        self.assertEqual(policy.http.status_codes, (200, 204))
        self.assertEqual(policy.descriptor()["maximum_attempts"], 5)
        with self.assertRaises(ValueError):
            ProbePolicy(maximum_attempts=0)
        with self.assertRaises(ValueError):
            ProbePolicy(maximum_response_bytes=65_537)

    def test_every_failure_outcome_remains_distinct_from_readiness(self) -> None:
        self.assertEqual(len(set(ProbeOutcome)), len(ProbeOutcome))
        self.assertIsNot(ProbeOutcome.PROCESS_RUNNING, ProbeOutcome.READY)
        self.assertIsNot(ProbeOutcome.REACHABLE, ProbeOutcome.HEALTHY)
        self.assertIsNot(ProbeOutcome.REFUSED, ProbeOutcome.TIMED_OUT)
        self.assertIsNot(ProbeOutcome.UNHEALTHY, ProbeOutcome.MALFORMED)

    def test_probe_intent_descriptors_are_deterministic_and_redacted(self) -> None:
        subject = _subject()
        endpoint = RuntimeEndpointObservation(
            "api",
            "internal",
            "graph-a",
            Protocol.HTTP,
            EndpointContext.PUBLIC,
            SecretEndpointMaterial("secret://workspace/public-api"),
        )
        policy = ProbePolicy(maximum_attempts=3)

        process = process_probe(subject, "graph-a", policy)
        transport = transport_probe(subject, endpoint, policy)
        health = application_health_probe(subject, endpoint, policy)
        readiness = ReadinessProbeIntent(subject.subject_id, "graph-a")

        self.assertEqual(process.descriptor()["kind"], "process")
        self.assertEqual(transport.descriptor()["endpoint"], endpoint.descriptor())
        self.assertEqual(health.descriptor()["health_path"], "/health")
        self.assertEqual(
            readiness.descriptor()["required"],
            ["process", "transport", "application-health"],
        )
        self.assertNotIn("token", repr(transport.descriptor()).lower())
        self.assertEqual(
            transport.descriptor()["endpoint"]["address"],
            {
                "kind": "secret-reference",
                "reference_id": "secret://workspace/public-api",
            },
        )

    def test_probe_observations_accept_only_layer_coherent_outcomes(self) -> None:
        observations = (
            ProbeObservation(
                "api",
                "graph-a",
                ProbeKind.PROCESS,
                ProbeOutcome.PROCESS_RUNNING,
            ),
            ProbeObservation(
                "api",
                "graph-a",
                ProbeKind.TRANSPORT,
                ProbeOutcome.REACHABLE,
                endpoint_context=EndpointContext.RUNTIME_PRIVATE,
            ),
            ProbeObservation(
                "api",
                "graph-a",
                ProbeKind.APPLICATION_HEALTH,
                ProbeOutcome.HEALTHY,
                endpoint_context=EndpointContext.HOST_LOCAL,
            ),
            ProbeObservation(
                "api",
                "graph-a",
                ProbeKind.READINESS,
                ProbeOutcome.READY,
            ),
        )

        self.assertEqual(
            [value.descriptor()["outcome"] for value in observations],
            ["process-running", "reachable", "healthy", "ready"],
        )

    def test_probe_observations_reject_cross_layer_claims(self) -> None:
        invalid = (
            (ProbeKind.PROCESS, ProbeOutcome.HEALTHY, None),
            (ProbeKind.TRANSPORT, ProbeOutcome.READY, EndpointContext.HOST_LOCAL),
            (ProbeKind.READINESS, ProbeOutcome.REACHABLE, None),
        )
        for kind, outcome, context in invalid:
            with self.subTest(kind=kind, outcome=outcome), self.assertRaisesRegex(
                ValueError,
                "not a valid",
            ):
                ProbeObservation(
                    "api",
                    "graph-a",
                    kind,
                    outcome,
                    endpoint_context=context,
                )

        with self.assertRaisesRegex(ValueError, "requires endpoint context"):
            ProbeObservation(
                "api",
                "graph-a",
                ProbeKind.TRANSPORT,
                ProbeOutcome.REACHABLE,
            )
        with self.assertRaisesRegex(ValueError, "cannot claim endpoint context"):
            ProbeObservation(
                "api",
                "graph-a",
                ProbeKind.PROCESS,
                ProbeOutcome.PROCESS_RUNNING,
                endpoint_context=EndpointContext.HOST_LOCAL,
            )


def _subject(*, health_path: str | None = "/health") -> ProbeSubject:
    return ProbeSubject(
        "api",
        (
            EndpointDeclaration(
                "internal",
                Protocol.HTTP,
            ),
        ),
        health_path,
    )


def _endpoint() -> RuntimeEndpointObservation:
    return RuntimeEndpointObservation(
        "api",
        "internal",
        "graph-a",
        Protocol.HTTP,
        EndpointContext.HOST_LOCAL,
        LiteralEndpointMaterial("http://127.0.0.1:49152"),
    )


if __name__ == "__main__":
    unittest.main()
