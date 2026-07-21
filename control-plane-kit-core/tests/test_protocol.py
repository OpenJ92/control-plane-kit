from __future__ import annotations

import unittest

from control_plane_kit_core.types import ApplicationProtocol, Protocol, Transport


class ConnectionProtocolTests(unittest.TestCase):
    def test_existing_protocols_preserve_exact_transport_and_application_semantics(self) -> None:
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
                ApplicationProtocol.MCP_STREAMABLE_HTTP: frozenset((Transport.TCP,)),
            },
        )

    def test_invalid_transport_application_combinations_fail_at_construction(self) -> None:
        with self.assertRaisesRegex(ValueError, "http does not support udp"):
            Protocol(Transport.UDP, ApplicationProtocol.HTTP)
        with self.assertRaisesRegex(ValueError, "postgres does not support udp"):
            Protocol(Transport.UDP, ApplicationProtocol.POSTGRES)

    def test_protocol_descriptor_is_closed_and_round_trips(self) -> None:
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
            Protocol.MCP_STREAMABLE_HTTP,
        )

        for protocol in values:
            with self.subTest(protocol=protocol.value):
                self.assertIs(Protocol.parse(protocol.value), protocol)
                descriptor = protocol.descriptor()
                self.assertEqual(set(descriptor), {"transport", "application"})
                self.assertIs(Protocol.from_descriptor(descriptor), protocol)

        with self.assertRaisesRegex(ValueError, "unknown connection protocol"):
            Protocol.parse("custom")
        with self.assertRaises(ValueError):
            Protocol.from_descriptor(
                {"transport": "udp", "application": "postgres"}
            )


if __name__ == "__main__":
    unittest.main()
