import ast
import base64
import importlib
import json
from pathlib import Path
import tempfile
import sys
import time
import unittest

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
import httpx
import jwt


ROOT = Path(__file__).resolve().parents[3]
PRODUCT = ROOT / "products" / "cpk_server"
PRODUCT_SRC = PRODUCT / "src"
COORDINATES = json.loads(
    (ROOT / "coordinates" / "server-products.json").read_text(encoding="utf-8")
)
CPK_PIN = COORDINATES["upstreams"]["control_plane_kit_commit"]
INTERPRETERS_PIN = COORDINATES["upstreams"][
    "control_plane_kit_interpreters_commit"
]
STORE_ENVIRONMENT = [
    "CPK_WORKPLACE_DATABASE_URL",
    "CPK_ACTIVITY_HISTORY_DATABASE_URL",
    "CPK_OBSERVER_STATE_DATABASE_URL",
    "CPK_GRAPH_TOPOLOGY_DATABASE_URL",
]
SERVER_SOURCE = PRODUCT_SRC / "control_plane_kit_servers_cpk_server" / "server.py"
CONCRETE_PROVIDER_IMPORT_ROOTS = {
    "boto3",
    "botocore",
    "control_plane_kit_interpreters",
    "docker",
    "google",
    "kubernetes",
}
APPROVED_PROVIDER_FUNCTIONS = {
    "_cloudflare_ingress_interpreter",
    "_docker_runtime_interpreter",
    "_gateway_probe_dispatcher",
    "_secret_provider_composition",
}



class CpkServerImageBootstrapTests(unittest.TestCase):
    def test_dockerfile_runs_cpk_server_as_non_root_with_explicit_entrypoint(self) -> None:
        dockerfile = (PRODUCT / "Dockerfile").read_text(encoding="utf-8")

        self.assertIn("python:3.12-slim", dockerfile)
        self.assertIn("USER cpk", dockerfile)
        self.assertIn("control_plane_kit_servers_cpk_server.server", dockerfile)
        self.assertIn(
            "control-plane-kit-core @ "
            f"https://github.com/OpenJ92/control-plane-kit/archive/{CPK_PIN}.zip",
            dockerfile,
        )
        self.assertIn(
            "control-plane-kit-operations @ "
            f"https://github.com/OpenJ92/control-plane-kit/archive/{CPK_PIN}.zip",
            dockerfile,
        )
        self.assertIn(
            "control-plane-kit-interpreters[cloudflare,docker,gateway] @ "
            "https://github.com/OpenJ92/control-plane-kit-interpreters/archive/"
            f"{INTERPRETERS_PIN}.zip",
            dockerfile,
        )
        self.assertIn("fastapi>=0.115", dockerfile)
        self.assertIn("uvicorn>=0.30", dockerfile)
        self.assertIn("COPY products/cpk_server/src ./products/cpk_server/src", dockerfile)
        self.assertNotIn("COPY products/cpk_server ./products/cpk_server", dockerfile)
        self.assertNotIn("COPY catalogue", dockerfile)
        self.assertNotIn("COPY src ./src", dockerfile)
        self.assertIn("EXPOSE 8080", dockerfile)
        self.assertNotIn("apt-get", dockerfile)
        self.assertNotIn("latest", dockerfile)

    def test_bootstrap_contract_is_explicit_and_secret_free(self) -> None:
        contract = json.loads((PRODUCT / "bootstrap.contract.json").read_text(encoding="utf-8"))
        rendered = json.dumps(contract, sort_keys=True).lower()

        self.assertEqual(contract["schema"], "cpk-server.bootstrap-contract")
        self.assertEqual(
            [item["name"] for item in contract["environment"]],
            [
                "CPK_SERVER_MODE",
                "CPK_CONTROL_AUTH_VERIFIER",
                "CPK_CONTROL_AUTH_STATIC_CREDENTIAL",
                "CPK_CONTROL_AUTH_STATIC_WORKSPACE_GRANTS_JSON",
                "CPK_PORT",
                "CPK_RUNTIME_INTERPRETERS",
                "CPK_INGRESS_INTERPRETERS",
                "CPK_GATEWAY_PROBE_SIGNER",
                "CPK_GATEWAY_PROBE_SIGNING_KEY_REF",
                "CPK_GATEWAY_PROBE_ISSUER",
                "CPK_GATEWAY_PROBE_KEY_ID",
                "CPK_PRODUCT_MATERIAL_RESOLVER",
                "CPK_PRODUCT_SECRET_VALUES_JSON",
                "CPK_MATERIAL_PROVIDER_ROUTES_JSON",
                "CPK_MATERIAL_PROVIDER_BOOTSTRAP_FILES_JSON",
                *STORE_ENVIRONMENT,
            ],
        )
        self.assertNotIn("postgres://", rendered)
        self.assertNotIn("token-not-for-output", rendered)
        self.assertNotIn("secret://", rendered)
        self.assertNotIn("postgres-secret", rendered)
        self.assertIn("never echoed by readiness", rendered)

    def test_bootstrap_requires_store_endpoints_but_does_not_echo_them(self) -> None:
        sys.path.insert(0, str(PRODUCT_SRC))
        try:
            server_module = importlib.import_module(
                "control_plane_kit_servers_cpk_server.server"
            )
            environ = {
                "CPK_SERVER_MODE": "execution-capable",
                "CPK_CONTROL_AUTH_CONFIGURED": "true",
                "CPK_PORT": "8080",
                "CPK_RUNTIME_INTERPRETERS": "none",
                "CPK_WORKPLACE_DATABASE_URL": "postgres://user:pass@workspace/db",
                "CPK_ACTIVITY_HISTORY_DATABASE_URL": "postgres://user:pass@activity/db",
                "CPK_OBSERVER_STATE_DATABASE_URL": "postgres://user:pass@observer/db",
                "CPK_GRAPH_TOPOLOGY_DATABASE_URL": "postgres://user:pass@graph/db",
            }

            config = server_module.CpkServerBootstrapConfiguration.from_environment(
                environ
            )
            with self.assertRaisesRegex(
                server_module.BootstrapConfigurationError,
                "CPK_GRAPH_TOPOLOGY_DATABASE_URL is required",
            ):
                server_module.CpkServerBootstrapConfiguration.from_environment(
                    {
                        key: value
                        for key, value in environ.items()
                        if key != "CPK_GRAPH_TOPOLOGY_DATABASE_URL"
                    }
                )

            self.assertEqual(set(config.store_endpoints), set(STORE_ENVIRONMENT))
            self.assertEqual(str(config.runtime_dispatcher), "none")
            self.assertEqual(str(config.ingress_interpreters), "none")
            self.assertEqual(config.product_material_resolver, "none")
            self.assertIsNone(config.material_provider_routes_json)
            self.assertIsNone(config.material_provider_bootstrap_files_json)
            self.assertNotIn("postgres://", repr(config.process_configuration()))
        finally:
            sys.path.remove(str(PRODUCT_SRC))
            for name in list(sys.modules):
                if name == "control_plane_kit_servers_cpk_server" or name.startswith(
                    "control_plane_kit_servers_cpk_server."
                ):
                    sys.modules.pop(name, None)

    def test_authentication_bootstrap_fails_closed_and_redacts_static_credential(
        self,
    ) -> None:
        sys.path.insert(0, str(PRODUCT_SRC))
        try:
            server_module = importlib.import_module(
                "control_plane_kit_servers_cpk_server.server"
            )
            environ = {
                "CPK_SERVER_MODE": "execution-capable",
                "CPK_CONTROL_AUTH_CONFIGURED": "true",
                "CPK_PORT": "8080",
                "CPK_RUNTIME_INTERPRETERS": "none",
                "CPK_WORKPLACE_DATABASE_URL": "postgres://user:pass@db/cpk",
                "CPK_ACTIVITY_HISTORY_DATABASE_URL": "postgres://user:pass@db/cpk",
                "CPK_OBSERVER_STATE_DATABASE_URL": "postgres://user:pass@db/cpk",
                "CPK_GRAPH_TOPOLOGY_DATABASE_URL": "postgres://user:pass@db/cpk",
            }
            unconfigured = (
                server_module.CpkServerBootstrapConfiguration.from_environment(environ)
            )
            with self.assertRaisesRegex(
                server_module.BootstrapConfigurationError,
                "no credential verifier is configured",
            ):
                server_module._credential_verifier(unconfigured)
            with self.assertRaisesRegex(
                server_module.BootstrapConfigurationError,
                "requires CPK_CONTROL_AUTH_VERIFIER=static-development",
            ):
                server_module.CpkServerBootstrapConfiguration.from_environment(
                    {
                        **environ,
                        "CPK_CONTROL_AUTH_STATIC_CREDENTIAL": (
                            "orphaned-credential-not-for-output"
                        ),
                    }
                )
            with self.assertRaisesRegex(
                server_module.BootstrapConfigurationError,
                "bounded and nonempty",
            ):
                server_module.CpkServerBootstrapConfiguration.from_environment(
                    {
                        **environ,
                        "CPK_CONTROL_AUTH_VERIFIER": "static-development",
                        "CPK_CONTROL_AUTH_STATIC_CREDENTIAL": "credential with spaces",
                    }
                )

            with self.assertRaisesRegex(
                server_module.BootstrapConfigurationError,
                "requires CPK_CONTROL_AUTH_STATIC_WORKSPACE_GRANTS_JSON",
            ):
                server_module.CpkServerBootstrapConfiguration.from_environment(
                    {
                        **environ,
                        "CPK_CONTROL_AUTH_VERIFIER": "static-development",
                        "CPK_CONTROL_AUTH_STATIC_CREDENTIAL": (
                            "credential-not-for-output"
                        ),
                    }
                )
            grants_json = json.dumps(
                {
                    "workspace-a": [
                        "hub:instance:create",
                        "instance:workspace:read",
                        "instance:workspace:edit",
                        "plan:request",
                    ]
                }
            )
            configured = server_module.CpkServerBootstrapConfiguration.from_environment(
                {
                    **environ,
                    "CPK_CONTROL_AUTH_VERIFIER": "static-development",
                    "CPK_CONTROL_AUTH_STATIC_CREDENTIAL": "credential-not-for-output",
                    "CPK_CONTROL_AUTH_STATIC_WORKSPACE_GRANTS_JSON": grants_json,
                }
            )
            verifier = server_module._credential_verifier(configured)
            principal = verifier.authenticate(b"credential-not-for-output")
            other_config = (
                server_module.CpkServerBootstrapConfiguration.from_environment(
                    {
                        **environ,
                        "CPK_CONTROL_AUTH_VERIFIER": "static-development",
                        "CPK_CONTROL_AUTH_STATIC_CREDENTIAL": (
                            "different-credential-not-for-output"
                        ),
                        "CPK_CONTROL_AUTH_STATIC_WORKSPACE_GRANTS_JSON": grants_json,
                    }
                )
            )
            other_verifier = server_module._credential_verifier(other_config)

            self.assertEqual(
                principal.identity.subject_id,
                "local-development-operator",
            )
            self.assertEqual(
                tuple(grant.descriptor() for grant in principal.workspace_grants),
                (
                    {
                        "workspace_id": "workspace-a",
                        "scopes": (
                            "hub:instance:create",
                            "instance:workspace:edit",
                            "instance:workspace:read",
                            "plan:request",
                        ),
                    },
                ),
            )
            with self.assertRaisesRegex(
                server_module.BootstrapConfigurationError,
                "wildcard workspace grants are forbidden",
            ):
                server_module.CpkServerBootstrapConfiguration.from_environment(
                    {
                        **environ,
                        "CPK_CONTROL_AUTH_VERIFIER": "static-development",
                        "CPK_CONTROL_AUTH_STATIC_CREDENTIAL": (
                            "credential-not-for-output"
                        ),
                        "CPK_CONTROL_AUTH_STATIC_WORKSPACE_GRANTS_JSON": (
                            '{"*":["instance:workspace:read"]}'
                        ),
                    }
                )
            with self.assertRaisesRegex(
                server_module.BootstrapConfigurationError,
                "unknown policy scope",
            ):
                server_module.CpkServerBootstrapConfiguration.from_environment(
                    {
                        **environ,
                        "CPK_CONTROL_AUTH_VERIFIER": "static-development",
                        "CPK_CONTROL_AUTH_STATIC_CREDENTIAL": (
                            "credential-not-for-output"
                        ),
                        "CPK_CONTROL_AUTH_STATIC_WORKSPACE_GRANTS_JSON": (
                            '{"workspace-a":["admin:*"]}'
                        ),
                    }
                )
            self.assertEqual(configured, other_config)
            self.assertNotEqual(verifier, other_verifier)
            self.assertNotIn("credential-not-for-output", repr(configured))
            self.assertNotIn("credential-not-for-output", repr(verifier))

            principals_json = json.dumps(
                [
                    {
                        "credential": "operator-token-not-for-output",
                        "subject_id": "operator-a",
                        "kind": "operator",
                        "workspace_grants": {
                            "workspace-a": [
                                "instance:workspace:read",
                                "plan:request",
                            ]
                        },
                    },
                    {
                        "credential": "worker-token-not-for-output",
                        "subject_id": "worker-a",
                        "kind": "worker",
                        "workspace_grants": {
                            "workspace-a": [
                                "execution:operate",
                            ]
                        },
                    },
                ]
            )
            multi_config = (
                server_module.CpkServerBootstrapConfiguration.from_environment(
                    {
                        **environ,
                        "CPK_CONTROL_AUTH_VERIFIER": "static-development",
                        "CPK_CONTROL_AUTH_STATIC_PRINCIPALS_JSON": principals_json,
                    }
                )
            )
            multi_verifier = server_module._credential_verifier(multi_config)
            operator = multi_verifier.authenticate(b"operator-token-not-for-output")
            worker = multi_verifier.authenticate(b"worker-token-not-for-output")

            self.assertEqual(operator.identity.kind.value, "operator")
            self.assertEqual(operator.identity.subject_id, "operator-a")
            self.assertEqual(worker.identity.kind.value, "worker")
            self.assertEqual(worker.identity.subject_id, "worker-a")
            self.assertNotIn("operator-token-not-for-output", repr(multi_config))
            self.assertNotIn("worker-token-not-for-output", repr(multi_config))

            with self.assertRaisesRegex(
                server_module.BootstrapConfigurationError,
                "must not be mixed",
            ):
                server_module.CpkServerBootstrapConfiguration.from_environment(
                    {
                        **environ,
                        "CPK_CONTROL_AUTH_VERIFIER": "static-development",
                        "CPK_CONTROL_AUTH_STATIC_CREDENTIAL": (
                            "credential-not-for-output"
                        ),
                        "CPK_CONTROL_AUTH_STATIC_PRINCIPALS_JSON": principals_json,
                    }
                )
        finally:
            sys.path.remove(str(PRODUCT_SRC))
            for name in list(sys.modules):
                if name == "control_plane_kit_servers_cpk_server" or name.startswith(
                    "control_plane_kit_servers_cpk_server."
                ):
                    sys.modules.pop(name, None)

    def test_bootstrap_rejects_legacy_docker_config_secret_resolution(self) -> None:
        sys.path.insert(0, str(PRODUCT_SRC))
        try:
            server_module = importlib.import_module(
                "control_plane_kit_servers_cpk_server.server"
            )
            with self.assertRaisesRegex(
                server_module.BootstrapConfigurationError,
                "legacy Docker credential bootstrap is unavailable",
            ):
                server_module.CpkServerBootstrapConfiguration.from_environment(
                    {
                        "CPK_SERVER_MODE": "execution-capable",
                        "CPK_CONTROL_AUTH_CONFIGURED": "true",
                        "CPK_PORT": "8080",
                        "CPK_RUNTIME_INTERPRETERS": "docker",
                        "CPK_IMAGE_PULL_CREDENTIAL_RESOLVER": "docker-config",
                        "CPK_WORKPLACE_DATABASE_URL": "postgres://user:pass@db/cpk",
                        "CPK_ACTIVITY_HISTORY_DATABASE_URL": "postgres://user:pass@db/cpk",
                        "CPK_OBSERVER_STATE_DATABASE_URL": "postgres://user:pass@db/cpk",
                        "CPK_GRAPH_TOPOLOGY_DATABASE_URL": "postgres://user:pass@db/cpk",
                    }
                )
        finally:
            sys.path.remove(str(PRODUCT_SRC))
            for name in list(sys.modules):
                if name == "control_plane_kit_servers_cpk_server" or name.startswith(
                    "control_plane_kit_servers_cpk_server."
                ):
                    sys.modules.pop(name, None)

    def test_bootstrap_local_product_material_resolver_is_explicit_and_redacted(
        self,
    ) -> None:
        sys.path.insert(0, str(PRODUCT_SRC))
        try:
            server_module = importlib.import_module(
                "control_plane_kit_servers_cpk_server.server"
            )
            config = server_module.CpkServerBootstrapConfiguration.from_environment(
                {
                    "CPK_SERVER_MODE": "execution-capable",
                    "CPK_CONTROL_AUTH_CONFIGURED": "true",
                    "CPK_PORT": "8080",
                    "CPK_RUNTIME_INTERPRETERS": "docker",
                    "CPK_PRODUCT_MATERIAL_RESOLVER": "local-development",
                    "CPK_PRODUCT_SECRET_VALUES_JSON": json.dumps(
                        {
                            "secret://control-plane-kit/postgres/password": (
                                "postgres-secret-not-for-output"
                            ),
                            "secret://cloudflare/openj92/api-token": (
                                "cloudflare-token-not-for-output"
                            ),
                        }
                    ),
                    "CPK_WORKPLACE_DATABASE_URL": "postgres://user:pass@db/cpk",
                    "CPK_ACTIVITY_HISTORY_DATABASE_URL": "postgres://user:pass@db/cpk",
                    "CPK_OBSERVER_STATE_DATABASE_URL": "postgres://user:pass@db/cpk",
                    "CPK_GRAPH_TOPOLOGY_DATABASE_URL": "postgres://user:pass@db/cpk",
                }
            )
            resolver = server_module._secret_provider_composition(
                config
            ).authorized_resolver

            self.assertEqual(
                type(resolver).__name__,
                "_LocalDevelopmentAuthorizedSecretResolver",
            )
            self.assertEqual(
                config.product_material_resolver,
                "local-development",
            )
            self.assertNotIn("postgres-secret-not-for-output", repr(config))
            self.assertNotIn("cloudflare-token-not-for-output", repr(config))
            self.assertNotIn("postgres-secret-not-for-output", repr(resolver))
            self.assertNotIn("cloudflare-token-not-for-output", repr(resolver))
        finally:
            sys.path.remove(str(PRODUCT_SRC))
            for name in list(sys.modules):
                if name == "control_plane_kit_servers_cpk_server" or name.startswith(
                    "control_plane_kit_servers_cpk_server."
                ):
                    sys.modules.pop(name, None)

    def test_bootstrap_product_material_resolver_selection_is_closed(self) -> None:
        sys.path.insert(0, str(PRODUCT_SRC))
        try:
            server_module = importlib.import_module(
                "control_plane_kit_servers_cpk_server.server"
            )
            environ = {
                "CPK_SERVER_MODE": "execution-capable",
                "CPK_CONTROL_AUTH_CONFIGURED": "true",
                "CPK_PORT": "8080",
                "CPK_RUNTIME_INTERPRETERS": "docker",
                "CPK_PRODUCT_MATERIAL_RESOLVER": "env-file",
                "CPK_WORKPLACE_DATABASE_URL": "postgres://user:pass@db/cpk",
                "CPK_ACTIVITY_HISTORY_DATABASE_URL": "postgres://user:pass@db/cpk",
                "CPK_OBSERVER_STATE_DATABASE_URL": "postgres://user:pass@db/cpk",
                "CPK_GRAPH_TOPOLOGY_DATABASE_URL": "postgres://user:pass@db/cpk",
            }

            with self.assertRaisesRegex(
                server_module.BootstrapConfigurationError,
                "CPK_PRODUCT_MATERIAL_RESOLVER must be one of",
            ):
                server_module.CpkServerBootstrapConfiguration.from_environment(environ)
        finally:
            sys.path.remove(str(PRODUCT_SRC))
            for name in list(sys.modules):
                if name == "control_plane_kit_servers_cpk_server" or name.startswith(
                    "control_plane_kit_servers_cpk_server."
                ):
                    sys.modules.pop(name, None)

    def test_gateway_probe_signer_bootstrap_composes_real_bounded_dispatch(
        self,
    ) -> None:
        sys.path.insert(0, str(PRODUCT_SRC))
        try:
            server_module = importlib.import_module(
                "control_plane_kit_servers_cpk_server.server"
            )
            from control_plane_kit_core.gateway_delegation import (
                DelegatedGatewayProbeGrant,
                GatewayProbeCommandKind,
                GatewayProbeRequest,
            )
            from control_plane_kit_core.probe_intents import (
                EndpointContext,
                LiteralEndpointMaterial,
                RuntimeEndpointObservation,
            )
            from control_plane_kit_core.runtime_effects import GatewayTargetId
            from control_plane_kit_core.secrets import (
                SecretProviderEndpointReference,
                SecretReference,
                SecretResolutionGrant,
                SecretResolved,
                SecretUseIntent,
                SecretValue,
            )
            from control_plane_kit_core.types import Protocol
            from control_plane_kit_operations import (
                GatewayProbeAttemptStatus,
                GatewayProbeDispatch,
            )

            private_key = Ed25519PrivateKey.generate()
            private_pem = private_key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.PKCS8,
                encryption_algorithm=serialization.NoEncryption(),
            ).decode("ascii")
            public_pem = private_key.public_key().public_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PublicFormat.SubjectPublicKeyInfo,
            ).decode("ascii")
            key_reference = "secret://control-plane-kit/gateway/signing-key"
            config = server_module.CpkServerBootstrapConfiguration.from_environment(
                {
                    "CPK_SERVER_MODE": "execution-capable",
                    "CPK_CONTROL_AUTH_CONFIGURED": "true",
                    "CPK_PORT": "8080",
                    "CPK_RUNTIME_INTERPRETERS": "none",
                    "CPK_GATEWAY_PROBE_SIGNER": "ed25519",
                    "CPK_GATEWAY_PROBE_SIGNING_KEY_REF": key_reference,
                    "CPK_GATEWAY_PROBE_ISSUER": "urn:cpk:test",
                    "CPK_GATEWAY_PROBE_KEY_ID": "gateway-key-a",
                    "CPK_PRODUCT_MATERIAL_RESOLVER": "provider",
                    "CPK_MATERIAL_PROVIDER_ROUTES_JSON": json.dumps(
                        {"provider-main": "https://secrets.internal.example"}
                    ),
                    "CPK_MATERIAL_PROVIDER_BOOTSTRAP_FILES_JSON": json.dumps(
                        {
                            "secret://bootstrap/provider-token":
                                "/run/secrets/provider-token"
                        }
                    ),
                    "CPK_WORKPLACE_DATABASE_URL": "postgres://user:pass@db/cpk",
                    "CPK_ACTIVITY_HISTORY_DATABASE_URL": "postgres://user:pass@db/cpk",
                    "CPK_OBSERVER_STATE_DATABASE_URL": "postgres://user:pass@db/cpk",
                    "CPK_GRAPH_TOPOLOGY_DATABASE_URL": "postgres://user:pass@db/cpk",
                }
            )
            observed: dict[str, object] = {}

            def handler(inbound: httpx.Request) -> httpx.Response:
                scheme, token = inbound.headers["authorization"].split(
                    " ",
                    maxsplit=1,
                )
                observed["scheme"] = scheme
                observed["claims"] = jwt.decode(
                    token,
                    public_pem,
                    algorithms=["EdDSA"],
                    audience="gateway:workspace-a:gateway-a",
                    issuer="urn:cpk:test",
                )
                return httpx.Response(
                    200,
                    json={
                        "outcome": "passed",
                        "target_id": "hello.http",
                        "probe": "http-status",
                        "status": 200,
                        "body_size": 4,
                    },
                )

            request = GatewayProbeRequest(
                GatewayProbeCommandKind.HTTP_STATUS,
                GatewayTargetId("hello.http"),
                "/health/ready",
            )
            issued_at = int(time.time()) - 1
            grant = DelegatedGatewayProbeGrant(
                issuer="urn:cpk:test",
                key_id="gateway-key-a",
                audience="gateway:workspace-a:gateway-a",
                workspace_id="workspace-a",
                operation_id="probe-a",
                request_id="request-a",
                gateway_node_id="gateway-a",
                probe_kind=request.kind,
                target_id=request.target_id,
                request_digest=request.canonical_digest(),
                issued_at=issued_at,
                expires_at=issued_at + 60,
                jti="jti-a",
            )
            secret_grant = SecretResolutionGrant(
                authorization_id="suse_" + "a" * 64,
                workspace_id="workspace-a",
                reference_registration_id="sref_" + "b" * 64,
                provider_registration_id="sprov_" + "c" * 64,
                endpoint_reference=SecretProviderEndpointReference(
                    "provider-main"
                ),
                credential_reference=SecretReference(
                    "secret://bootstrap/provider-token"
                ),
                reference=SecretReference(key_reference),
                intent=SecretUseIntent.GATEWAY_PROBE_SIGNING_KEY,
                actor_subject="operator-a",
                correlation_id="gateway-probe-a",
                intent_fingerprint="d" * 64,
                operation_id="probe-a",
                probe_id="probe-a",
            )

            observed_resolution_grants = []

            class AuthorizedResolver:
                def resolve(self, received):
                    observed_resolution_grants.append(received)
                    return SecretResolved(
                        SecretReference(key_reference),
                        SecretValue(private_pem),
                    )

            dispatch = server_module._gateway_probe_dispatcher(
                config,
                secret_provider=server_module._SecretProviderComposition(
                    authorized_resolver=AuthorizedResolver()
                ),
                transport=httpx.MockTransport(handler),
            )
            result = dispatch.dispatch(
                GatewayProbeDispatch(
                    grant,
                    request,
                    RuntimeEndpointObservation(
                        "gateway-a",
                        "control",
                        "graph-a",
                        Protocol.HTTP,
                        EndpointContext.RUNTIME_PRIVATE,
                        LiteralEndpointMaterial("http://gateway-a:8000"),
                    ),
                    secret_grant,
                )
            )

            self.assertEqual(observed_resolution_grants, [secret_grant])
            self.assertEqual(config.gateway_probe_signer, "ed25519")
            self.assertEqual(
                config.gateway_probe_signing_key_reference.reference_id,
                key_reference,
            )
            self.assertEqual(observed["scheme"], "CPK-Gateway")
            self.assertEqual(
                observed["claims"]["gateway_probe"],
                grant.descriptor(),
            )
            self.assertIs(result.status, GatewayProbeAttemptStatus.SUCCEEDED)
            self.assertEqual(result.code, "probe-succeeded")
            self.assertEqual(
                result.evidence.descriptor(),
                {
                    "body_size": 4,
                    "http_status": 200,
                    "outcome": "passed",
                    "probe": "http-status",
                    "target_id": "hello.http",
                },
            )
            self.assertNotIn(private_pem, repr(config))
            self.assertNotIn(private_pem, repr(dispatch))
            self.assertNotIn(private_pem, repr(result))
        finally:
            sys.path.remove(str(PRODUCT_SRC))
            for name in list(sys.modules):
                if name == "control_plane_kit_servers_cpk_server" or name.startswith(
                    "control_plane_kit_servers_cpk_server."
                ):
                    sys.modules.pop(name, None)

    def test_gateway_probe_signer_bootstrap_fails_closed_without_authority(
        self,
    ) -> None:
        sys.path.insert(0, str(PRODUCT_SRC))
        try:
            server_module = importlib.import_module(
                "control_plane_kit_servers_cpk_server.server"
            )
            base = {
                "CPK_SERVER_MODE": "execution-capable",
                "CPK_CONTROL_AUTH_CONFIGURED": "true",
                "CPK_PORT": "8080",
                "CPK_RUNTIME_INTERPRETERS": "none",
                "CPK_GATEWAY_PROBE_SIGNER": "ed25519",
                "CPK_GATEWAY_PROBE_SIGNING_KEY_REF": (
                    "secret://control-plane-kit/gateway/signing-key"
                ),
                "CPK_GATEWAY_PROBE_ISSUER": "urn:cpk:test",
                "CPK_GATEWAY_PROBE_KEY_ID": "gateway-key-a",
                "CPK_WORKPLACE_DATABASE_URL": "postgres://user:pass@db/cpk",
                "CPK_ACTIVITY_HISTORY_DATABASE_URL": "postgres://user:pass@db/cpk",
                "CPK_OBSERVER_STATE_DATABASE_URL": "postgres://user:pass@db/cpk",
                "CPK_GRAPH_TOPOLOGY_DATABASE_URL": "postgres://user:pass@db/cpk",
            }

            with self.assertRaisesRegex(
                server_module.BootstrapConfigurationError,
                "requires provider-backed secret resolution",
            ):
                server_module.CpkServerBootstrapConfiguration.from_environment(base)
            with self.assertRaisesRegex(
                server_module.BootstrapConfigurationError,
                "must be one of: none, ed25519",
            ):
                server_module.CpkServerBootstrapConfiguration.from_environment(
                    {
                        **base,
                        "CPK_GATEWAY_PROBE_SIGNER": "home-grown",
                    }
                )
        finally:
            sys.path.remove(str(PRODUCT_SRC))
            for name in list(sys.modules):
                if name == "control_plane_kit_servers_cpk_server" or name.startswith(
                    "control_plane_kit_servers_cpk_server."
                ):
                    sys.modules.pop(name, None)

    def test_bootstrap_runtime_interpreter_selection_is_closed(self) -> None:
        sys.path.insert(0, str(PRODUCT_SRC))
        try:
            server_module = importlib.import_module(
                "control_plane_kit_servers_cpk_server.server"
            )
            environ = {
                "CPK_SERVER_MODE": "execution-capable",
                "CPK_CONTROL_AUTH_CONFIGURED": "true",
                "CPK_PORT": "8080",
                "CPK_RUNTIME_INTERPRETERS": "made-up-runtime",
                "CPK_WORKPLACE_DATABASE_URL": "postgres://user:pass@db/cpk",
                "CPK_ACTIVITY_HISTORY_DATABASE_URL": "postgres://user:pass@db/cpk",
                "CPK_OBSERVER_STATE_DATABASE_URL": "postgres://user:pass@db/cpk",
                "CPK_GRAPH_TOPOLOGY_DATABASE_URL": "postgres://user:pass@db/cpk",
            }

            with self.assertRaisesRegex(
                server_module.BootstrapConfigurationError,
                "runtime dispatcher bootstrap includes an unknown runtime kind",
            ):
                server_module.CpkServerBootstrapConfiguration.from_environment(environ)
        finally:
            sys.path.remove(str(PRODUCT_SRC))
            for name in list(sys.modules):
                if name == "control_plane_kit_servers_cpk_server" or name.startswith(
                    "control_plane_kit_servers_cpk_server."
                ):
                    sys.modules.pop(name, None)

    def test_bootstrap_ingress_interpreter_selection_is_closed(self) -> None:
        sys.path.insert(0, str(PRODUCT_SRC))
        try:
            server_module = importlib.import_module(
                "control_plane_kit_servers_cpk_server.server"
            )
            environ = {
                "CPK_SERVER_MODE": "execution-capable",
                "CPK_CONTROL_AUTH_CONFIGURED": "true",
                "CPK_PORT": "8080",
                "CPK_RUNTIME_INTERPRETERS": "none",
                "CPK_INGRESS_INTERPRETERS": "made-up-provider",
                "CPK_WORKPLACE_DATABASE_URL": "postgres://user:pass@db/cpk",
                "CPK_ACTIVITY_HISTORY_DATABASE_URL": "postgres://user:pass@db/cpk",
                "CPK_OBSERVER_STATE_DATABASE_URL": "postgres://user:pass@db/cpk",
                "CPK_GRAPH_TOPOLOGY_DATABASE_URL": "postgres://user:pass@db/cpk",
            }

            with self.assertRaisesRegex(
                server_module.BootstrapConfigurationError,
                "ingress interpreter bootstrap includes an unknown provider kind",
            ):
                server_module.CpkServerBootstrapConfiguration.from_environment(environ)
        finally:
            sys.path.remove(str(PRODUCT_SRC))
            for name in list(sys.modules):
                if name == "control_plane_kit_servers_cpk_server" or name.startswith(
                    "control_plane_kit_servers_cpk_server."
                ):
                    sys.modules.pop(name, None)

    def test_cloudflare_ingress_bootstrap_requires_secret_resolver(self) -> None:
        sys.path.insert(0, str(PRODUCT_SRC))
        try:
            server_module = importlib.import_module(
                "control_plane_kit_servers_cpk_server.server"
            )
            environ = {
                "CPK_SERVER_MODE": "execution-capable",
                "CPK_CONTROL_AUTH_CONFIGURED": "true",
                "CPK_PORT": "8080",
                "CPK_RUNTIME_INTERPRETERS": "docker",
                "CPK_INGRESS_INTERPRETERS": "cloudflare",
                "CPK_WORKPLACE_DATABASE_URL": "postgres://user:pass@db/cpk",
                "CPK_ACTIVITY_HISTORY_DATABASE_URL": "postgres://user:pass@db/cpk",
                "CPK_OBSERVER_STATE_DATABASE_URL": "postgres://user:pass@db/cpk",
                "CPK_GRAPH_TOPOLOGY_DATABASE_URL": "postgres://user:pass@db/cpk",
            }

            with self.assertRaisesRegex(
                server_module.BootstrapConfigurationError,
                "CPK_INGRESS_INTERPRETERS=cloudflare requires provider-backed",
            ):
                server_module.CpkServerBootstrapConfiguration.from_environment(environ)
        finally:
            sys.path.remove(str(PRODUCT_SRC))
            for name in list(sys.modules):
                if name == "control_plane_kit_servers_cpk_server" or name.startswith(
                    "control_plane_kit_servers_cpk_server."
                ):
                    sys.modules.pop(name, None)

    def test_cloudflare_ingress_bootstrap_composes_explicit_provider_adapter(
        self,
    ) -> None:
        sys.path.insert(0, str(PRODUCT_SRC))
        try:
            server_module = importlib.import_module(
                "control_plane_kit_servers_cpk_server.server"
            )
            config = server_module.CpkServerBootstrapConfiguration.from_environment(
                {
                    "CPK_SERVER_MODE": "execution-capable",
                    "CPK_CONTROL_AUTH_CONFIGURED": "true",
                    "CPK_PORT": "8080",
                    "CPK_RUNTIME_INTERPRETERS": "docker",
                    "CPK_INGRESS_INTERPRETERS": "cloudflare",
                    "CPK_PRODUCT_MATERIAL_RESOLVER": "provider",
                    "CPK_MATERIAL_PROVIDER_ROUTES_JSON": json.dumps(
                        {"provider-main": "https://secrets.internal.example"}
                    ),
                    "CPK_MATERIAL_PROVIDER_BOOTSTRAP_FILES_JSON": json.dumps(
                        {
                            "secret://bootstrap/provider-token":
                                "/run/secrets/provider-token"
                        }
                    ),
                    "CPK_WORKPLACE_DATABASE_URL": "postgres://user:pass@db/cpk",
                    "CPK_ACTIVITY_HISTORY_DATABASE_URL": "postgres://user:pass@db/cpk",
                    "CPK_OBSERVER_STATE_DATABASE_URL": "postgres://user:pass@db/cpk",
                    "CPK_GRAPH_TOPOLOGY_DATABASE_URL": "postgres://user:pass@db/cpk",
                }
            )

            class Authorizer:
                def authorize_resolution(self, _command):
                    raise AssertionError("composition test performs no secret use")

            adapter = server_module._activity_adapter(
                config,
                lambda: None,
                Authorizer(),
                server_module._secret_provider_composition(config),
            )

            self.assertEqual(str(config.ingress_interpreters), "cloudflare")
            self.assertEqual(type(adapter).__name__, "_CompositeExecutionAdapter")
            self.assertNotIn("provider-token", repr(adapter))
        finally:
            sys.path.remove(str(PRODUCT_SRC))
            for name in list(sys.modules):
                if name == "control_plane_kit_servers_cpk_server" or name.startswith(
                    "control_plane_kit_servers_cpk_server."
                ):
                    sys.modules.pop(name, None)

    def test_bootstrap_closed_runtime_without_provider_fails_at_adapter_boundary(
        self,
    ) -> None:
        sys.path.insert(0, str(PRODUCT_SRC))
        try:
            server_module = importlib.import_module(
                "control_plane_kit_servers_cpk_server.server"
            )

            config = server_module.CpkServerBootstrapConfiguration.from_environment(
                {
                    "CPK_SERVER_MODE": "execution-capable",
                    "CPK_CONTROL_AUTH_CONFIGURED": "true",
                    "CPK_PORT": "8080",
                    "CPK_RUNTIME_INTERPRETERS": "aws",
                    "CPK_WORKPLACE_DATABASE_URL": "postgres://user:pass@db/cpk",
                    "CPK_ACTIVITY_HISTORY_DATABASE_URL": "postgres://user:pass@db/cpk",
                    "CPK_OBSERVER_STATE_DATABASE_URL": "postgres://user:pass@db/cpk",
                    "CPK_GRAPH_TOPOLOGY_DATABASE_URL": "postgres://user:pass@db/cpk",
                }
            )

            with self.assertRaisesRegex(
                server_module.BootstrapConfigurationError,
                "no runtime interpreter provider is available for 'aws'",
            ):
                server_module._runtime_adapter(config)
        finally:
            sys.path.remove(str(PRODUCT_SRC))
            for name in list(sys.modules):
                if name == "control_plane_kit_servers_cpk_server" or name.startswith(
                    "control_plane_kit_servers_cpk_server."
                ):
                    sys.modules.pop(name, None)

    def test_bootstrap_uses_operations_runtime_dispatcher_language(self) -> None:
        sys.path.insert(0, str(PRODUCT_SRC))
        try:
            server_module = importlib.import_module(
                "control_plane_kit_servers_cpk_server.server"
            )
            from control_plane_kit_core.types import RuntimeKind
            from control_plane_kit_operations import (
                RuntimeDispatcherBootstrapConfiguration,
            )

            config = server_module.CpkServerBootstrapConfiguration.from_environment(
                {
                    "CPK_SERVER_MODE": "execution-capable",
                    "CPK_CONTROL_AUTH_CONFIGURED": "true",
                    "CPK_PORT": "8080",
                    "CPK_RUNTIME_INTERPRETERS": "docker",
                    "CPK_WORKPLACE_DATABASE_URL": "postgres://user:pass@db/cpk",
                    "CPK_ACTIVITY_HISTORY_DATABASE_URL": "postgres://user:pass@db/cpk",
                    "CPK_OBSERVER_STATE_DATABASE_URL": "postgres://user:pass@db/cpk",
                    "CPK_GRAPH_TOPOLOGY_DATABASE_URL": "postgres://user:pass@db/cpk",
                }
            )

            self.assertIsInstance(
                config.runtime_dispatcher,
                RuntimeDispatcherBootstrapConfiguration,
            )
            self.assertEqual(config.runtime_dispatcher.runtime_kinds, (RuntimeKind.DOCKER,))
            self.assertEqual(str(config.runtime_dispatcher), "docker")
            self.assertEqual(str(config.ingress_interpreters), "none")
            self.assertNotIn("authority", repr(config.runtime_dispatcher).lower())
        finally:
            sys.path.remove(str(PRODUCT_SRC))
            for name in list(sys.modules):
                if name == "control_plane_kit_servers_cpk_server" or name.startswith(
                    "control_plane_kit_servers_cpk_server."
                ):
                    sys.modules.pop(name, None)

    def test_runtime_provider_import_is_lazy_when_dispatch_is_disabled(self) -> None:
        sys.path.insert(0, str(PRODUCT_SRC))
        try:
            sys.modules.pop("control_plane_kit_interpreters.docker", None)
            server_module = importlib.import_module(
                "control_plane_kit_servers_cpk_server.server"
            )
            sys.modules.pop("control_plane_kit_interpreters.docker", None)
            config = server_module.CpkServerBootstrapConfiguration.from_environment(
                {
                    "CPK_SERVER_MODE": "execution-capable",
                    "CPK_CONTROL_AUTH_CONFIGURED": "true",
                    "CPK_PORT": "8080",
                    "CPK_RUNTIME_INTERPRETERS": "none",
                    "CPK_WORKPLACE_DATABASE_URL": "postgres://user:pass@db/cpk",
                    "CPK_ACTIVITY_HISTORY_DATABASE_URL": "postgres://user:pass@db/cpk",
                    "CPK_OBSERVER_STATE_DATABASE_URL": "postgres://user:pass@db/cpk",
                    "CPK_GRAPH_TOPOLOGY_DATABASE_URL": "postgres://user:pass@db/cpk",
                }
            )

            adapter = server_module._runtime_adapter(config)

            self.assertEqual(type(adapter).__name__, "_UnsupportedExecutionAdapter")
            self.assertNotIn("control_plane_kit_interpreters.docker", sys.modules)
        finally:
            sys.path.remove(str(PRODUCT_SRC))
            for name in list(sys.modules):
                if name == "control_plane_kit_servers_cpk_server" or name.startswith(
                    "control_plane_kit_servers_cpk_server."
                ):
                    sys.modules.pop(name, None)

    def test_docker_runtime_bootstrap_defers_docker_client_until_authority_execution(
        self,
    ) -> None:
        source = (
            PRODUCT_SRC / "control_plane_kit_servers_cpk_server" / "server.py"
        ).read_text(encoding="utf-8")

        self.assertIn("DockerLocalAmbientClientConfig", source)
        self.assertIn("DockerSdkClient.from_authority", source)
        self.assertIn("connect_on_init=False", source)
        self.assertNotIn("DockerSdkClient(),", source)

    def test_concrete_provider_imports_are_confined_to_bootstrap_functions(self) -> None:
        tree = ast.parse(SERVER_SOURCE.read_text(encoding="utf-8"))
        function_stack: list[str] = []
        violations: list[tuple[str, str, int]] = []

        class ImportVisitor(ast.NodeVisitor):
            def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
                function_stack.append(node.name)
                self.generic_visit(node)
                function_stack.pop()

            def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
                function_stack.append(node.name)
                self.generic_visit(node)
                function_stack.pop()

            def visit_Import(self, node: ast.Import) -> None:
                for alias in node.names:
                    self._record(alias.name.split(".", 1)[0], node.lineno)

            def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
                if node.module:
                    self._record(node.module.split(".", 1)[0], node.lineno)

            def _record(self, root: str, line: int) -> None:
                owner = function_stack[-1] if function_stack else "<module>"
                if (
                    root in CONCRETE_PROVIDER_IMPORT_ROOTS
                    and owner not in APPROVED_PROVIDER_FUNCTIONS
                ):
                    violations.append((owner, root, line))

        ImportVisitor().visit(tree)

        self.assertEqual(violations, [])

    def test_ready_route_reports_capability_not_authority_material(self) -> None:
        source = SERVER_SOURCE.read_text(encoding="utf-8")
        ready_start = source.index("@app.get(\"/health/ready\")")
        ready_end = source.index("@app.post(\"/mcp\")")
        ready_source = source[ready_start:ready_end].lower()

        self.assertIn("runtime_interpreters", ready_source)
        self.assertIn("ingress_interpreters", ready_source)
        self.assertIn("material_provider", ready_source)
        self.assertIn("development-fixture", ready_source)
        for forbidden in (
            "store_endpoints",
            "docker_config_path",
            "product_secret_values_json",
            "cpk_product_secret_values_json",
            "cpk_docker_auth_config",
            "docker_config",
            "credential",
            "secret",
            "token",
            "tls",
            "endpoint",
            "socket",
        ):
            self.assertNotIn(forbidden, ready_source)

    def test_hosted_process_is_fastapi_over_operations_boundary(self) -> None:
        source = SERVER_SOURCE.read_text(encoding="utf-8")

        self.assertIn("from fastapi import FastAPI, Request", source)
        self.assertIn("uvicorn.run", source)
        self.assertIn("CpkServerOperationsApplication", source)
        self.assertIn("cpk_server_services", source)
        self.assertIn("PostgresUnitOfWork", source)
        self.assertIn("WorkspaceCommandService", source)
        self.assertIn("ProductRegistrationService", source)
        self.assertIn("ImagePullAuthorityRegistrationService", source)
        self.assertIn("RuntimeAuthorityRegistrationService", source)
        self.assertIn("runtime_authorities=RuntimeAuthorityRegistrationService", source)
        self.assertIn("DesiredGraphCommandService", source)
        self.assertIn("OperationCommandService", source)
        self.assertIn("CurrentGraphAdvancementCommandService", source)
        self.assertIn("RuntimeDispatcherBootstrapConfiguration", source)
        self.assertIn("RuntimeInterpreterDispatcher", source)
        self.assertIn("IngressRealizationAdapter", source)
        self.assertIn("IngressAuthorityRegistrationService", source)
        self.assertIn("SecretProviderRegistrationService", source)
        self.assertIn(
            "secret_providers=SecretProviderRegistrationService",
            source,
        )
        self.assertIn("GatewayProbeCommandService", source)
        self.assertIn("gateway_probes=_gateway_probe_service", source)
        self.assertIn("control_plane_kit_interpreters.docker", source)
        self.assertIn("control_plane_kit_interpreters.cloudflare", source)
        self.assertIn("control_plane_kit_interpreters.probes", source)
        self.assertIn("allocation_name=allocation_name", source)
        self.assertIn("CPK_RUNTIME_INTERPRETERS", source)
        self.assertIn("CPK_INGRESS_INTERPRETERS", source)
        self.assertNotIn("BaseHTTPRequestHandler", source)
        self.assertNotIn("ThreadingHTTPServer", source)
        self.assertNotIn("_DemoService", source)
        self.assertNotIn("import docker", source)

    def test_product_descriptor_is_now_published_contract_data(self) -> None:
        descriptor = json.loads((PRODUCT / "product.cpk.json").read_text(encoding="utf-8"))

        self.assertEqual(descriptor["schema"], "control-plane-kit.product")
        self.assertEqual(descriptor["product"]["identity"]["name"], "cpk-server")
        self.assertNotIn("publishing_issue", descriptor)

    def test_host_side_smoke_script_builds_runs_and_cleans_owned_image(self) -> None:
        smoke = (ROOT / "scripts" / "cpk_server_image_smoke.sh").read_text(
            encoding="utf-8"
        )

        self.assertIn("localhost/control-plane-kit-servers/cpk-server:local", smoke)
        self.assertIn("docker build", smoke)
        self.assertIn("postgres:16-alpine", smoke)
        self.assertIn("products/cpk_server/Dockerfile", smoke)
        self.assertIn("docker run", smoke)
        self.assertIn("CPK_WORKPLACE_DATABASE_URL", smoke)
        self.assertIn("CPK_ACTIVITY_HISTORY_DATABASE_URL", smoke)
        self.assertIn("CPK_OBSERVER_STATE_DATABASE_URL", smoke)
        self.assertIn("CPK_GRAPH_TOPOLOGY_DATABASE_URL", smoke)
        self.assertIn("CPK_RUNTIME_INTERPRETERS", smoke)
        self.assertIn("/health/live", smoke)
        self.assertIn("/health/ready", smoke)
        self.assertIn("/workspaces", smoke)
        self.assertIn("/products/import", smoke)
        self.assertIn("/sessions", smoke)
        self.assertIn("/graphs/desired", smoke)
        self.assertIn("products/hello_server/product.cpk.json", smoke)
        self.assertIn("/mcp", smoke)
        self.assertIn("Mcp-Method: resources/read", smoke)
        self.assertIn("Mcp-Method: tools/call", smoke)
        self.assertIn("ready response leaked store endpoint", smoke)
        self.assertIn("org.openj92.project=control-plane-kit-servers", smoke)
        self.assertIn("docker rm -f", smoke)
        self.assertIn("docker network rm", smoke)
        self.assertNotIn("docker system prune", smoke)
        self.assertNotIn("docker volume prune", smoke)

    def test_published_image_smoke_uses_ghcr_digest_without_rebuilding(self) -> None:
        smoke = (ROOT / "scripts" / "cpk_server_published_image_smoke.sh").read_text(
            encoding="utf-8"
        )

        self.assertIn("products/cpk_server/product.cpk.json", smoke)
        self.assertIn("@{image['digest']}", smoke)
        self.assertIn("docker pull", smoke)
        self.assertIn("CPK_SERVER_BUILD_IMAGE=0", smoke)
        self.assertIn("scripts/cpk_server_image_smoke.sh", smoke)
        self.assertNotIn("docker build", smoke)

    def test_hosted_activity_smoke_uses_published_image_and_docker_runtime_authority(
        self,
    ) -> None:
        smoke = (ROOT / "scripts" / "cpk_server_hosted_activity_smoke.sh").read_text(
            encoding="utf-8"
        )

        self.assertIn("products/cpk_server/product.docker.cpk.json", smoke)
        self.assertIn("products/cpk_server/product.docker-cloudflare.cpk.json", smoke)
        self.assertIn("@{image['digest']}", smoke)
        self.assertIn("docker pull", smoke)
        self.assertIn("CPK_RUNTIME_INTERPRETERS=docker", smoke)
        self.assertIn("CPK_INGRESS_INTERPRETERS", smoke)
        self.assertIn('INGRESS_INTERPRETERS="cloudflare"', smoke)
        self.assertIn('IMAGE_PULL_RESOLVER="docker-config"', smoke)
        self.assertIn('CPK_IMAGE_PULL_CREDENTIAL_RESOLVER="$IMAGE_PULL_RESOLVER"', smoke)
        self.assertIn("CPK_HOSTED_ACTIVITY_REGISTER_PULL_AUTHORITY", smoke)
        self.assertIn("CPK_HOSTED_ACTIVITY_SCENARIO", smoke)
        self.assertIn('NEEDS_INGRESS=1', smoke)
        self.assertIn("public-gateway-ingress|public-gateway-toggle|", smoke)
        self.assertIn("workspace-a-router-transition|", smoke)
        self.assertIn(
            "workspace-b-multiplexer-observer|workspace-c-postgres-retained-data|",
            smoke,
        )
        self.assertIn(
            "seeded-stress-public-ingress)",
            smoke,
        )
        self.assertIn("CPK_CLOUDFLARE_ENV_FILE", smoke)
        self.assertIn("OPENJ92_CLOUDFLARE_ACCOUNT_ID", smoke)
        self.assertIn("OPENJ92_CLOUDFLARE_ZONE_ID", smoke)
        self.assertIn("OPENJ92_CLOUDFLARE_API_TOKEN", smoke)
        self.assertIn("CPK_DOCKER_SOCKET_GROUP", smoke)
        self.assertIn("CPK_DOCKER_AUTH_CONFIG", smoke)
        self.assertIn("CPK_PRODUCT_MATERIAL_RESOLVER=local-development", smoke)
        self.assertIn("CPK_PRODUCT_SECRET_VALUES_JSON", smoke)
        self.assertIn("secret://control-plane-kit/postgres/password", smoke)
        self.assertIn("gh auth token", smoke)
        self.assertIn("auths", smoke)
        self.assertIn("DOCKER_CONFIG=/tmp/cpk-docker-config", smoke)
        self.assertIn("chmod 0444", smoke)
        self.assertIn("--group-add", smoke)
        self.assertIn("/var/run/docker.sock:/var/run/docker.sock", smoke)
        self.assertIn("postgres:16-alpine", smoke)
        self.assertIn("python scripts/cpk_server_hosted_activity.py", smoke)
        self.assertIn("org.openj92.cpk.workspace=cpk-hosted-activity-basic", smoke)
        self.assertIn('WORKSPACE_LABEL_KEY="org.openj92.cpk.workspace"', smoke)
        self.assertIn("workspace-a-router|workspace-b-multiplexer|", smoke)
        self.assertIn("workspace-c-postgres|workspace-d-negative-cleanup)", smoke)
        self.assertIn('docker ps -aq --filter "label=$WORKSPACE_LABEL_KEY"', smoke)
        self.assertIn('docker volume ls -q --filter "label=$WORKSPACE_LABEL_KEY"', smoke)
        self.assertIn('docker network ls -q --filter "label=$WORKSPACE_LABEL_KEY"', smoke)
        self.assertIn("docker rm -f", smoke)
        self.assertIn("docker network rm", smoke)
        self.assertNotIn("docker system prune", smoke)
        self.assertNotIn("docker volume prune", smoke)

    def test_hosted_activity_controller_drives_public_workflow_over_http_and_mcp(
        self,
    ) -> None:
        controller = (ROOT / "scripts" / "cpk_server_hosted_activity.py").read_text(
            encoding="utf-8"
        )

        self.assertIn("command.deployment.plan", controller)
        self.assertIn("/image-pull-authorities", controller)
        self.assertIn("command.approval.decide", controller)
        self.assertIn("run_approved_transition", controller)
        self.assertIn("secret://docker-config/ghcr.io", controller)
        self.assertIn("read.pending-approvals", controller)
        self.assertIn("read.approval-detail", controller)
        self.assertIn("class HostedWorkflow", controller)
        self.assertIn("HostedTransitionResult", controller)
        self.assertIn("def run_approved_transition", controller)
        self.assertIn("def request_approval", controller)
        self.assertIn("def assert_approval_visible", controller)
        self.assertIn("def advance_current_graph", controller)
        self.assertIn("/plans/{plan_id}/approval", controller)
        self.assertIn("/runs/{run_id}/advance-current-graph", controller)
        self.assertIn("self.workspace_id", controller)
        self.assertIn("self.worker_id", controller)
        self.assertIn("ProductDescriptorCodec", controller)
        self.assertIn("DEFAULT_GRAPH_CODEC.encode", controller)
        self.assertIn("DockerRuntime(", controller)
        self.assertIn("SocketConnection(", controller)
        self.assertIn("http_active_router", controller)
        self.assertIn("router-transition", controller)
        self.assertIn('"HELLO_MESSAGE": message', controller)
        self.assertIn('"router"', controller)
        self.assertIn('"active"', controller)
        self.assertIn("http://router:8000/", controller)
        self.assertNotIn('"ACTIVE_TARGET_URL"', controller)
        self.assertIn("timeout=60", controller)
        self.assertIn("network.connect", controller)
        self.assertIn("runtime_interpreters", controller)
        self.assertIn("http://hello:8000/", controller)
        self.assertIn('"public-gateway-ingress"', controller)
        self.assertIn('"public-gateway-toggle"', controller)
        self.assertIn("def _public_gateway_overlay", controller)
        self.assertIn("def _named_public_gateway_ingress", controller)
        self.assertIn("command.ingress-authority.register", controller)
        self.assertIn("read.ingress-authority-detail", controller)
        self.assertIn('"authority_ref": OPENJ92_INGRESS_AUTHORITY_REF', controller)
        self.assertIn("PolicyScope.INGRESS_AUTHORITY_READ.value", controller)
        self.assertIn("NamedPublicIngress(", controller)
        self.assertIn("PublicIngressTarget(target_node_id, target_provider_socket)", controller)
        self.assertIn('"cloudflared_connector"', controller)
        self.assertIn('"cloudflared-gateway"', controller)
        self.assertIn("cpk-gateway-001.openj92.dev", controller)
        self.assertIn("def _assert_public_gateway_http_probe", controller)
        self.assertIn("def _assert_public_gateway_postgres_query_ready", controller)
        self.assertIn("def _assert_public_gateway_unreachable", controller)
        self.assertIn("PUBLIC_GATEWAY_READY_ATTEMPTS = 60", controller)
        self.assertIn("PUBLIC_GATEWAY_READY_RETRY_SECONDS = 2", controller)
        self.assertIn("for attempt in range(PUBLIC_GATEWAY_READY_ATTEMPTS):", controller)
        self.assertIn("attempt < PUBLIC_GATEWAY_READY_ATTEMPTS - 1", controller)
        self.assertIn("time.sleep(PUBLIC_GATEWAY_READY_RETRY_SECONDS)", controller)
        self.assertIn('"1.1.1.1"', controller)
        self.assertIn('hostname="cloudflare-dns.com"', controller)
        self.assertIn("server_hostname=hostname", controller)
        self.assertIn("secret://cloudflare/openj92/api-token", controller)
        self.assertNotIn("CpkServerOperationsApplication", controller)
        self.assertNotIn("PostgresUnitOfWork", controller)
        self.assertNotIn("DockerRuntimeInterpreter", controller)

    def test_hosted_activity_controller_proves_public_gateway_toggle_overlay(
        self,
    ) -> None:
        controller = (ROOT / "scripts" / "cpk_server_hosted_activity.py").read_text(
            encoding="utf-8"
        )

        self.assertIn('"public-gateway-toggle"', controller)
        self.assertIn("def _run_public_gateway_toggle", controller)
        self.assertIn("Hosted public gateway toggle on", controller)
        self.assertIn("Hosted public gateway toggle off", controller)
        self.assertIn("Hosted public gateway toggle on again", controller)
        self.assertIn("public_graph = _public_gateway_ingress_graph", controller)
        self.assertIn("private_graph = _single_hello_graph", controller)
        self.assertIn("graph=private_graph", controller)
        self.assertIn("_assert_public_gateway_unreachable(PUBLIC_GATEWAY_HOSTNAME)", controller)
        self.assertIn('_assert_body("http://hello:8000/", "Hello through public ingress\\n")', controller)
        self.assertIn("graph=public_graph", controller)
        self.assertIn("_disconnect_runtime_networks(workflow.server_container, workspace_id=workflow.workspace_id)", controller)
        self.assertIn("graph=DeploymentGraph(workflow.workspace_id)", controller)
        self.assertNotIn("DockerRuntimeInterpreter", controller)

    def test_hosted_activity_controller_supports_multi_workspace_stress_harness(
        self,
    ) -> None:
        controller = (ROOT / "scripts" / "cpk_server_hosted_activity.py").read_text(
            encoding="utf-8"
        )

        self.assertIn("WORKSPACE_IDS = (", controller)
        self.assertIn('"workspace-a-router"', controller)
        self.assertIn('"workspace-b-multiplexer"', controller)
        self.assertIn('"workspace-c-postgres"', controller)
        self.assertIn('"workspace-d-negative-cleanup"', controller)
        self.assertIn('"multi-workspace-foundation"', controller)
        self.assertIn('"seeded-stress-public-ingress"', controller)
        self.assertIn("def _run_seeded_stress_public_ingress", controller)
        self.assertIn("def _run_negative_cleanup", controller)
        self.assertIn("def _workflow_for", controller)
        self.assertIn("def _bootstrap_workspace", controller)
        self.assertIn("def register_local_docker_authority", controller)
        self.assertIn("def register_local_docker_delivery", controller)
        self.assertIn("command.runtime-authority.register", controller)
        self.assertIn("command.runtime-authority-delivery.register", controller)
        self.assertIn('"kind": "local-docker-socket"', controller)
        self.assertIn('"delivery_kind": "local-docker-socket-mount"', controller)
        self.assertIn("RuntimeAuthorityReference(LOCAL_DOCKER_AUTHORITY_REF)", controller)
        self.assertIn("authority_ref=RuntimeAuthorityReference", controller)
        self.assertIn("CPK_HOSTED_ACTIVITY_WORKSPACE_ID", controller)
        self.assertIn("register_runtime_authority", controller)
        self.assertIn("register_runtime_delivery", controller)
        self.assertIn("workflow.create_workspace", controller)
        self.assertIn("workflow.import_product", controller)
        self.assertIn("run_approved_transition", controller)
        self.assertIn("request_approval", controller)
        self.assertIn("assert_approval_visible", controller)
        self.assertIn("advance_current_graph", controller)
        self.assertNotIn("CpkServerOperationsApplication", controller)
        self.assertNotIn("PostgresUnitOfWork", controller)
        self.assertNotIn("DockerRuntimeInterpreter", controller)

    def test_hosted_activity_controller_proves_seeded_stress_public_ingress(
        self,
    ) -> None:
        controller = (ROOT / "scripts" / "cpk_server_hosted_activity.py").read_text(
            encoding="utf-8"
        )

        self.assertIn('"seeded-stress-public-ingress"', controller)
        self.assertIn("def _run_seeded_stress_public_ingress", controller)
        self.assertIn("workspace_id=\"workspace-a-router\"", controller)
        self.assertIn("workspace_id=\"workspace-b-multiplexer\"", controller)
        self.assertIn("workspace_id=\"workspace-c-postgres\"", controller)
        self.assertIn("workspace_id=\"workspace-d-negative-cleanup\"", controller)
        self.assertIn("_run_router_transition(", controller)
        self.assertIn("_run_multiplexer_observer(", controller)
        self.assertIn("_run_postgres_retained_data(", controller)
        self.assertIn("_run_negative_cleanup(", controller)
        self.assertIn("Hosted negative cleanup deploy", controller)
        self.assertIn("Hosted negative cleanup teardown", controller)
        self.assertIn("_assert_public_gateway_private_probe(PUBLIC_GATEWAY_HOSTNAME)", controller)
        self.assertIn("_assert_public_gateway_unreachable(PUBLIC_GATEWAY_HOSTNAME)", controller)
        self.assertIn("_assert_no_runtime_networks(workflow.workspace_id)", controller)
        self.assertIn("_required_env(\"OPENJ92_CLOUDFLARE_API_TOKEN\")", controller)
        self.assertNotIn("DockerRuntimeInterpreter", controller)

        smoke = (
            ROOT / "scripts" / "cpk_server_seeded_stress_public_ingress_smoke.sh"
        ).read_text(encoding="utf-8")
        self.assertIn("CPK_HOSTED_ACTIVITY_SCENARIO=seeded-stress-public-ingress", smoke)
        self.assertIn("scripts/cpk_server_hosted_activity_smoke.sh", smoke)

    def test_hosted_activity_controller_proves_workspace_a_router_transition(
        self,
    ) -> None:
        controller = (ROOT / "scripts" / "cpk_server_hosted_activity.py").read_text(
            encoding="utf-8"
        )

        self.assertIn('"workspace-a-router-transition"', controller)
        self.assertIn('workspace_id="workspace-a-router"', controller)
        self.assertIn('_product_document(servers_repo, "cpk_local_gateway")', controller)
        self.assertIn('_product_document(servers_repo, "cloudflared_connector")', controller)
        self.assertIn("workflow.register_cloudflare_ingress_authority()", controller)
        self.assertIn('"Hello from blue"', controller)
        self.assertIn('"Hello from green"', controller)
        self.assertIn('SocketConnection("router", "internal", "gateway", "target-http")', controller)
        self.assertIn(
            '_assert_public_gateway_http_probe(PUBLIC_GATEWAY_HOSTNAME, "router.internal")',
            controller,
        )
        self.assertIn('_assert_body("http://router:8000/", "Hello from blue\\n")', controller)
        self.assertIn('_assert_body("http://router:8000/", "Hello from green\\n")', controller)
        self.assertIn('_assert_activity_mentions(workflow, blue.run_id, "hello-blue")', controller)
        self.assertIn('_assert_activity_mentions(workflow, blue.run_id, "router")', controller)
        self.assertIn('_assert_activity_mentions(workflow, green.run_id, "hello-green")', controller)
        self.assertIn('_assert_activity_mentions(workflow, green.run_id, "router")', controller)
        self.assertIn('title="Hosted router teardown"', controller)
        self.assertIn("graph=DeploymentGraph(workflow.workspace_id)", controller)
        self.assertIn(
            "_disconnect_runtime_networks(workflow.server_container, workspace_id=workflow.workspace_id)",
            controller,
        )
        self.assertIn('_assert_activity_mentions(workflow, removed.run_id, "cloudflared-gateway")', controller)
        self.assertIn("read.activity", controller)
        self.assertIn("step_succeeded", controller)
        self.assertIn('"HELLO_MESSAGE": message', controller)
        self.assertIn("SocketConnection(", controller)
        self.assertNotIn('"ACTIVE_TARGET_URL"', controller)
        self.assertNotIn("DockerRuntimeInterpreter", controller)

        smoke = (
            ROOT / "scripts" / "cpk_server_workspace_a_router_transition_smoke.sh"
        ).read_text(encoding="utf-8")
        self.assertIn("CPK_HOSTED_ACTIVITY_SCENARIO=workspace-a-router-transition", smoke)
        self.assertIn("scripts/cpk_server_hosted_activity_smoke.sh", smoke)

    def test_hosted_activity_controller_proves_workspace_b_multiplexer_observer(
        self,
    ) -> None:
        controller = (ROOT / "scripts" / "cpk_server_hosted_activity.py").read_text(
            encoding="utf-8"
        )

        self.assertIn('"workspace-b-multiplexer-observer"', controller)
        self.assertIn('workspace_id="workspace-b-multiplexer"', controller)
        self.assertIn("def _run_multiplexer_observer", controller)
        self.assertIn("def _multiplexer_graph", controller)
        self.assertIn('_product_document(servers_repo, "cpk_local_gateway")', controller)
        self.assertIn('_product_document(servers_repo, "cloudflared_connector")', controller)
        self.assertIn("workflow.register_cloudflare_ingress_authority()", controller)
        self.assertIn('"hello-primary"', controller)
        self.assertIn('"hello-observer"', controller)
        self.assertIn('"multiplexer"', controller)
        self.assertIn('"Primary response"', controller)
        self.assertIn('"Observer response"', controller)
        self.assertIn(
            'SocketConnection("hello-primary", "internal", "multiplexer", "primary")',
            controller,
        )
        self.assertIn('"observer-a"', controller)
        self.assertIn('SocketConnection("multiplexer", "internal", "gateway", "target-http")', controller)
        self.assertIn(
            '_assert_public_gateway_http_probe(PUBLIC_GATEWAY_HOSTNAME, "multiplexer.internal")',
            controller,
        )
        self.assertIn('_assert_body("http://multiplexer:8000/", "Primary response\\n")', controller)
        self.assertIn(
            '_assert_observer_receipt("http://hello-observer:8000/observations/requests")',
            controller,
        )
        self.assertIn('_assert_activity_mentions(workflow, result.run_id, "hello-primary")', controller)
        self.assertIn('_assert_activity_mentions(workflow, result.run_id, "hello-observer")', controller)
        self.assertIn('_assert_activity_mentions(workflow, result.run_id, "multiplexer")', controller)
        self.assertIn('title="Hosted multiplexer teardown"', controller)
        self.assertIn("graph=DeploymentGraph(workflow.workspace_id)", controller)
        self.assertIn(
            "_disconnect_runtime_networks(workflow.server_container, workspace_id=workflow.workspace_id)",
            controller,
        )
        self.assertIn('_assert_activity_mentions(workflow, removed.run_id, "cloudflared-gateway")', controller)
        self.assertIn('"headers", "body", "secret"', controller)
        self.assertIn('{"method": "GET", "path": "/"}', controller)
        self.assertNotIn('"MULTIPLEXER_PRIMARY_URL"', controller)
        self.assertNotIn('"MULTIPLEXER_OBSERVER_A_URL"', controller)
        self.assertNotIn("DockerRuntimeInterpreter", controller)

        smoke = (
            ROOT / "scripts" / "cpk_server_workspace_b_multiplexer_observer_smoke.sh"
        ).read_text(encoding="utf-8")
        self.assertIn("CPK_HOSTED_ACTIVITY_SCENARIO=workspace-b-multiplexer-observer", smoke)
        self.assertIn("scripts/cpk_server_hosted_activity_smoke.sh", smoke)

    def test_hosted_activity_controller_proves_workspace_c_postgres_retained_data(
        self,
    ) -> None:
        controller = (ROOT / "scripts" / "cpk_server_hosted_activity.py").read_text(
            encoding="utf-8"
        )

        self.assertIn('"workspace-c-postgres-retained-data"', controller)
        self.assertIn('workspace_id="workspace-c-postgres"', controller)
        self.assertIn("def _run_postgres_retained_data", controller)
        self.assertIn("def _postgres_graph", controller)
        self.assertIn('"cpk_local_gateway"', controller)
        self.assertIn('"gateway"', controller)
        self.assertIn('"postgres_server"', controller)
        self.assertIn('"postgres"', controller)
        self.assertIn('"cloudflared_connector"', controller)
        self.assertIn("workflow.register_cloudflare_ingress_authority()", controller)
        self.assertIn("DeploymentGraph(workflow.workspace_id)", controller)
        self.assertIn(
            "spec=replace(gateway.spec, verification=VerificationContract())",
            controller,
        )
        self.assertIn(
            "spec=replace(postgres.spec, verification=VerificationContract())",
            controller,
        )
        self.assertIn("VerificationContract()", controller)
        self.assertIn("_assert_gateway_postgres_query_ready", controller)
        self.assertIn("_assert_public_gateway_postgres_query_ready", controller)
        self.assertIn("_retained_data_volumes", controller)
        self.assertIn("_assert_retained_volumes_still_exist", controller)
        self.assertIn("_assert_no_node_containers", controller)
        self.assertIn("_assert_no_runtime_networks", controller)
        self.assertIn("_assert_secret_absent_from_activity", controller)
        self.assertIn('"cpk-postgres-smoke-password"', controller)
        self.assertIn("sync_runtime_networks=False", controller)
        self.assertIn('"target-postgres"', controller)
        self.assertIn('"postgres-select-one"', controller)
        self.assertIn('"target_id": "postgres.postgres"', controller)
        self.assertIn('"org.openj92.cpk.volume.kind=retained-data"', controller)
        self.assertNotIn('["psql", "-U", "cpk", "-d", "cpk", "-c", "SELECT 1"]', controller)
        self.assertNotIn('"POSTGRES_PASSWORD"', controller)
        self.assertNotIn("DockerRuntimeInterpreter", controller)

        smoke = (
            ROOT / "scripts" / "cpk_server_workspace_c_postgres_retained_data_smoke.sh"
        ).read_text(encoding="utf-8")
        self.assertIn("CPK_HOSTED_ACTIVITY_SCENARIO=workspace-c-postgres-retained-data", smoke)
        self.assertIn("scripts/cpk_server_hosted_activity_smoke.sh", smoke)

    def test_secret_provider_source_live_uses_real_provider_and_public_workflow(
        self,
    ) -> None:
        smoke = (
            ROOT / "scripts" / "cpk_server_secret_provider_source_live_smoke.sh"
        ).read_text(encoding="utf-8")
        controller = (
            ROOT / "scripts" / "cpk_server_secret_provider_source_live.py"
        ).read_text(encoding="utf-8")

        self.assertIn("control-plane-kit-secrets:source-1202", smoke)
        self.assertIn("control_plane_kit_secrets.server:app", smoke)
        self.assertIn("CPK_PRODUCT_MATERIAL_RESOLVER=provider", smoke)
        self.assertIn("CPK_MATERIAL_PROVIDER_ROUTES_JSON", smoke)
        self.assertIn("CPK_MATERIAL_PROVIDER_BOOTSTRAP_FILES_JSON", smoke)
        self.assertIn("CPK_SECRETS_MASTER_KEY_FILE", smoke)
        self.assertIn("CPK_SECRETS_CREDENTIALS_FILE", smoke)
        self.assertNotIn("CPK_SECRETS_DEVELOPMENT_CREDENTIALS_JSON", smoke)
        self.assertIn("provider-data", smoke)
        self.assertIn("docker_residue_audit.sh", smoke)
        self.assertNotIn("CPK_PRODUCT_SECRET_VALUES_JSON", smoke)
        self.assertNotIn("CPK_PRODUCT_MATERIAL_RESOLVER=local-development", smoke)
        self.assertNotIn("CPK_IMAGE_PULL_CREDENTIAL_RESOLVER", smoke)

        self.assertIn("/secret-providers", controller)
        self.assertIn("/secret-references", controller)
        self.assertIn("read.secret-providers", controller)
        self.assertIn("read.secret-references", controller)
        self.assertIn("run_approved_transition", controller)
        self.assertIn("cpk_secret_use_authorizations", controller)
        self.assertIn("audit_records", controller)
        self.assertIn("_restart_provider", controller)
        self.assertIn("workspace-secret-denied-scope", controller)
        self.assertIn("workspace-secret-wrong-target", controller)
        self.assertIn("workspace-secret-wrong-intent", controller)
        self.assertIn("workspace-secret-revoked-provider", controller)
        self.assertIn("workspace-secret-revoked-reference", controller)
        self.assertIn("workspace-secret-missing", controller)
        self.assertIn("workspace-secret-wrong-credential", controller)
        self.assertIn("workspace-secret-unavailable", controller)
        self.assertNotIn("DockerRuntimeInterpreter", controller)
        self.assertNotIn("ControlPlaneKitSecretsResolver", controller)

    def test_recursive_activity_smoke_uses_published_parent_and_secret_authority(
        self,
    ) -> None:
        smoke = (ROOT / "scripts" / "cpk_server_recursive_activity_smoke.sh").read_text(
            encoding="utf-8"
        )

        self.assertIn("products/cpk_server/product.docker.cpk.json", smoke)
        self.assertIn("@{image['digest']}", smoke)
        self.assertIn("docker pull", smoke)
        self.assertIn('CHAIN_DEPTH="${CPK_RECURSIVE_LOCAL_CHAIN_DEPTH:-1}"', smoke)
        self.assertIn('CPK_RECURSIVE_LOCAL_CHAIN_DEPTH="$CHAIN_DEPTH"', smoke)
        self.assertIn("CPK_RUNTIME_INTERPRETERS=docker", smoke)
        self.assertIn("CPK_PRODUCT_MATERIAL_RESOLVER=local-development", smoke)
        self.assertIn("CPK_PRODUCT_SECRET_VALUES_JSON", smoke)
        self.assertIn('python3 - "${AUTH_CONFIG_DIR:-}" "$CHAIN_DEPTH"', smoke)
        self.assertIn("def child_secret_values(remaining_depth: int)", smoke)
        self.assertIn("secret://control-plane-kit/postgres/password", smoke)
        self.assertIn("secret://control-plane-kit/child/docker-auth-config-json", smoke)
        self.assertIn("secret://control-plane-kit/child/image-pull-credential-resolver", smoke)
        self.assertIn("secret://control-plane-kit/child/product-secret-resolver", smoke)
        self.assertIn("secret://control-plane-kit/child/product-secret-values-json", smoke)
        self.assertIn("CPK_RECURSIVE_REGISTER_PULL_AUTHORITY", smoke)
        self.assertIn("python scripts/cpk_server_recursive_activity.py", smoke)
        self.assertIn('WORKSPACE_LABEL_KEY="org.openj92.cpk.workspace"', smoke)
        self.assertIn('docker ps -aq --filter "label=$WORKSPACE_LABEL_KEY"', smoke)
        self.assertIn('docker volume ls -q --filter "label=$WORKSPACE_LABEL_KEY"', smoke)
        self.assertIn('docker network ls -q --filter "label=$WORKSPACE_LABEL_KEY"', smoke)
        self.assertIn("recursive-cpk-server|recursive-cpk-server-local-chain-*)", smoke)
        self.assertIn("\ncleanup_recursive_resources\n\nif [ \"$BUILD_CONTROLLER\" = \"1\" ]", smoke)
        self.assertIn("docker rm -f", smoke)
        self.assertIn("docker network rm", smoke)
        self.assertNotIn("docker system prune", smoke)
        self.assertNotIn("docker volume prune", smoke)

    def test_recursive_activity_controller_uses_parent_routes_and_local_chain(
        self,
    ) -> None:
        controller = (ROOT / "scripts" / "cpk_server_recursive_activity.py").read_text(
            encoding="utf-8"
        )

        self.assertIn('WORKSPACE_ID = "recursive-cpk-server"', controller)
        self.assertIn("MAX_LOCAL_CHAIN_DEPTH = 10", controller)
        self.assertIn('os.environ.get("CPK_RECURSIVE_LOCAL_CHAIN_DEPTH", "1")', controller)
        self.assertIn('LOCAL_CHAIN_AUTHORITY_REF = "local-docker"', controller)
        self.assertIn("_chain_cpk_document(servers_repo)", controller)
        self.assertIn("_product_document(servers_repo, \"postgres_server\")", controller)
        self.assertIn('"kind": "local-docker-socket"', controller)
        self.assertIn("command.runtime-authority.register", controller)
        self.assertIn("command.runtime-authority-delivery.register", controller)
        self.assertIn('"delivery_kind": "local-docker-socket-mount"', controller)
        self.assertIn("CPK_PRODUCT_MATERIAL_RESOLVER", controller)
        self.assertIn("CPK_PRODUCT_SECRET_VALUES_JSON", controller)
        self.assertIn("secret://control-plane-kit/child/product-secret-resolver", controller)
        self.assertIn("secret://control-plane-kit/child/product-secret-values-json", controller)
        self.assertIn("PolicyScope.RUNTIME_AUTHORITY_DELIVERY_REGISTER.value", controller)
        self.assertIn("_register_local_docker_delivery", controller)
        self.assertIn("_run_local_chain", controller)
        self.assertIn("cpk-server-docker-local-chain-harness", controller)
        self.assertIn("RuntimeAuthorityReference(LOCAL_CHAIN_AUTHORITY_REF)", controller)
        self.assertIn("command.deployment.plan", controller)
        self.assertIn("command.approval.decide", controller)
        self.assertIn("command.deployment.execute", controller)
        self.assertIn("/activity", controller)
        self.assertIn("_assert_parent_observations", controller)
        self.assertIn("parent activity timeline did not expose run", controller)
        self.assertIn('session.get("plans", [])', controller)
        self.assertIn('plan.get("runs", [])', controller)
        self.assertIn("parent did not record health evidence", controller)
        self.assertIn("docker.io/library/postgres@sha256:", controller)
        self.assertIn("ghcr.io/openj92/control-plane-kit-servers/cpk-server@sha256:", controller)
        self.assertIn("read.pending-approvals", controller)
        self.assertIn("read.approval-detail", controller)
        self.assertIn("child-cpk", controller)
        self.assertIn("command.deployment.execute", controller)
        self.assertIn("/health/live", controller)
        self.assertIn("/health/ready", controller)
        self.assertIn("workflow.wait_ready()", controller)
        self.assertEqual(controller.count('SocketConnection('), 4)
        self.assertNotIn("CpkServerOperationsApplication", controller)
        self.assertNotIn("PostgresUnitOfWork", controller)
        self.assertNotIn("DockerRuntimeInterpreter", controller)
        self.assertNotIn("/activity-history", controller)
        self.assertNotIn("CPK_RUNTIME_INTERPRETERS=docker implies socket", controller)

    def test_recursive_tls_activity_smoke_uses_ephemeral_dind_authority(
        self,
    ) -> None:
        smoke = (
            ROOT / "scripts" / "cpk_server_recursive_tls_activity_smoke.sh"
        ).read_text(encoding="utf-8")

        self.assertIn("products/cpk_server/product.docker.cpk.json", smoke)
        self.assertIn("@{image['digest']}", smoke)
        self.assertIn("docker pull", smoke)
        self.assertIn('DIND_IMAGE="${CPK_RECURSIVE_TLS_DIND_IMAGE:-docker:27-dind}"', smoke)
        self.assertIn("--privileged", smoke)
        self.assertIn("DOCKER_TLS_CERTDIR=/certs", smoke)
        self.assertIn("-H tcp://127.0.0.1:2376 version", smoke)
        self.assertIn("docker cp", smoke)
        self.assertIn("secret://control-plane-kit/docker-tls/ca", smoke)
        self.assertIn("secret://control-plane-kit/docker-tls/cert", smoke)
        self.assertIn("secret://control-plane-kit/docker-tls/key", smoke)
        self.assertIn("secret://control-plane-kit/child/docker-auth-config-json", smoke)
        self.assertIn("secret://control-plane-kit/child/image-pull-credential-resolver", smoke)
        self.assertIn("secret://control-plane-kit/child/product-secret-resolver", smoke)
        self.assertIn("secret://control-plane-kit/child/product-secret-values-json", smoke)
        self.assertIn("CPK_RUNTIME_INTERPRETERS=docker", smoke)
        self.assertIn("CPK_RECURSIVE_TLS_DOCKER_ENDPOINT=tcp://docker:2376", smoke)
        self.assertIn('FAMILY_SIZE="${CPK_RECURSIVE_TLS_FAMILY_SIZE:-1}"', smoke)
        self.assertIn('CPK_RECURSIVE_TLS_FAMILY_SIZE="$FAMILY_SIZE"', smoke)
        self.assertIn(
            'CPK_RECURSIVE_TLS_REGISTER_CHILD_PULL_AUTHORITY="$IMAGE_PULL_RESOLVER"',
            smoke,
        )
        self.assertIn("python scripts/cpk_server_recursive_tls_activity.py", smoke)
        self.assertIn("org.openj92.cpk.workspace=recursive-cpk-server-tls-parent", smoke)
        self.assertIn("org.openj92.cpk.workspace=recursive-cpk-server-tls-child", smoke)
        self.assertIn("docker rm -f", smoke)
        self.assertIn("docker network rm", smoke)
        self.assertNotIn("docker system prune", smoke)
        self.assertNotIn("docker volume prune", smoke)

    def test_recursive_tls_controller_registers_authority_inside_child(
        self,
    ) -> None:
        controller = (
            ROOT / "scripts" / "cpk_server_recursive_tls_activity.py"
        ).read_text(encoding="utf-8")

        self.assertIn('PARENT_WORKSPACE_ID = "recursive-cpk-server-tls-parent"', controller)
        self.assertIn('CHILD_WORKSPACE_ID = "recursive-cpk-server-tls-child"', controller)
        self.assertIn('CHILD_AUTHORITY_REF = "ephemeral-docker-tls"', controller)
        self.assertIn("MAX_FAMILY_SIZE = 10", controller)
        self.assertIn('os.environ.get("CPK_RECURSIVE_TLS_FAMILY_SIZE", "1")', controller)
        self.assertIn("_cpk_family_with_postgres_graph", controller)
        self.assertIn("cpk-server-docker-tls-harness", controller)
        self.assertIn("cpk-server-no-health-tls-harness", controller)
        self.assertIn("postgres-server-no-health-tls-harness", controller)
        self.assertIn("command.runtime-authority.register", controller)
        self.assertIn("read.runtime-authorities", controller)
        self.assertIn("read.runtime-authority-detail", controller)
        self.assertIn('"kind": "remote-docker-tls"', controller)
        self.assertIn("secret://control-plane-kit/docker-tls/ca", controller)
        self.assertIn("secret://control-plane-kit/docker-tls/cert", controller)
        self.assertIn("secret://control-plane-kit/docker-tls/key", controller)
        self.assertIn("secret://control-plane-kit/child/docker-auth-config-json", controller)
        self.assertIn("image-pull-credential-resolver", controller)
        self.assertIn("CPK_DOCKER_AUTH_CONFIG_JSON", controller)
        self.assertIn("CPK_IMAGE_PULL_CREDENTIAL_RESOLVER", controller)
        self.assertIn("RuntimeAuthorityReference(CHILD_AUTHORITY_REF)", controller)
        self.assertIn("authority_ref=authority_ref", controller)
        self.assertIn("run_approved_transition", controller)
        self.assertIn('f"grandchild-cpk-{index}"', controller)
        self.assertIn('f"grandchild-postgres-{index}"', controller)
        self.assertIn("network.connect(container, aliases=aliases)", controller)
        self.assertIn('"docker"', controller)
        self.assertIn("begin private key", controller)
        self.assertNotIn("CpkServerOperationsApplication", controller)
        self.assertNotIn("PostgresUnitOfWork", controller)
        self.assertNotIn("DockerRuntimeInterpreter", controller)


if __name__ == "__main__":
    unittest.main()
