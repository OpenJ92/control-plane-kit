from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient
import jwt

from control_plane_kit_core.gateway_delegation import (
    DelegatedGatewayProbeGrant,
    DelegatedGatewayProbeVerificationCode,
    GatewayProbeCommandKind,
    GatewayProbeRequest,
)
from control_plane_kit_core.algebra import (
    DeploymentTopology,
    DockerRuntime,
    SocketConnection,
)
from control_plane_kit_core.environment import PublicStaticEnvironmentBinding
from control_plane_kit_core.products import (
    ProductDescriptorCodec,
    ProductFamily,
    ProductIdentity,
    ProductInstanceConfiguration,
    instantiate_product,
)
from control_plane_kit_core.runtime_effects import GatewayTargetId
from control_plane_kit_core.secrets import (
    SecretEnvironmentDelivery,
    SecretReference,
    SecretUseIntent,
)
from control_plane_kit_core.types import Protocol, SocketBinding
from control_plane_kit_core.topology import compile_topology


ROOT = Path(__file__).resolve().parents[3]
PRODUCT = ROOT / "products" / "cpk_local_gateway"
PRODUCT_SRC = PRODUCT / "src"
DESCRIPTOR = PRODUCT / "product.cpk.json"
STRUCTURAL_GRANT_CHECK = (
    ROOT / "scripts" / "cpk_local_gateway_structural_grant_check.py"
)
ISSUER = "urn:control-plane-kit:test-gateway"
AUDIENCE = "gateway:workspace-auth-private:gateway"
GATEWAY_NODE_ID = "gateway"
KEY_ID = "gateway-test-key"


class CpkLocalGatewayProductTests(unittest.TestCase):
    def setUp(self) -> None:
        sys.path.insert(0, str(PRODUCT_SRC))

    def tearDown(self) -> None:
        sys.path.remove(str(PRODUCT_SRC))
        for name in list(sys.modules):
            if name == "control_plane_kit_servers_cpk_local_gateway" or name.startswith(
                "control_plane_kit_servers_cpk_local_gateway."
            ):
                sys.modules.pop(name, None)

    def decode(self):
        return ProductDescriptorCodec().decode_document(DESCRIPTOR.read_bytes())

    def test_descriptor_round_trips_as_external_product_contract(self) -> None:
        document = self.decode()
        product = document.product

        self.assertEqual(
            product.identity,
            ProductIdentity("control-plane-kit", "cpk-local-gateway", 1),
        )
        self.assertEqual(product.display_name, "cpk-local-gateway")
        self.assertIs(product.product_family, ProductFamily.SERVER)
        self.assertEqual(product.image.registry, "ghcr.io")
        self.assertEqual(
            product.image.repository,
            "openj92/control-plane-kit-servers/cpk-local-gateway",
        )
        self.assertEqual(
            ProductDescriptorCodec().encode_document(product).content,
            DESCRIPTOR.read_bytes(),
        )

    def test_descriptor_declares_control_provider_and_target_map_config(self) -> None:
        product = self.decode().product
        sockets = product.runtime_contract.sockets

        self.assertEqual(sockets.provider("control").protocol, Protocol.HTTP)
        self.assertEqual(
            {
                value.provider_socket: value.container_port
                for value in product.runtime_contract.provider_ports
            },
            {"control": 8000},
        )
        self.assertEqual(
            sockets.requirement_names(),
            ("target-http", "target-postgres"),
        )
        self.assertEqual(sockets.requirement("target-http").protocol, Protocol.HTTP)
        self.assertEqual(
            sockets.requirement("target-http").env_bindings,
            (),
        )
        self.assertIs(
            sockets.requirement("target-http").binding,
            SocketBinding.RUNTIME_CONTROL,
        )
        self.assertEqual(
            sockets.requirement("target-postgres").protocol,
            Protocol.POSTGRES,
        )
        self.assertEqual(
            sockets.requirement("target-postgres").env_bindings,
            (),
        )
        self.assertIs(
            sockets.requirement("target-postgres").binding,
            SocketBinding.RUNTIME_CONTROL,
        )
        self.assertEqual(sockets.requirement("target-http").secret_deliveries, ())
        self.assertEqual(
            sockets.requirement("target-postgres").secret_deliveries,
            (
                SecretEnvironmentDelivery(
                    "POSTGRES_PASSWORD",
                    SecretReference("secret://control-plane-kit/postgres/password"),
                    SecretUseIntent.POSTGRES_PASSWORD,
                ),
            ),
        )
        self.assertEqual(
            product.runtime_contract.public_environment,
            (
                PublicStaticEnvironmentBinding("CPK_GATEWAY_PROBE_AUDIENCE", "gateway"),
                PublicStaticEnvironmentBinding("CPK_GATEWAY_PROBE_ISSUER", "cpk"),
                PublicStaticEnvironmentBinding(
                    "CPK_GATEWAY_PROBE_NODE_ID",
                    "gateway",
                ),
                PublicStaticEnvironmentBinding(
                    "CPK_GATEWAY_PROBE_VERIFICATION_KEYS_JSON",
                    "{}",
                ),
                PublicStaticEnvironmentBinding(
                    "CPK_GATEWAY_PROBE_VERIFIER",
                    "ed25519",
                ),
                PublicStaticEnvironmentBinding("CPK_GATEWAY_TARGETS_JSON", "{}"),
            ),
        )
        self.assertEqual(product.runtime_contract.secret_deliveries, ())
        self.assertEqual(product.runtime_contract.retained_data_mounts, ())
        self.assertIn("closed semantic probe", product.description.lower())

    def test_postgres_edge_activates_and_removal_revokes_password_delivery(
        self,
    ) -> None:
        gateway_product = self.decode().product
        postgres_product = ProductDescriptorCodec().decode_document(
            (ROOT / "products" / "postgres_server" / "product.cpk.json").read_bytes()
        ).product
        hello_product = ProductDescriptorCodec().decode_document(
            (ROOT / "products" / "hello_server" / "product.cpk.json").read_bytes()
        ).product
        gateway = instantiate_product(
            gateway_product,
            "gateway",
            ProductInstanceConfiguration.from_contract(
                gateway_product.runtime_contract
            ),
        )
        postgres = instantiate_product(
            postgres_product,
            "postgres",
            ProductInstanceConfiguration.from_contract(
                postgres_product.runtime_contract
            ),
        )
        hello = instantiate_product(
            hello_product,
            "hello",
            ProductInstanceConfiguration.from_contract(
                hello_product.runtime_contract
            ),
        )

        disconnected = compile_topology(
            DeploymentTopology(
                "gateway-disconnected",
                DockerRuntime(children=(gateway,)),
            )
        )
        connected = compile_topology(
            DeploymentTopology(
                "gateway-connected",
                DockerRuntime(
                    children=(
                        gateway,
                        postgres,
                        SocketConnection(
                            "postgres",
                            "postgres",
                            "gateway",
                            "target-postgres",
                        ),
                    )
                ),
            )
        )
        http_only = compile_topology(
            DeploymentTopology(
                "gateway-http-only",
                DockerRuntime(
                    children=(
                        gateway,
                        hello,
                        SocketConnection(
                            "hello",
                            "internal",
                            "gateway",
                            "target-http",
                        ),
                    )
                ),
            )
        )

        self.assertEqual(disconnected.node("gateway").secret_deliveries, ())
        self.assertFalse(disconnected.edges)
        self.assertEqual(http_only.node("gateway").secret_deliveries, ())
        self.assertEqual(
            connected.node("gateway").secret_deliveries,
            gateway.sockets.requirement("target-postgres").secret_deliveries,
        )

    def test_descriptor_instantiates_without_importing_process_code(self) -> None:
        product = self.decode().product

        block = instantiate_product(
            product,
            "gateway",
            ProductInstanceConfiguration.from_contract(product.runtime_contract),
        )

        self.assertEqual(block.block_id, "gateway")
        self.assertEqual(block.sockets.provider("control").protocol, Protocol.HTTP)
        self.assertNotIn(
            "control_plane_kit_servers_cpk_local_gateway.server",
            sys.modules,
        )

    def test_probe_request_contract_rejects_unknown_kind_and_target(self) -> None:
        from control_plane_kit_servers_cpk_local_gateway import (
            GatewayConfiguration,
            GatewayConfigurationError,
            execute_probe,
        )

        gateway = GatewayConfiguration.from_target_map(
            {
                "hello.internal": {
                    "protocol": "http",
                    "url": "http://hello:8000",
                }
            }
        )

        with self.assertRaisesRegex(GatewayConfigurationError, "unsupported probe kind"):
            execute_probe(gateway, {"kind": "tcp-open", "target_id": "hello.internal"})
        with self.assertRaisesRegex(GatewayConfigurationError, "unknown target"):
            execute_probe(gateway, {"kind": "http-status", "target_id": "db.postgres"})

    def test_gateway_route_rejects_missing_and_forged_capabilities_before_target_io(
        self,
    ) -> None:
        from control_plane_kit_servers_cpk_local_gateway import (
            Ed25519GatewayProbeVerifier,
            GatewayConfiguration,
            GatewayProbeReplayCache,
            create_app,
        )

        private_key, public_key = _ed25519_keys()
        now = 1_750_000_000
        verifier = Ed25519GatewayProbeVerifier(
            issuer=ISSUER,
            audience=AUDIENCE,
            gateway_node_id=GATEWAY_NODE_ID,
            public_keys={KEY_ID: public_key},
            replay_cache=GatewayProbeReplayCache(clock=lambda: now),
            clock=lambda: now,
        )
        gateway = GatewayConfiguration.from_target_map(
            {
                "hello.internal": {
                    "protocol": "http",
                    "url": "http://hello:8000",
                }
            }
        )
        request = GatewayProbeRequest(
            GatewayProbeCommandKind.HTTP_STATUS,
            GatewayTargetId("hello.internal"),
            "/",
        )
        body = _request_body(request)
        forged = _signed_capability(
            Ed25519PrivateKey.generate(),
            _grant(request, now=now),
        )

        with patch(
            "control_plane_kit_servers_cpk_local_gateway.server.execute_probe"
        ) as execute:
            client = TestClient(create_app(gateway, verifier=verifier))
            missing = client.post(
                "/cpk/probes",
                content=body,
                headers={"Content-Type": "application/json"},
            )
            invalid = client.post(
                "/cpk/probes",
                content=body,
                headers={
                    "Authorization": f"CPK-Gateway {forged}",
                    "Content-Type": "application/json",
                },
            )

        self.assertEqual(missing.status_code, 401)
        self.assertEqual(invalid.status_code, 401)
        self.assertEqual(
            missing.json(),
            {
                "outcome": "failed",
                "code": "gateway.capability-rejected",
            },
        )
        self.assertNotIn(forged, invalid.text)
        execute.assert_not_called()
        self.assertEqual(repr(verifier), "Ed25519GatewayProbeVerifier(<redacted>)")

    def test_gateway_verifier_binds_exact_request_authority_and_time(self) -> None:
        from control_plane_kit_servers_cpk_local_gateway import (
            Ed25519GatewayProbeVerifier,
            GatewayProbeReplayCache,
            GatewayProbeVerificationError,
        )

        private_key, public_key = _ed25519_keys()
        now = 1_750_000_000
        request = GatewayProbeRequest(
            GatewayProbeCommandKind.HTTP_STATUS,
            GatewayTargetId("hello.internal"),
            "/",
        )

        def verifier() -> Ed25519GatewayProbeVerifier:
            return Ed25519GatewayProbeVerifier(
                issuer=ISSUER,
                audience=AUDIENCE,
                gateway_node_id=GATEWAY_NODE_ID,
                public_keys={KEY_ID: public_key},
                replay_cache=GatewayProbeReplayCache(clock=lambda: now),
                clock=lambda: now,
            )

        accepted = verifier().verify(
            f"CPK-Gateway {_signed_capability(private_key, _grant(request, now=now))}",
            _request_body(request),
        )
        self.assertEqual(accepted, request)

        cases = (
            (
                replace_grant(_grant(request, now=now), audience="gateway:other:gateway"),
                request,
                DelegatedGatewayProbeVerificationCode.AUDIENCE_MISMATCH,
            ),
            (
                replace_grant(_grant(request, now=now), gateway_node_id="other-gateway"),
                request,
                DelegatedGatewayProbeVerificationCode.AUDIENCE_MISMATCH,
            ),
            (
                replace_grant(
                    _grant(request, now=now),
                    issued_at=now + 20,
                    expires_at=now + 80,
                ),
                request,
                DelegatedGatewayProbeVerificationCode.TEMPORALLY_INVALID,
            ),
            (
                replace_grant(
                    _grant(request, now=now),
                    issued_at=now - 80,
                    expires_at=now - 20,
                ),
                request,
                DelegatedGatewayProbeVerificationCode.TEMPORALLY_INVALID,
            ),
            (
                _grant(request, now=now),
                GatewayProbeRequest(
                    GatewayProbeCommandKind.HTTP_STATUS,
                    GatewayTargetId("hello.internal"),
                    "/health/ready",
                ),
                DelegatedGatewayProbeVerificationCode.REQUEST_MISMATCH,
            ),
        )
        for grant, inbound_request, expected_code in cases:
            with self.subTest(code=expected_code):
                with self.assertRaises(GatewayProbeVerificationError) as raised:
                    verifier().verify(
                        f"CPK-Gateway {_signed_capability(private_key, grant)}",
                        _request_body(inbound_request),
                    )
                self.assertIs(raised.exception.code, expected_code)

    def test_gateway_replay_cache_allows_exactly_one_concurrent_request(self) -> None:
        from control_plane_kit_servers_cpk_local_gateway import (
            Ed25519GatewayProbeVerifier,
            GatewayProbeReplayCache,
            GatewayProbeVerificationError,
        )

        private_key, public_key = _ed25519_keys()
        now = 1_750_000_000
        request = GatewayProbeRequest(
            GatewayProbeCommandKind.POSTGRES_SELECT_ONE,
            GatewayTargetId("postgres.postgres"),
        )
        token = _signed_capability(private_key, _grant(request, now=now))
        verifier = Ed25519GatewayProbeVerifier(
            issuer=ISSUER,
            audience=AUDIENCE,
            gateway_node_id=GATEWAY_NODE_ID,
            public_keys={KEY_ID: public_key},
            replay_cache=GatewayProbeReplayCache(clock=lambda: now),
            clock=lambda: now,
        )

        def verify_once() -> str:
            try:
                verifier.verify(
                    f"CPK-Gateway {token}",
                    _request_body(request),
                )
            except GatewayProbeVerificationError as error:
                return error.code.value
            return "accepted"

        with ThreadPoolExecutor(max_workers=8) as executor:
            results = tuple(executor.map(lambda _: verify_once(), range(8)))

        self.assertEqual(results.count("accepted"), 1)
        self.assertEqual(
            results.count(DelegatedGatewayProbeVerificationCode.REPLAYED.value),
            7,
        )

        restarted = Ed25519GatewayProbeVerifier(
            issuer=ISSUER,
            audience=AUDIENCE,
            gateway_node_id=GATEWAY_NODE_ID,
            public_keys={KEY_ID: public_key},
            replay_cache=GatewayProbeReplayCache(clock=lambda: now),
            clock=lambda: now,
        )
        self.assertEqual(
            restarted.verify(f"CPK-Gateway {token}", _request_body(request)),
            request,
        )

    def test_valid_capability_still_cannot_reach_an_undeclared_target(self) -> None:
        from control_plane_kit_servers_cpk_local_gateway import (
            Ed25519GatewayProbeVerifier,
            GatewayConfiguration,
            GatewayProbeReplayCache,
            create_app,
        )

        private_key, public_key = _ed25519_keys()
        now = 1_750_000_000
        request = GatewayProbeRequest(
            GatewayProbeCommandKind.HTTP_STATUS,
            GatewayTargetId("missing.internal"),
            "/",
        )
        verifier = Ed25519GatewayProbeVerifier(
            issuer=ISSUER,
            audience=AUDIENCE,
            gateway_node_id=GATEWAY_NODE_ID,
            public_keys={KEY_ID: public_key},
            replay_cache=GatewayProbeReplayCache(clock=lambda: now),
            clock=lambda: now,
        )
        client = TestClient(
            create_app(
                GatewayConfiguration.from_target_map({}),
                verifier=verifier,
            )
        )

        with patch(
            "control_plane_kit_servers_cpk_local_gateway.server._http_status"
        ) as transport:
            response = client.post(
                "/cpk/probes",
                content=_request_body(request),
                headers={
                    "Authorization": (
                        "CPK-Gateway "
                        + _signed_capability(private_key, _grant(request, now=now))
                    ),
                    "Content-Type": "application/json",
                },
            )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["code"], "gateway.probe-rejected")
        transport.assert_not_called()

    def test_health_is_public_minimal_and_discloses_no_target_count(self) -> None:
        from control_plane_kit_servers_cpk_local_gateway import (
            Ed25519GatewayProbeVerifier,
            GatewayConfiguration,
            GatewayProbeReplayCache,
            create_app,
        )

        _, public_key = _ed25519_keys()
        now = 1_750_000_000
        verifier = Ed25519GatewayProbeVerifier(
            issuer=ISSUER,
            audience=AUDIENCE,
            gateway_node_id=GATEWAY_NODE_ID,
            public_keys={KEY_ID: public_key},
            replay_cache=GatewayProbeReplayCache(clock=lambda: now),
            clock=lambda: now,
        )
        client = TestClient(
            create_app(
                GatewayConfiguration.from_target_map(
                    {
                        "hello.internal": {
                            "protocol": "http",
                            "url": "http://hello:8000",
                        }
                    }
                ),
                verifier=verifier,
            )
        )

        self.assertEqual(client.get("/health/live").json(), {"status": "live"})
        self.assertEqual(client.get("/health/ready").json(), {"status": "ready"})
        self.assertNotIn("targets", client.get("/health/ready").json())

    def test_http_probe_uses_declared_target_without_forwarding_arbitrary_url(self) -> None:
        from control_plane_kit_servers_cpk_local_gateway import (
            GatewayConfiguration,
            execute_probe,
        )

        gateway = GatewayConfiguration.from_target_map(
            {
                "hello.internal": {
                    "protocol": "http",
                    "url": "http://hello:8000",
                }
            }
        )

        with patch(
            "control_plane_kit_servers_cpk_local_gateway.server._http_status",
            return_value={"status": 200, "body_size": 5},
        ) as transport:
            result = execute_probe(
                gateway,
                {
                    "kind": "http-status",
                    "target_id": "hello.internal",
                    "path": "/health/ready",
                },
            )

        self.assertEqual(result["outcome"], "passed")
        transport.assert_called_once_with("http://hello:8000/health/ready")
        self.assertNotIn("http://evil", json.dumps(result))

    def test_postgres_probe_contract_is_secret_free(self) -> None:
        from control_plane_kit_servers_cpk_local_gateway import GatewayConfiguration

        gateway = GatewayConfiguration.from_target_map(
            {
                "postgres.postgres": {
                    "protocol": "postgres",
                    "host": "postgres",
                    "port": 5432,
                }
            }
        )
        descriptor = gateway.targets["postgres.postgres"].descriptor()

        self.assertEqual(descriptor["protocol"], "postgres")
        self.assertNotIn("password", json.dumps(descriptor).lower())
        self.assertNotIn("secret", json.dumps(descriptor).lower())
        self.assertNotIn("postgres://", json.dumps(descriptor).lower())

    def test_entrypoint_source_preserves_closed_gateway_boundary(self) -> None:
        source = (
            PRODUCT_SRC / "control_plane_kit_servers_cpk_local_gateway" / "server.py"
        ).read_text(encoding="utf-8")

        self.assertIn("CPK_GATEWAY_TARGETS_JSON", source)
        self.assertIn("http-status", source)
        self.assertIn("postgres-select-one", source)
        self.assertNotIn("subprocess", source)
        self.assertNotIn("docker", source.lower())
        self.assertNotIn("graph_store", source)

    def test_dockerfile_uses_product_entrypoint_and_non_root_user(self) -> None:
        dockerfile = (PRODUCT / "Dockerfile").read_text(encoding="utf-8")

        self.assertIn("USER gateway", dockerfile)
        self.assertIn("control_plane_kit_servers_cpk_local_gateway.server", dockerfile)
        self.assertIn("EXPOSE 8000", dockerfile)
        self.assertNotIn("docker system prune", dockerfile)

    def test_descriptor_digest_is_catalogue_ready(self) -> None:
        content = DESCRIPTOR.read_bytes()
        digest = hashlib.sha256(content).hexdigest()

        self.assertEqual(len(digest), 64)
        self.assertEqual(
            ProductDescriptorCodec().decode_document(content).content_digest,
            digest,
        )
        catalogue = json.loads((ROOT / "catalogue" / "products.json").read_text())
        entry = next(
            product
            for product in catalogue["products"]
            if product["product_id"] == "cpk-local-gateway"
        )
        self.assertEqual(entry["descriptor_sha256"], digest)

    def test_private_probe_smoke_uses_gateway_as_only_public_probe_surface(self) -> None:
        smoke = (
            ROOT / "scripts" / "cpk_local_gateway_private_probe_smoke.sh"
        ).read_text(encoding="utf-8")

        self.assertIn("products/cpk_local_gateway/product.cpk.json", smoke)
        self.assertIn("products/hello_server/product.cpk.json", smoke)
        self.assertIn("products/postgres_server/product.cpk.json", smoke)
        self.assertIn("docker pull \"$GATEWAY_IMAGE\"", smoke)
        self.assertIn("127.0.0.1:$GATEWAY_PORT:8000", smoke)
        self.assertIn('"kind":"http-status"', smoke)
        self.assertIn('"kind":"postgres-select-one"', smoke)
        self.assertIn('"target_id":"missing.http"', smoke)
        self.assertIn('"kind":"tcp-open"', smoke)
        self.assertIn("password_environment", smoke)
        self.assertNotIn("sync_runtime_networks", smoke)
        self.assertNotIn("-p 5432", smoke)
        self.assertNotIn("docker system prune", smoke)

    def test_structural_grant_check_accepts_source_live_identity(self) -> None:
        spec = importlib.util.spec_from_file_location(
            "cpk_local_gateway_structural_grant_check",
            STRUCTURAL_GRANT_CHECK,
        )
        if spec is None or spec.loader is None:
            self.fail("could not load gateway structural grant check")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        self.assertEqual(module.main(), 0)
        self.assertTrue(module.WORKSPACE_ID.startswith("workspace-secret-cloudflare-"))
        self.assertEqual(
            module.AUDIENCE,
            f"gateway:{module.WORKSPACE_ID}:gateway",
        )

def _ed25519_keys() -> tuple[Ed25519PrivateKey, str]:
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("ascii")
    return private_key, public_key


def _grant(
    request: GatewayProbeRequest,
    *,
    now: int,
) -> DelegatedGatewayProbeGrant:
    return DelegatedGatewayProbeGrant(
        issuer=ISSUER,
        key_id=KEY_ID,
        audience=AUDIENCE,
        workspace_id="workspace-auth-private",
        operation_id="probe-operation",
        request_id="probe-request",
        gateway_node_id=GATEWAY_NODE_ID,
        probe_kind=request.kind,
        target_id=request.target_id,
        request_digest=request.canonical_digest(),
        issued_at=now - 1,
        expires_at=now + 60,
        jti="probe-jti",
    )


def replace_grant(
    grant: DelegatedGatewayProbeGrant,
    **changes: object,
) -> DelegatedGatewayProbeGrant:
    descriptor = grant.descriptor()
    descriptor.update(changes)
    return DelegatedGatewayProbeGrant(
        issuer=str(descriptor["issuer"]),
        key_id=str(descriptor["key_id"]),
        audience=str(descriptor["audience"]),
        workspace_id=str(descriptor["workspace_id"]),
        operation_id=str(descriptor["operation_id"]),
        request_id=str(descriptor["request_id"]),
        gateway_node_id=str(descriptor["gateway_node_id"]),
        probe_kind=GatewayProbeCommandKind(str(descriptor["probe_kind"])),
        target_id=GatewayTargetId(str(descriptor["target_id"])),
        request_digest=grant.request_digest,
        issued_at=int(descriptor["issued_at"]),
        expires_at=int(descriptor["expires_at"]),
        jti=str(descriptor["jti"]),
    )


def _signed_capability(
    private_key: Ed25519PrivateKey,
    grant: DelegatedGatewayProbeGrant,
) -> str:
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    return jwt.encode(
        {
            "iss": grant.issuer,
            "aud": grant.audience,
            "iat": grant.issued_at,
            "exp": grant.expires_at,
            "jti": grant.jti,
            "gateway_probe": grant.descriptor(),
        },
        private_pem,
        algorithm="EdDSA",
        headers={
            "kid": grant.key_id,
            "typ": "CPK-GATEWAY-PROBE+JWT",
        },
    )


def _request_body(request: GatewayProbeRequest) -> bytes:
    return json.dumps(
        request.descriptor(),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("ascii")


if __name__ == "__main__":
    unittest.main()
