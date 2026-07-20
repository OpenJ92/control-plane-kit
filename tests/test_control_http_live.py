from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import socket
from threading import Thread
import time
import unittest

from fastapi import FastAPI
from fastapi.responses import JSONResponse, PlainTextResponse, RedirectResponse, Response
import httpx
import uvicorn

from control_plane_kit import (
    CapabilityName,
)
from control_plane_kit.servers import (
    BlockControlState,
    create_block_control_app,
)
from control_plane_kit.adapters.control_http import (
    BlockControlHttpInterpreter,
    ControlAddressPolicy,
    ControlAddressSource,
    ControlAuthority,
    ControlEndpointObservation,
    ControlHttpLimits,
    CredentialReference,
    RuntimeEndpointProvenance,
    SecretValue,
    StaticControlAuthorityProvider,
)
from control_plane_kit.effects import (
    ActivateTarget,
    EffectIdentity,
    EffectRequest,
    EffectSucceeded,
    EndpointMaterial,
    EndpointReference,
    LiteralEndpointMaterial,
    MaterializedEffectRequest,
    PinnedGraphSet,
    RegisterObserver,
    RegisterTarget,
    SocketConnectionMaterial,
    TimeoutPolicy,
)
from control_plane_kit.planning import ActivityId
from control_plane_kit.core.types import EndpointScope, Protocol
from control_plane_kit.core.secrets import SecretProviderAuthority, SecretProviderId, SecretResolved


@dataclass
class Resolver:
    value: str

    authority = SecretProviderAuthority(SecretProviderId("test"))

    def resolve(self, reference: CredentialReference) -> SecretResolved:
        return SecretResolved(reference, SecretValue(self.value))


class ControlHttpLiveTests(unittest.TestCase):
    def test_package_blocks_use_authenticated_live_protocol(self) -> None:
        cases = (
            (
                BlockControlState(
                    "router",
                    capabilities=(CapabilityName.SWITCHABLE,),
                    targets={"api": "http://api:8000"},
                ),
                ActivateTarget("router", "api"),
            ),
            (
                BlockControlState("limiter", capabilities=(CapabilityName.TARGET_MUTABLE,)),
                _register_target("limiter"),
            ),
            (
                BlockControlState("balancer", capabilities=(CapabilityName.TARGET_MUTABLE,)),
                _register_target("balancer"),
            ),
            (
                BlockControlState("multiplexer", capabilities=(CapabilityName.OBSERVER_MUTABLE,)),
                RegisterObserver("multiplexer", "logger", EndpointReference("logger-internal")),
            ),
        )
        for state, action in cases:
            with self.subTest(block=state.block_id):
                with live_server(
                    create_block_control_app(
                        state,
                        token="live-token",
                        execution_mode=True,
                    )
                ) as base_url:
                    result = _interpreter(state.block_id, base_url, "live-token").execute(
                        _request(action)
                    )
                self.assertIsInstance(result, EffectSucceeded)

        logger = BlockControlState(
            "logger",
            capabilities=(CapabilityName.LOG_READABLE,),
            log_provider=lambda: ["request accepted", "response sent"],
        )
        with live_server(
            create_block_control_app(logger, token="live-token", execution_mode=True)
        ) as base_url:
            evidence = _interpreter("logger", base_url, "live-token").read_logs(
                "logger",
                request_id="read-logger",
                idempotency_key="read-logger",
            )
        self.assertEqual(evidence.descriptor()["lines"], ["request accepted", "response sent"])

    def test_live_mutation_replay_converges_and_changed_intent_conflicts(self) -> None:
        state = BlockControlState(
            "router",
            capabilities=(CapabilityName.SWITCHABLE,),
            targets={"v1": "http://v1", "v2": "http://v2"},
            active_target="v1",
        )
        with live_server(
            create_block_control_app(state, token="live-token", execution_mode=True)
        ) as base_url:
            interpreter = _interpreter("router", base_url, "live-token")
            request = _request(ActivateTarget("router", "v2"))
            first = interpreter.execute(request)
            replay = interpreter.execute(request)
            conflict = interpreter.execute(
                _request(ActivateTarget("router", "v1"))
            )

        self.assertIsInstance(first, EffectSucceeded)
        self.assertIsInstance(replay, EffectSucceeded)
        self.assertEqual(state.active_target, "v2")
        self.assertEqual(state.runtime.version, 1)
        self.assertEqual(conflict.failure.code, "control.rejected")

    def test_live_authentication_fails_closed_without_leaking_token(self) -> None:
        state = BlockControlState(
            "router",
            capabilities=(CapabilityName.SWITCHABLE,),
            targets={"api": "http://api"},
        )
        with live_server(
            create_block_control_app(state, token="expected-token", execution_mode=True)
        ) as base_url:
            self.assertEqual(httpx.get(f"{base_url}/__deploy/status").status_code, 401)
            result = _interpreter("router", base_url, "wrong-secret-token").execute(
                _request(ActivateTarget("router", "api"))
            )

        self.assertEqual(result.failure.code, "control.unauthorized")
        self.assertNotIn("wrong-secret-token", str(result.failure))
        self.assertNotIn("expected-token", str(result.failure))

    def test_live_adversarial_peers_remain_bounded_and_redirect_free(self) -> None:
        cases = (
            ("redirect", _adversarial_app("redirect"), "control.redirect-rejected"),
            ("oversized", _adversarial_app("oversized"), "control.response-too-large"),
            ("slow", _adversarial_app("slow"), "control.timeout"),
            ("malformed", _adversarial_app("malformed"), "control.malformed-response"),
            ("content-type", _adversarial_app("content-type"), "control.malformed-response"),
        )
        for name, app, expected_code in cases:
            with self.subTest(peer=name):
                with live_server(app) as base_url:
                    result = _interpreter(
                        "router",
                        base_url,
                        "synthetic-token",
                        limits=ControlHttpLimits(response_bytes=256),
                    ).execute(
                        _request(
                            ActivateTarget("router", "api"),
                            timeout=1,
                        )
                    )
                self.assertEqual(result.failure.code, expected_code)
                self.assertNotIn("adversarial-secret", str(result.failure))


@contextmanager
def live_server(app):
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(128)
    port = listener.getsockname()[1]
    server = uvicorn.Server(
        uvicorn.Config(app, log_level="error", access_log=False, lifespan="off")
    )
    thread = Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
    thread.start()
    deadline = time.monotonic() + 5
    while not server.started and thread.is_alive() and time.monotonic() < deadline:
        time.sleep(0.01)
    if not server.started:
        server.should_exit = True
        thread.join(timeout=2)
        listener.close()
        raise RuntimeError("live FastAPI server did not start")
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        listener.close()
        if thread.is_alive():
            raise RuntimeError("live FastAPI server did not stop")


def _interpreter(
    subject_id: str,
    base_url: str,
    token: str,
    *,
    limits: ControlHttpLimits | None = None,
) -> BlockControlHttpInterpreter:
    observation = ControlEndpointObservation(
        subject_id,
        EndpointMaterial(
            "control",
            Protocol.HTTP,
            EndpointScope.LOCAL,
            LiteralEndpointMaterial(base_url),
        ),
        RuntimeEndpointProvenance(ControlAddressSource.HOST_LOCAL, "live-test"),
    )
    return BlockControlHttpInterpreter(
        StaticControlAuthorityProvider(
            {subject_id: ControlAuthority(observation, CredentialReference("secret://test/live-token"))}
        ),
        Resolver(token),
        ControlAddressPolicy(allow_host_local=True),
        limits=limits,
    )


def _request(action, *, timeout: int = 5) -> MaterializedEffectRequest:
    request = EffectRequest(
        EffectIdentity("run-live", ActivityId("edge"), 1, "run-live:edge:1"),
        action,
        TimeoutPolicy(total_seconds=timeout),
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
        request,
        PinnedGraphSet("workspace", "plan", "base", "desired"),
        "desired",
        material,
    )


def _register_target(controller_id: str):
    return RegisterTarget(controller_id, "api", EndpointReference("api-internal"))


def _adversarial_app(mode: str) -> FastAPI:
    app = FastAPI()

    @app.get("/__deploy/capabilities")
    def capabilities():
        if mode == "redirect":
            return RedirectResponse("http://127.0.0.1:1/adversarial-secret")
        if mode == "oversized":
            return JSONResponse({"block_id": "router", "capabilities": [], "padding": "x" * 4096})
        if mode == "slow":
            time.sleep(1.5)
            return {"block_id": "router", "capabilities": []}
        if mode == "malformed":
            return Response("{adversarial-secret", media_type="application/json")
        return PlainTextResponse("adversarial-secret")

    return app


if __name__ == "__main__":
    unittest.main()
