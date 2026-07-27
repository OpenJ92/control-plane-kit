import ast
import base64
import importlib
import json
from pathlib import Path
import tempfile
import sys
import unittest


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
    "_docker_runtime_interpreter",
    "_image_pull_credential_resolver",
    "_product_secret_resolver",
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
            "control-plane-kit-interpreters[docker] @ "
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
                "CPK_CONTROL_AUTH_CONFIGURED",
                "CPK_PORT",
                "CPK_RUNTIME_INTERPRETERS",
                "CPK_IMAGE_PULL_CREDENTIAL_RESOLVER",
                "CPK_PRODUCT_SECRET_RESOLVER",
                "CPK_PRODUCT_SECRET_VALUES_JSON",
                "DOCKER_CONFIG",
                "CPK_DOCKER_AUTH_CONFIG_JSON",
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
            self.assertEqual(config.image_pull_credential_resolver, "none")
            self.assertEqual(config.product_secret_resolver, "none")
            self.assertNotIn("postgres://", repr(config.process_configuration()))
        finally:
            sys.path.remove(str(PRODUCT_SRC))
            for name in list(sys.modules):
                if name == "control_plane_kit_servers_cpk_server" or name.startswith(
                    "control_plane_kit_servers_cpk_server."
                ):
                    sys.modules.pop(name, None)

    def test_bootstrap_docker_config_pull_resolver_is_explicit_and_redacted(self) -> None:
        sys.path.insert(0, str(PRODUCT_SRC))
        try:
            server_module = importlib.import_module(
                "control_plane_kit_servers_cpk_server.server"
            )
            from control_plane_kit_core.runtime_effects import ImagePullAuthority
            from control_plane_kit_core.secrets import SecretReference
            from control_plane_kit_interpreters.secrets import (
                ImagePullCredentialDenied,
                ImagePullCredentialResolved,
            )

            with tempfile.TemporaryDirectory() as directory:
                config_path = Path(directory) / "config.json"
                encoded = base64.b64encode(
                    b"OpenJ92:registry-token-not-for-output"
                ).decode("ascii")
                config_path.write_text(
                    json.dumps({"auths": {"ghcr.io": {"auth": encoded}}}),
                    encoding="utf-8",
                )
                config = server_module.CpkServerBootstrapConfiguration.from_environment(
                    {
                        "CPK_SERVER_MODE": "execution-capable",
                        "CPK_CONTROL_AUTH_CONFIGURED": "true",
                        "CPK_PORT": "8080",
                        "CPK_RUNTIME_INTERPRETERS": "docker",
                        "CPK_IMAGE_PULL_CREDENTIAL_RESOLVER": "docker-config",
                        "DOCKER_CONFIG": directory,
                        "CPK_WORKPLACE_DATABASE_URL": "postgres://user:pass@db/cpk",
                        "CPK_ACTIVITY_HISTORY_DATABASE_URL": "postgres://user:pass@db/cpk",
                        "CPK_OBSERVER_STATE_DATABASE_URL": "postgres://user:pass@db/cpk",
                        "CPK_GRAPH_TOPOLOGY_DATABASE_URL": "postgres://user:pass@db/cpk",
                    }
                )

                resolver = server_module._image_pull_credential_resolver(config)
                resolved = resolver.resolve(
                    ImagePullAuthority(
                        "ghcr.io",
                        "openj92/control-plane-kit-servers",
                        SecretReference("secret://docker-config/ghcr.io"),
                    )
                )
                denied = resolver.resolve(
                    ImagePullAuthority(
                        "ghcr.io",
                        "openj92/control-plane-kit-servers",
                        SecretReference("secret://other/ghcr.io"),
                    )
                )

                self.assertIsInstance(resolved, ImagePullCredentialResolved)
                self.assertIsInstance(denied, ImagePullCredentialDenied)
                self.assertEqual(config.image_pull_credential_resolver, "docker-config")
                self.assertEqual(config.docker_config_path, str(config_path))
                self.assertIsNone(config.docker_config_json)
                self.assertNotIn("registry-token-not-for-output", repr(resolver))
                self.assertNotIn("registry-token-not-for-output", repr(resolved))
                self.assertNotIn("registry-token-not-for-output", repr(config))
        finally:
            sys.path.remove(str(PRODUCT_SRC))
            for name in list(sys.modules):
                if name == "control_plane_kit_servers_cpk_server" or name.startswith(
                    "control_plane_kit_servers_cpk_server."
                ):
                    sys.modules.pop(name, None)

    def test_bootstrap_local_product_secret_resolver_is_explicit_and_redacted(
        self,
    ) -> None:
        sys.path.insert(0, str(PRODUCT_SRC))
        try:
            server_module = importlib.import_module(
                "control_plane_kit_servers_cpk_server.server"
            )
            from control_plane_kit_core.secrets import (
                SecretReference,
                SecretResolved,
            )

            config = server_module.CpkServerBootstrapConfiguration.from_environment(
                {
                    "CPK_SERVER_MODE": "execution-capable",
                    "CPK_CONTROL_AUTH_CONFIGURED": "true",
                    "CPK_PORT": "8080",
                    "CPK_RUNTIME_INTERPRETERS": "docker",
                    "CPK_PRODUCT_SECRET_RESOLVER": "local-development",
                    "CPK_PRODUCT_SECRET_VALUES_JSON": json.dumps(
                        {
                            "secret://control-plane-kit/postgres/password": (
                                "postgres-secret-not-for-output"
                            )
                        }
                    ),
                    "CPK_WORKPLACE_DATABASE_URL": "postgres://user:pass@db/cpk",
                    "CPK_ACTIVITY_HISTORY_DATABASE_URL": "postgres://user:pass@db/cpk",
                    "CPK_OBSERVER_STATE_DATABASE_URL": "postgres://user:pass@db/cpk",
                    "CPK_GRAPH_TOPOLOGY_DATABASE_URL": "postgres://user:pass@db/cpk",
                }
            )
            resolver = server_module._product_secret_resolver(config)
            resolved = resolver.resolve(
                SecretReference("secret://control-plane-kit/postgres/password")
            )

            self.assertIsInstance(resolved, SecretResolved)
            self.assertEqual(config.product_secret_resolver, "local-development")
            self.assertNotIn("postgres-secret-not-for-output", repr(config))
            self.assertNotIn("postgres-secret-not-for-output", repr(resolver))
            self.assertNotIn("postgres-secret-not-for-output", repr(resolved))
        finally:
            sys.path.remove(str(PRODUCT_SRC))
            for name in list(sys.modules):
                if name == "control_plane_kit_servers_cpk_server" or name.startswith(
                    "control_plane_kit_servers_cpk_server."
                ):
                    sys.modules.pop(name, None)

    def test_bootstrap_product_secret_resolver_selection_is_closed(self) -> None:
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
                "CPK_PRODUCT_SECRET_RESOLVER": "env-file",
                "CPK_WORKPLACE_DATABASE_URL": "postgres://user:pass@db/cpk",
                "CPK_ACTIVITY_HISTORY_DATABASE_URL": "postgres://user:pass@db/cpk",
                "CPK_OBSERVER_STATE_DATABASE_URL": "postgres://user:pass@db/cpk",
                "CPK_GRAPH_TOPOLOGY_DATABASE_URL": "postgres://user:pass@db/cpk",
            }

            with self.assertRaisesRegex(
                server_module.BootstrapConfigurationError,
                "CPK_PRODUCT_SECRET_RESOLVER must be one of",
            ):
                server_module.CpkServerBootstrapConfiguration.from_environment(environ)
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
        self.assertIn("control_plane_kit_interpreters.docker", source)
        self.assertIn("CPK_RUNTIME_INTERPRETERS", source)
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
        self.assertIn("@{image['digest']}", smoke)
        self.assertIn("docker pull", smoke)
        self.assertIn("CPK_RUNTIME_INTERPRETERS=docker", smoke)
        self.assertIn('IMAGE_PULL_RESOLVER="docker-config"', smoke)
        self.assertIn('CPK_IMAGE_PULL_CREDENTIAL_RESOLVER="$IMAGE_PULL_RESOLVER"', smoke)
        self.assertIn("CPK_HOSTED_ACTIVITY_REGISTER_PULL_AUTHORITY", smoke)
        self.assertIn("CPK_HOSTED_ACTIVITY_SCENARIO", smoke)
        self.assertIn("CPK_DOCKER_SOCKET_GROUP", smoke)
        self.assertIn("CPK_DOCKER_AUTH_CONFIG", smoke)
        self.assertIn("CPK_PRODUCT_SECRET_RESOLVER=local-development", smoke)
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
        self.assertNotIn("CpkServerOperationsApplication", controller)
        self.assertNotIn("PostgresUnitOfWork", controller)
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

    def test_hosted_activity_controller_proves_workspace_a_router_transition(
        self,
    ) -> None:
        controller = (ROOT / "scripts" / "cpk_server_hosted_activity.py").read_text(
            encoding="utf-8"
        )

        self.assertIn('"workspace-a-router-transition"', controller)
        self.assertIn('workspace_id="workspace-a-router"', controller)
        self.assertIn('"Hello from blue"', controller)
        self.assertIn('"Hello from green"', controller)
        self.assertIn('_assert_body("http://router:8000/", "Hello from blue\\n")', controller)
        self.assertIn('_assert_body("http://router:8000/", "Hello from green\\n")', controller)
        self.assertIn('_assert_activity_mentions(workflow, blue.run_id, "hello-blue")', controller)
        self.assertIn('_assert_activity_mentions(workflow, blue.run_id, "router")', controller)
        self.assertIn('_assert_activity_mentions(workflow, green.run_id, "hello-green")', controller)
        self.assertIn('_assert_activity_mentions(workflow, green.run_id, "router")', controller)
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
        self.assertIn('_assert_body("http://multiplexer:8000/", "Primary response\\n")', controller)
        self.assertIn(
            '_assert_observer_receipt("http://hello-observer:8000/observations/requests")',
            controller,
        )
        self.assertIn('_assert_activity_mentions(workflow, result.run_id, "hello-primary")', controller)
        self.assertIn('_assert_activity_mentions(workflow, result.run_id, "hello-observer")', controller)
        self.assertIn('_assert_activity_mentions(workflow, result.run_id, "multiplexer")', controller)
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
        self.assertIn("CPK_PRODUCT_SECRET_RESOLVER=local-development", smoke)
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
        self.assertIn("CPK_PRODUCT_SECRET_RESOLVER", controller)
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
