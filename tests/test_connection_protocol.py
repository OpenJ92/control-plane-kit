from __future__ import annotations

import unittest

from control_plane_kit import ApplicationProtocol, Protocol, Transport


class ConnectionProtocolTests(unittest.TestCase):
    def test_existing_protocols_preserve_exact_semantics(self) -> None:
        self.assertEqual(
            (Protocol.HTTP.transport, Protocol.HTTP.application, Protocol.HTTP.value),
            (Transport.TCP, ApplicationProtocol.HTTP, "http"),
        )
        self.assertEqual(
            (
                Protocol.POSTGRES.transport,
                Protocol.POSTGRES.application,
                Protocol.POSTGRES.value,
            ),
            (Transport.TCP, ApplicationProtocol.POSTGRES, "postgres"),
        )
        self.assertEqual(
            (Protocol.TCP.transport, Protocol.TCP.application, Protocol.TCP.value),
            (Transport.TCP, ApplicationProtocol.RAW, "tcp"),
        )

    def test_every_application_protocol_has_an_explicit_transport_set(self) -> None:
        self.assertEqual(
            {
                application: Protocol.allowed_transports(application)
                for application in ApplicationProtocol
            },
            {
                ApplicationProtocol.RAW: frozenset((Transport.TCP, Transport.UDP)),
                ApplicationProtocol.HTTP: frozenset((Transport.TCP,)),
                ApplicationProtocol.POSTGRES: frozenset((Transport.TCP,)),
                ApplicationProtocol.DNS: frozenset((Transport.TCP, Transport.UDP)),
                ApplicationProtocol.REDIS: frozenset((Transport.TCP,)),
                ApplicationProtocol.SMTP: frozenset((Transport.TCP,)),
                ApplicationProtocol.OTLP_HTTP: frozenset((Transport.TCP,)),
                ApplicationProtocol.OTLP_GRPC: frozenset((Transport.TCP,)),
                ApplicationProtocol.NATS: frozenset((Transport.TCP,)),
                ApplicationProtocol.AMQP: frozenset((Transport.TCP,)),
                ApplicationProtocol.KAFKA: frozenset((Transport.TCP,)),
                ApplicationProtocol.S3: frozenset((Transport.TCP,)),
            },
        )

    def test_dns_and_raw_protocols_represent_both_transports(self) -> None:
        self.assertEqual(Protocol.DNS_TCP.value, "dns+tcp")
        self.assertEqual(Protocol.DNS_UDP.value, "dns+udp")
        self.assertEqual(Protocol.UDP.value, "udp")
        self.assertNotEqual(Protocol.DNS_TCP, Protocol.DNS_UDP)

    def test_invalid_transport_application_combinations_fail_at_construction(self) -> None:
        with self.assertRaisesRegex(ValueError, "http does not support udp"):
            Protocol(Transport.UDP, ApplicationProtocol.HTTP)
        with self.assertRaisesRegex(ValueError, "postgres does not support udp"):
            Protocol(Transport.UDP, ApplicationProtocol.POSTGRES)

    def test_compatibility_requires_both_transport_and_application_semantics(self) -> None:
        self.assertTrue(Protocol.HTTP.compatible_with(Protocol.HTTP))
        self.assertFalse(Protocol.HTTP.compatible_with(Protocol.TCP))
        self.assertFalse(Protocol.DNS_TCP.compatible_with(Protocol.DNS_UDP))

    def test_compact_names_parse_only_to_closed_canonical_values(self) -> None:
        values = (
            Protocol.TCP,
            Protocol.UDP,
            Protocol.HTTP,
            Protocol.POSTGRES,
            Protocol.DNS_TCP,
            Protocol.DNS_UDP,
            Protocol.REDIS,
            Protocol.SMTP,
            Protocol.OTLP_HTTP,
            Protocol.OTLP_GRPC,
            Protocol.NATS,
            Protocol.AMQP,
            Protocol.KAFKA,
            Protocol.S3,
        )

        for protocol in values:
            with self.subTest(protocol=protocol.value):
                self.assertIs(Protocol.parse(protocol.value), protocol)

        with self.assertRaisesRegex(ValueError, "unknown connection protocol"):
            Protocol.parse("custom")

    def test_every_protocol_round_trips_through_structured_string_projection(self) -> None:
        values = (
            Protocol.TCP,
            Protocol.UDP,
            Protocol.HTTP,
            Protocol.POSTGRES,
            Protocol.DNS_TCP,
            Protocol.DNS_UDP,
            Protocol.REDIS,
            Protocol.SMTP,
            Protocol.OTLP_HTTP,
            Protocol.OTLP_GRPC,
            Protocol.NATS,
            Protocol.AMQP,
            Protocol.KAFKA,
            Protocol.S3,
        )

        for protocol in values:
            with self.subTest(protocol=protocol.value):
                descriptor = protocol.descriptor()
                self.assertEqual(set(descriptor), {"transport", "application"})
                self.assertTrue(all(isinstance(value, str) for value in descriptor.values()))
                self.assertIs(Protocol.from_descriptor(descriptor), protocol)

    def test_protocol_descriptor_unknown_extra_and_invalid_values_fail_closed(self) -> None:
        invalid = (
            {"transport": "tcp"},
            {"transport": "tcp", "application": "http", "future": "value"},
            {"transport": "future", "application": "http"},
            {"transport": "udp", "application": "http"},
            {"transport": 1, "application": "http"},
        )

        for descriptor in invalid:
            with self.subTest(descriptor=descriptor):
                with self.assertRaises(ValueError):
                    Protocol.from_descriptor(descriptor)


if __name__ == "__main__":
    unittest.main()
