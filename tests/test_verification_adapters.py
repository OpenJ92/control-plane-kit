from dataclasses import dataclass, field
import unittest

import httpx

from control_plane_kit import (
    EndpointMaterial,
    EndpointScope,
    HttpCheck,
    HttpVerificationEvidence,
    HttpVerificationInterpreter,
    LiteralEndpointMaterial,
    Protocol,
    RedisCheck,
    RedisVerificationEvidence,
    RedisVerificationInterpreter,
    VerificationCapability,
    VerificationCheckMaterial,
    VerificationOutcome,
    VerificationPolicy,
)
from control_plane_kit.adapters.probes import ProbeAddressPolicy


def http_material(*, maximum_bytes: int = 64) -> VerificationCheckMaterial:
    return VerificationCheckMaterial(
        "api",
        "graph-1",
        HttpCheck(
            check_id="semantic-http",
            provider_socket="internal",
            path="/verify",
            policy=VerificationPolicy(maximum_evidence_bytes=maximum_bytes),
        ),
        EndpointMaterial(
            "internal",
            Protocol.HTTP,
            EndpointScope.PRIVATE,
            LiteralEndpointMaterial("http://api:8080"),
        ),
    )


def redis_material(*, attempts: int = 1) -> VerificationCheckMaterial:
    return VerificationCheckMaterial(
        "cache",
        "graph-1",
        RedisCheck(
            check_id="redis-ping",
            provider_socket="redis",
            policy=VerificationPolicy(maximum_attempts=attempts),
        ),
        EndpointMaterial(
            "redis",
            Protocol.REDIS,
            EndpointScope.PRIVATE,
            LiteralEndpointMaterial("redis://cache:6379"),
        ),
    )


@dataclass
class ScriptedRedisTransport:
    responses: list[bytes | Exception]
    calls: list[tuple[str, int, float, int]] = field(default_factory=list)

    def ping(self, host, port, *, timeout_seconds, maximum_response_bytes):
        self.calls.append((host, port, timeout_seconds, maximum_response_bytes))
        result = self.responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


class VerificationAdapterTests(unittest.TestCase):
    def test_http_check_is_redirect_free_bounded_and_status_driven(self) -> None:
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(200, content=b"semantic-ok")

        interpreter = HttpVerificationInterpreter(
            ProbeAddressPolicy(
                runtime_private_authorities=frozenset(("http://api:8080",))
            ),
            transport=httpx.MockTransport(handler),
        )

        result = interpreter.execute(http_material())

        self.assertIs(result.outcome, VerificationOutcome.PASSED)
        self.assertIs(result.capability, VerificationCapability.HTTP)
        self.assertEqual(result.evidence, HttpVerificationEvidence(200, 11))
        self.assertEqual(requests[0].url.path, "/verify")

        redirect = HttpVerificationInterpreter(
            interpreter.policy,
            transport=httpx.MockTransport(
                lambda request: httpx.Response(302, headers={"Location": "http://other/"})
            ),
        ).execute(http_material())
        oversized = HttpVerificationInterpreter(
            interpreter.policy,
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, content=b"x" * 65)
            ),
        ).execute(http_material(maximum_bytes=64))
        self.assertIs(redirect.outcome, VerificationOutcome.MALFORMED)
        self.assertIs(oversized.outcome, VerificationOutcome.MALFORMED)

    def test_http_address_outside_policy_is_rejected_without_attempt(self) -> None:
        calls = 0

        def handler(request):
            nonlocal calls
            calls += 1
            return httpx.Response(200)

        result = HttpVerificationInterpreter(
            ProbeAddressPolicy(),
            transport=httpx.MockTransport(handler),
        ).execute(http_material())

        self.assertIs(result.outcome, VerificationOutcome.REJECTED)
        self.assertEqual(calls, 0)

    def test_redis_ping_retries_bounded_exchange_and_retains_no_payload(self) -> None:
        transport = ScriptedRedisTransport([OSError(), b"+PONG\r\n"])
        interpreter = RedisVerificationInterpreter(
            ProbeAddressPolicy(
                runtime_private_authorities=frozenset(("redis://cache:6379",))
            ),
            transport=transport,
        )

        result = interpreter.execute(redis_material(attempts=2))

        self.assertIs(result.outcome, VerificationOutcome.PASSED)
        self.assertEqual(result.attempts, 2)
        self.assertEqual(result.evidence, RedisVerificationEvidence(7))
        self.assertEqual(len(transport.calls), 2)
        self.assertEqual(transport.calls[0][0:2], ("cache", 6379))


if __name__ == "__main__":
    unittest.main()
