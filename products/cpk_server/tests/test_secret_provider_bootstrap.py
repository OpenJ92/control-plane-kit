import base64
import importlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

import httpx


ROOT = Path(__file__).resolve().parents[3]
PRODUCT = ROOT / "products" / "cpk_server"
PRODUCT_SRC = PRODUCT / "src"
SERVER_SOURCE = PRODUCT_SRC / "control_plane_kit_servers_cpk_server" / "server.py"


class SecretProviderBootstrapTests(unittest.TestCase):
    def test_provider_mode_composes_one_registry_backed_resolver_and_custodian(
        self,
    ) -> None:
        server = _server_module()
        try:
            with tempfile.TemporaryDirectory() as directory:
                credential_file = Path(directory) / "provider.token"
                credential_file.write_text(
                    "provider-token-not-for-output",
                    encoding="utf-8",
                )
                config = server.CpkServerBootstrapConfiguration.from_environment(
                    _provider_environment(credential_file)
                )

                composition = server._secret_provider_composition(config)

                self.assertEqual(config.product_material_resolver, "provider")
                self.assertEqual(
                    type(composition.authorized_resolver).__name__,
                    "ControlPlaneKitSecretsResolver",
                )
                self.assertEqual(
                    type(composition.secret_custodian).__name__,
                    "ControlPlaneKitSecretsCustodian",
                )
                rendered = repr(config) + repr(composition)
                self.assertNotIn(str(credential_file), rendered)
                self.assertNotIn("provider-token-not-for-output", rendered)
                self.assertNotIn("https://secrets.internal.example", rendered)
        finally:
            _unload_server()

    def test_provider_mode_resolves_through_exact_grant_routing(self) -> None:
        server = _server_module()
        try:
            from control_plane_kit_core.secrets import (
                SecretProviderEndpointReference,
                SecretReference,
                SecretResolutionGrant,
                SecretResolved,
                SecretUseIntent,
            )
            from control_plane_kit_interpreters.secret_provider import (
                canonical_provider_secret_id,
            )

            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                selected_file = root / "selected.token"
                selected_file.write_text(
                    "selected-provider-token",
                    encoding="ascii",
                )
                other_file = root / "other.token"
                other_file.write_text("other-provider-token", encoding="ascii")
                reference = SecretReference(
                    "secret://provider-main/application/control-token"
                )
                requests = []

                def handler(request: httpx.Request) -> httpx.Response:
                    requests.append(request)
                    return httpx.Response(
                        200,
                        headers={"content-type": "application/json"},
                        json={
                            "outcome": "resolved",
                            "metadata": {
                                "workspace_id": "workspace-a",
                                "secret_id": canonical_provider_secret_id(
                                    reference
                                ),
                                "version_id": "version-1",
                                "version_number": 1,
                                "status": "active",
                                "algorithm": "AES-256-GCM",
                                "key_fingerprint": "a" * 64,
                                "key_version": "test",
                                "labels": {
                                    "intent": "application.control-token"
                                },
                                "created_at": "2026-07-30T00:00:00Z",
                                "revoked_at": None,
                            },
                            "value_base64": base64.b64encode(
                                b"resolved-value-not-for-output"
                            ).decode("ascii"),
                        },
                    )

                config = server.CpkServerBootstrapConfiguration.from_environment(
                    {
                        **_base_environment(),
                        "CPK_PRODUCT_MATERIAL_RESOLVER": "provider",
                        "CPK_MATERIAL_PROVIDER_ROUTES_JSON": json.dumps(
                            {
                                "provider-main":
                                    "https://selected.internal.example",
                                "provider-other":
                                    "https://other.internal.example",
                            }
                        ),
                        "CPK_MATERIAL_PROVIDER_BOOTSTRAP_FILES_JSON": json.dumps(
                            {
                                "secret://bootstrap/selected-token":
                                    str(selected_file),
                                "secret://bootstrap/other-token": str(other_file),
                            }
                        ),
                    }
                )
                composition = server._secret_provider_composition(
                    config,
                    transport=httpx.MockTransport(handler),
                )
                grant = SecretResolutionGrant(
                    authorization_id="suse_" + "a" * 64,
                    workspace_id="workspace-a",
                    reference_registration_id="sref_" + "b" * 64,
                    provider_registration_id="sprov_" + "c" * 64,
                    endpoint_reference=SecretProviderEndpointReference(
                        "provider-main"
                    ),
                    credential_reference=SecretReference(
                        "secret://bootstrap/selected-token"
                    ),
                    reference=reference,
                    intent=SecretUseIntent.APPLICATION_CONTROL_TOKEN,
                    actor_subject="operator-a",
                    correlation_id="resolve-a",
                    intent_fingerprint="d" * 64,
                )

                resolved = composition.authorized_resolver.resolve(grant)

                self.assertIsInstance(resolved, SecretResolved)
                self.assertEqual(len(requests), 1)
                self.assertEqual(
                    requests[0].url.host,
                    "selected.internal.example",
                )
                self.assertEqual(
                    requests[0].headers["authorization"],
                    "Bearer selected-provider-token",
                )
                rendered = repr(config) + repr(composition) + repr(resolved)
                for forbidden in (
                    "resolved-value-not-for-output",
                    "selected-provider-token",
                    "other-provider-token",
                    str(selected_file),
                    str(other_file),
                ):
                    self.assertNotIn(forbidden, rendered)
        finally:
            _unload_server()

    def test_provider_mode_fails_closed_for_missing_ambiguous_or_inline_config(
        self,
    ) -> None:
        server = _server_module()
        try:
            base = _base_environment()
            with self.assertRaisesRegex(
                server.BootstrapConfigurationError,
                "requires endpoint and credential-file registries",
            ):
                server.CpkServerBootstrapConfiguration.from_environment(
                    {
                        **base,
                        "CPK_PRODUCT_MATERIAL_RESOLVER": "provider",
                    }
                )
            with self.assertRaisesRegex(
                server.BootstrapConfigurationError,
                "registry is malformed",
            ):
                server.CpkServerBootstrapConfiguration.from_environment(
                    {
                        **base,
                        "CPK_PRODUCT_MATERIAL_RESOLVER": "provider",
                        "CPK_MATERIAL_PROVIDER_ROUTES_JSON": (
                            '{"provider-main":"https://one.example",'
                            '"provider-main":"https://two.example"}'
                        ),
                        "CPK_MATERIAL_PROVIDER_BOOTSTRAP_FILES_JSON": json.dumps(
                            {
                                "secret://bootstrap/provider-token":
                                    "/run/secrets/provider-token"
                            }
                        ),
                    }
                )
            with self.assertRaisesRegex(
                server.BootstrapConfigurationError,
                "requires CPK_PRODUCT_MATERIAL_RESOLVER=local-development",
            ):
                server.CpkServerBootstrapConfiguration.from_environment(
                    {
                        **base,
                        "CPK_PRODUCT_MATERIAL_RESOLVER": "provider",
                        "CPK_PRODUCT_SECRET_VALUES_JSON": json.dumps(
                            {"secret://provider/value": "raw-value"}
                        ),
                        "CPK_MATERIAL_PROVIDER_ROUTES_JSON": json.dumps(
                            {
                                "provider-main":
                                    "https://secrets.internal.example"
                            }
                        ),
                        "CPK_MATERIAL_PROVIDER_BOOTSTRAP_FILES_JSON": json.dumps(
                            {
                                "secret://bootstrap/provider-token":
                                    "/run/secrets/provider-token"
                            }
                        ),
                    }
                )
            with self.assertRaisesRegex(
                server.BootstrapConfigurationError,
                "legacy Docker credential bootstrap is unavailable",
            ):
                server.CpkServerBootstrapConfiguration.from_environment(
                    {
                        **base,
                        "CPK_IMAGE_PULL_CREDENTIAL_RESOLVER": "docker-config",
                    }
                )
        finally:
            _unload_server()

    def test_production_descriptors_select_provider_without_bootstrap_material(
        self,
    ) -> None:
        for name in (
            "product.docker.cpk.json",
            "product.docker-cloudflare.cpk.json",
        ):
            descriptor = json.loads((PRODUCT / name).read_text(encoding="utf-8"))
            environment = {
                item["name"]: item["value"]
                for item in descriptor["product"]["runtime_contract"][
                    "public_environment"
                ]
            }
            self.assertEqual(
                environment["CPK_PRODUCT_MATERIAL_RESOLVER"],
                "provider",
            )
            rendered = json.dumps(descriptor, sort_keys=True)
            self.assertNotIn("CPK_PRODUCT_SECRET_VALUES_JSON", rendered)
            self.assertNotIn("CPK_MATERIAL_PROVIDER_ROUTES_JSON", rendered)
            self.assertNotIn(
                "CPK_MATERIAL_PROVIDER_BOOTSTRAP_FILES_JSON",
                rendered,
            )
            self.assertNotIn("local-development", rendered)

    def test_obsolete_production_secret_custody_and_selection_are_absent(
        self,
    ) -> None:
        source = SERVER_SOURCE.read_text(encoding="utf-8")
        for obsolete in (
            "InMemoryGeneratedSecretRecorder",
            "_GeneratedSecretResolver",
            "_CompositeSecretResolver",
            "DockerConfigImagePullCredentialResolver",
        ):
            self.assertNotIn(obsolete, source)
        self.assertIn("SecretUseAuthorizationService", source)
        self.assertIn("ControlPlaneKitSecretsResolver", source)
        self.assertIn("ControlPlaneKitSecretsCustodian", source)


def _base_environment() -> dict[str, str]:
    return {
        "CPK_SERVER_MODE": "execution-capable",
        "CPK_CONTROL_AUTH_CONFIGURED": "true",
        "CPK_PORT": "8080",
        "CPK_RUNTIME_INTERPRETERS": "docker",
        "CPK_WORKPLACE_DATABASE_URL": "postgres://user:pass@db/cpk",
        "CPK_ACTIVITY_HISTORY_DATABASE_URL": "postgres://user:pass@db/cpk",
        "CPK_OBSERVER_STATE_DATABASE_URL": "postgres://user:pass@db/cpk",
        "CPK_GRAPH_TOPOLOGY_DATABASE_URL": "postgres://user:pass@db/cpk",
    }


def _provider_environment(credential_file: Path) -> dict[str, str]:
    return {
        **_base_environment(),
        "CPK_PRODUCT_MATERIAL_RESOLVER": "provider",
        "CPK_MATERIAL_PROVIDER_ROUTES_JSON": json.dumps(
            {"provider-main": "https://secrets.internal.example"}
        ),
        "CPK_MATERIAL_PROVIDER_BOOTSTRAP_FILES_JSON": json.dumps(
            {"secret://bootstrap/provider-token": str(credential_file)}
        ),
    }


def _server_module():
    sys.path.insert(0, str(PRODUCT_SRC))
    return importlib.import_module("control_plane_kit_servers_cpk_server.server")


def _unload_server() -> None:
    if str(PRODUCT_SRC) in sys.path:
        sys.path.remove(str(PRODUCT_SRC))
    for name in list(sys.modules):
        if name == "control_plane_kit_servers_cpk_server" or name.startswith(
            "control_plane_kit_servers_cpk_server."
        ):
            sys.modules.pop(name, None)


if __name__ == "__main__":
    unittest.main()
