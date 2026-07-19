from __future__ import annotations

import json
import os
import socket
import subprocess
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from control_plane_kit import (
    AuthGatewayPolicy,
    AuthenticatedIdentity,
    AuthenticationAccepted,
    AuthenticationMechanism,
    AuthenticationRejected,
    AuthenticationRejection,
    AuthorizationDecision,
    DeploymentRecipe,
    DockerRuntime,
    ForwardedIdentityHeader,
    GatewayMethod,
    GraphDescriptorCodec,
    HttpAuthGatewayServer,
    HttpRequest,
    HttpResponse,
    JwtAlgorithm,
    PackageServerProduct,
    ProductMaturity,
    RouteAuthorizationPolicy,
    StaticApiKeyValidator,
    auth_gateway_policy_from_descriptor,
    compile_recipe,
    http_auth_gateway_block,
    http_auth_gateway_command,
)


def _policy() -> AuthGatewayPolicy:
    return AuthGatewayPolicy(
        AuthenticationMechanism.API_KEY,
        (
            RouteAuthorizationPolicy("/read", (GatewayMethod.GET,), ("read",)),
            RouteAuthorizationPolicy("/admin", (GatewayMethod.POST,), ("admin",)),
        ),
        forwarded_headers=(
            ForwardedIdentityHeader.SUBJECT,
            ForwardedIdentityHeader.ISSUER,
            ForwardedIdentityHeader.SCOPES,
        ),
    )


class HttpAuthGatewayTests(unittest.TestCase):
    def test_authentication_and_authorization_are_separate_closed_decisions(self) -> None:
        forwarded: list[HttpRequest] = []
        validator = StaticApiKeyValidator(
            "correct-key",
            AuthenticatedIdentity("operator-a", "test-key", ("read",)),
        )
        server = HttpAuthGatewayServer(
            {"target": lambda request: forwarded.append(request) or HttpResponse.text("ok")},
            "target",
            _policy(),
            validator,
        )

        self.assertEqual(server.handle(HttpRequest(path="/read")).status_code, 401)
        self.assertEqual(
            server.handle(HttpRequest(path="/read", headers={"X-Api-Key": "wrong"})).status_code,
            401,
        )
        self.assertEqual(
            server.handle(HttpRequest(method="POST", path="/admin", headers={"X-Api-Key": "correct-key"})).status_code,
            403,
        )
        self.assertEqual(
            server.handle(HttpRequest(path="/read", headers={"X-Api-Key": "correct-key"})).status_code,
            200,
        )
        self.assertEqual(len(forwarded), 1)
        observation = server.observation()
        self.assertEqual(observation.rejected_count, 2)
        self.assertEqual(observation.forbidden_count, 1)
        self.assertEqual(observation.forwarded_count, 1)
        self.assertIs(observation.latest_authorization, AuthorizationDecision.ALLOWED)

    def test_untrusted_identity_headers_are_replaced_after_authentication(self) -> None:
        forwarded: list[HttpRequest] = []
        server = HttpAuthGatewayServer(
            {"target": lambda request: forwarded.append(request) or HttpResponse.text("ok")},
            "target",
            _policy(),
            StaticApiKeyValidator(
                "key",
                AuthenticatedIdentity("real-subject", "real-issuer", ("read",)),
            ),
        )
        request = HttpRequest(
            path="/read",
            headers={
                "X-Api-Key": "key",
                "X-CPK-Authenticated-Subject": "forged-subject",
                "X-CPK-Authenticated-Scopes": "admin",
            },
        )

        self.assertEqual(server.handle(request).status_code, 200)
        headers = {key.lower(): value for key, value in forwarded[0].headers.items()}
        self.assertEqual(headers["x-cpk-authenticated-subject"], "real-subject")
        self.assertEqual(headers["x-cpk-authenticated-issuer"], "real-issuer")
        self.assertEqual(headers["x-cpk-authenticated-scopes"], "read")
        self.assertNotIn("x-api-key", headers)
        self.assertNotIn("authorization", headers)
        self.assertNotIn("forged-subject", json.dumps(server.observation().descriptor()))
        self.assertNotIn("key", json.dumps(server.observation().descriptor()))

    def test_validator_unavailability_fails_closed_without_becoming_forbidden(self) -> None:
        class Unavailable:
            def authenticate(self, _request: HttpRequest):
                return AuthenticationRejected(AuthenticationRejection.IDENTITY_PROVIDER_UNAVAILABLE)

        server = HttpAuthGatewayServer(
            {"target": lambda _request: HttpResponse.text("must-not-run")},
            "target",
            _policy(),
            Unavailable(),
        )
        self.assertEqual(server.handle(HttpRequest(path="/read")).status_code, 503)
        self.assertEqual(server.observation().rejected_count, 1)
        self.assertEqual(server.observation().forbidden_count, 0)

    def test_route_prefixes_match_segments_not_lexical_prefixes(self) -> None:
        server = HttpAuthGatewayServer(
            {"target": lambda _request: HttpResponse.text("must-not-run")},
            "target",
            _policy(),
            StaticApiKeyValidator(
                "key",
                AuthenticatedIdentity("subject", "issuer", ("read", "admin")),
            ),
        )
        response = server.handle(
            HttpRequest(path="/reader", headers={"X-Api-Key": "key"})
        )
        self.assertEqual(response.status_code, 403)
        self.assertIs(
            server.observation().latest_authorization,
            AuthorizationDecision.ROUTE_NOT_ALLOWED,
        )

    def test_policy_language_is_closed_and_mechanism_specific(self) -> None:
        with self.assertRaisesRegex(ValueError, "issuer, audience, and algorithm"):
            AuthGatewayPolicy(
                AuthenticationMechanism.OIDC_JWT,
                (RouteAuthorizationPolicy("/", (GatewayMethod.GET,)),),
            )
        oidc = AuthGatewayPolicy(
            AuthenticationMechanism.OIDC_JWT,
            (RouteAuthorizationPolicy("/", (GatewayMethod.GET,)),),
            accepted_issuers=("https://issuer.example",),
            accepted_audiences=("orders-api",),
            accepted_algorithms=(JwtAlgorithm.RS256,),
        )
        with self.assertRaisesRegex(ValueError, "only API-key"):
            http_auth_gateway_command(oidc)
        with self.assertRaisesRegex(TypeError, "gateway methods"):
            RouteAuthorizationPolicy("/", ("GET",))  # type: ignore[arg-type]

        descriptor = oidc.descriptor()
        self.assertEqual(auth_gateway_policy_from_descriptor(descriptor), oidc)
        with self.assertRaisesRegex(ValueError, "unknown or missing"):
            auth_gateway_policy_from_descriptor({**descriptor, "escape": "open"})
        with self.assertRaisesRegex(ValueError, "unknown or missing"):
            auth_gateway_policy_from_descriptor({
                **descriptor,
                "routes": [{**descriptor["routes"][0], "escape": "open"}],
            })

    def test_block_is_test_only_and_secret_values_are_runtime_material(self) -> None:
        block = http_auth_gateway_block(policy=_policy(), api_key_scopes=("read",))
        self.assertIs(block.spec.product, PackageServerProduct.HTTP_AUTH_GATEWAY)
        self.assertIs(block.spec.maturity, ProductMaturity.TEST_ONLY)
        self.assertEqual(block.sockets.requirement_names(), ("target",))
        descriptor = json.dumps(
            GraphDescriptorCodec().encode(
                compile_recipe(DeploymentRecipe("auth", DockerRuntime(children=(block,))))
            ),
            sort_keys=True,
        )
        self.assertIn("secret://http-auth-gateway/api-key", descriptor)
        self.assertNotIn("correct-key", descriptor)

    def test_live_test_adapter_strips_forgery_enforces_policy_and_redacts(self) -> None:
        target = _TargetServer()
        target.start()
        self.addCleanup(target.stop)
        port = _free_port()
        environment = dict(os.environ)
        environment["AUTH_GATEWAY_TARGET_URL"] = f"http://127.0.0.1:{target.port}"
        environment["CPK_GATEWAY_API_KEY"] = "live-secret-key"
        environment["CPK_GATEWAY_CONTROL_TOKEN"] = "live-control-token"
        process = subprocess.Popen(
            http_auth_gateway_command(_policy(), api_key_scopes=("read",), port=port),
            env=environment,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self.addCleanup(_stop_process, process)
        _wait_ready(port)

        _expect_error(port, "/read", 401)
        _expect_error(port, "/read", 401, headers={"X-Api-Key": "wrong"})
        _expect_error(port, "/admin", 403, method="POST", headers={"X-Api-Key": "live-secret-key"})
        status, body = _request(
            port,
            "/read",
            headers={
                "X-Api-Key": "live-secret-key",
                "Authorization": "Bearer untrusted-token",
                "X-CPK-Authenticated-Subject": "forged",
                "X-CPK-Authenticated-Scopes": "admin",
            },
        )
        self.assertEqual(status, 200)
        forwarded = json.loads(body)
        self.assertEqual(forwarded["subject"], "api-key-client")
        self.assertEqual(forwarded["scopes"], "read")
        self.assertFalse(forwarded["api_key_forwarded"])
        self.assertFalse(forwarded["authorization_forwarded"])
        self.assertNotEqual(forwarded["subject"], "forged")

        _expect_error(port, "/__deploy/metrics", 401)
        metrics = json.loads(_request(port, "/__deploy/metrics", headers={"Authorization": "Bearer live-control-token"})[1])
        evidence = json.dumps(metrics)
        self.assertEqual(metrics["forwarded_count"], 1)
        self.assertNotIn("live-secret-key", evidence)
        self.assertNotIn("live-control-token", evidence)
        self.assertNotIn("forged", evidence)
        self.assertNotIn(str(target.port), evidence)


class _TargetHandler(BaseHTTPRequestHandler):
    def _respond(self) -> None:
        body = json.dumps({
            "subject": self.headers.get("x-cpk-authenticated-subject"),
            "issuer": self.headers.get("x-cpk-authenticated-issuer"),
            "scopes": self.headers.get("x-cpk-authenticated-scopes"),
            "api_key_forwarded": self.headers.get("x-api-key") is not None,
            "authorization_forwarded": self.headers.get("authorization") is not None,
        }, sort_keys=True).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    do_GET = _respond
    do_POST = _respond

    def log_message(self, format: str, *args: object) -> None:
        pass


class _TargetServer:
    def __init__(self) -> None:
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), _TargetHandler)
        self.port = self._server.server_port
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=2)


def _free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def _wait_ready(port: int) -> None:
    for _ in range(100):
        try:
            if _request(port, "/health")[0] == 200:
                return
        except OSError:
            time.sleep(0.02)
    raise RuntimeError("auth gateway did not become ready")


def _request(port: int, path: str, *, method: str = "GET", headers: dict[str, str] | None = None) -> tuple[int, bytes]:
    request = Request(f"http://127.0.0.1:{port}{path}", headers={} if headers is None else headers, method=method)
    with urlopen(request, timeout=2) as response:
        return response.status, response.read()


def _expect_error(port: int, path: str, status: int, *, method: str = "GET", headers: dict[str, str] | None = None) -> None:
    try:
        _request(port, path, method=method, headers=headers)
    except HTTPError as error:
        with error:
            error.read()
            if error.code != status:
                raise AssertionError(f"expected HTTP {status}, received {error.code}")
        return
    raise AssertionError(f"expected HTTP {status}")


def _stop_process(process: subprocess.Popen[bytes]) -> None:
    process.terminate()
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=2)


if __name__ == "__main__":
    unittest.main()
