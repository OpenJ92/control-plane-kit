from __future__ import annotations

from dataclasses import dataclass
import json
import unittest

import httpx

from control_plane_kit.adapters.control_http import (
    BlockControlHttpInterpreter,
    ControlAddressPolicy,
    ControlAddressSource,
    ControlAuthority,
    ControlEndpointObservation,
    ControlHttpLimits,
    ControlHttpReadError,
    CredentialReference,
    RuntimeEndpointProvenance,
    SecretValue,
    StaticControlAuthorityProvider,
)
from control_plane_kit.core.capabilities import CapabilityName, capability_named
from control_plane_kit.effects import (
    EffectCapability,
    EffectIdentity,
    EffectRequest,
    EffectSucceeded,
    EffectUnsupported,
    EndpointMaterial,
    LiteralEndpointMaterial,
    MaterializedEffectRequest,
    PinnedGraphSet,
    SocketConnectionMaterial,
)
from control_plane_kit.core.planning import (
    ActivityId,
    AddSocketConnection,
    RemoveSocketConnection,
    SocketConnectionTarget,
    SwitchSocketConnection,
)
from control_plane_kit.core.types import EndpointScope, Protocol
from control_plane_kit.core.secrets import SecretProviderAuthority, SecretProviderId, SecretResolved


@dataclass
class Resolver:
    value: str = "test-control-token"

    authority = SecretProviderAuthority(SecretProviderId("test"))

    def resolve(self, reference: CredentialReference) -> SecretResolved:
        return SecretResolved(reference, SecretValue(self.value))


class ControlHttpClientTests(unittest.TestCase):
    def test_socket_operation_matrix_discovers_capability_before_mutation(self) -> None:
        cases = (
            (AddSocketConnection(SocketConnectionTarget("edge")), CapabilityName.TARGET_MUTABLE, "/__deploy/targets"),
            (SwitchSocketConnection(SocketConnectionTarget("edge")), CapabilityName.SWITCHABLE, "/__deploy/active-target"),
            (RemoveSocketConnection(SocketConnectionTarget("edge")), CapabilityName.DRAINABLE, "/__deploy/drain-target"),
        )
        for action, capability, expected_path in cases:
            with self.subTest(action=type(action).__name__):
                seen: list[httpx.Request] = []

                def handler(request: httpx.Request) -> httpx.Response:
                    seen.append(request)
                    self.assertEqual(request.headers["authorization"], "Bearer test-control-token")
                    if request.url.path == "/__deploy/capabilities":
                        return _json({"block_id": "router", "capabilities": [capability_named(capability).as_descriptor()]})
                    if request.method == "GET" and request.url.path == "/__deploy/targets":
                        return _json({"block_id": "router", "active_target": "", "targets": {}})
                    if request.url.path == "/__deploy/targets":
                        return _json({"block_id": "router", "active_target": "", "targets": json.loads(request.content)})
                    if request.url.path == "/__deploy/active-target":
                        return _json({"block_id": "router", "active_target": "api"})
                    return _json({"block_id": "router", "draining_target": "api"})

                result = self._interpreter(handler).execute(_request(action))

                self.assertIsInstance(result, EffectSucceeded)
                self.assertEqual(seen[0].url.path, "/__deploy/capabilities")
                self.assertEqual(seen[-1].url.path, expected_path)
                self.assertEqual(seen[-1].headers["idempotency-key"], "run-a:edge:1")

    def test_missing_remote_capability_is_unsupported_without_mutation(self) -> None:
        paths: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            paths.append(request.url.path)
            return _json({"block_id": "router", "capabilities": []})

        request = _request(SwitchSocketConnection(SocketConnectionTarget("edge")))
        result = self._interpreter(handler).execute(request)

        self.assertEqual(result, EffectUnsupported(request.identity, EffectCapability.SOCKET_RECONCILIATION))
        self.assertEqual(paths, ["/__deploy/capabilities"])

    def test_redirect_malformed_and_oversized_responses_fail_without_body_echo(self) -> None:
        cases = (
            httpx.Response(302, headers={"location": "http://secret.internal"}),
            httpx.Response(200, headers={"content-type": "text/plain"}, content=b"secret-response"),
            httpx.Response(200, headers={"content-type": "application/json"}, content=b"{" + b"x" * 1024),
        )
        for response in cases:
            with self.subTest(status=response.status_code):
                result = self._interpreter(
                    lambda _request, response=response: response,
                    limits=ControlHttpLimits(response_bytes=128),
                ).execute(_request(SwitchSocketConnection(SocketConnectionTarget("edge"))))
                self.assertEqual(result.failure.code.startswith("control."), True)
                self.assertNotIn("secret", str(result.failure))

    def test_mutation_without_a_trustworthy_result_is_explicitly_uncertain(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/__deploy/capabilities":
                return _json({
                    "block_id": "router",
                    "capabilities": [capability_named(CapabilityName.SWITCHABLE).as_descriptor()],
                })
            raise httpx.ReadTimeout("do not persist this transport text")

        result = self._interpreter(handler).execute(
            _request(SwitchSocketConnection(SocketConnectionTarget("edge")))
        )

        self.assertEqual(result.failure.category.value, "uncertain")
        self.assertEqual(result.failure.code, "control.timeout")
        self.assertNotIn("transport text", str(result.failure))

    def test_status_and_logs_are_bounded_typed_operator_reads(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/__deploy/status":
                return _json({"block_id": "router", "status": "ready", "target_count": 2})
            return _json({"block_id": "router", "lines": ["one", "two"]})

        interpreter = self._interpreter(handler)
        status = interpreter.read_status("router", request_id="read-a", idempotency_key="read-a")
        logs = interpreter.read_logs("router", request_id="read-b", idempotency_key="read-b")

        self.assertEqual(status.descriptor(), {"block_id": "router", "status": "ready"})
        self.assertEqual(logs.descriptor()["lines"], ["one", "two"])

        oversized = self._interpreter(
            lambda _request: _json({"block_id": "router", "lines": ["one", "two"]}),
            limits=ControlHttpLimits(log_lines=1),
        )
        with self.assertRaises(ControlHttpReadError):
            oversized.read_logs("router", request_id="read", idempotency_key="read")

    def _interpreter(self, handler, *, limits: ControlHttpLimits | None = None):
        observation = ControlEndpointObservation(
            "router",
            EndpointMaterial(
                "control",
                Protocol.HTTP,
                EndpointScope.LOCAL,
                LiteralEndpointMaterial("http://127.0.0.1:8010"),
            ),
            RuntimeEndpointProvenance(ControlAddressSource.HOST_LOCAL, "local"),
        )
        return BlockControlHttpInterpreter(
            StaticControlAuthorityProvider(
                {"router": ControlAuthority(observation, CredentialReference("secret://test/router-token"))}
            ),
            Resolver(),
            ControlAddressPolicy(allow_host_local=True),
            limits=limits,
            transport=httpx.MockTransport(handler),
        )


def _request(action) -> MaterializedEffectRequest:
    abstract = EffectRequest(
        EffectIdentity("run-a", ActivityId("edge"), 1, "run-a:edge:1"),
        action,
    )
    material = SocketConnectionMaterial(
        "edge",
        Protocol.HTTP,
        "api",
        "http",
        EndpointMaterial(
            "http",
            Protocol.HTTP,
            EndpointScope.PRIVATE,
            LiteralEndpointMaterial("http://api:8000"),
        ),
        "router",
        "target",
        (),
    )
    return MaterializedEffectRequest(
        abstract,
        PinnedGraphSet("workspace", "plan", "base", "desired"),
        "desired",
        material,
    )


def _json(payload: dict[str, object]) -> httpx.Response:
    return httpx.Response(200, headers={"content-type": "application/json"}, json=payload)


if __name__ == "__main__":
    unittest.main()
