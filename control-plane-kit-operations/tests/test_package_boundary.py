from __future__ import annotations

import ast
from pathlib import Path
import tomllib
import unittest

from control_plane_kit_core import DeploymentProgramStage
from control_plane_kit_operations import OPERATIONS_PACKAGE_BOUNDARY


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = PACKAGE_ROOT / "pyproject.toml"
SRC_ROOT = PACKAGE_ROOT / "src" / "control_plane_kit_operations"
CONCRETE_RUNTIME_IMPORT_ROOTS = {
    "boto3",
    "botocore",
    "control_plane_kit_interpreters",
    "control_plane_kit_secrets",
    "cryptography",
    "docker",
    "google",
    "kubernetes",
}
PROCESS_IMPORT_ROOTS = {
    "control_plane_kit_servers",
    "fastapi",
    "httpx",
    "mcp",
    "uvicorn",
}
FORBIDDEN_SOURCE_IMPORT_ROOTS = (
    CONCRETE_RUNTIME_IMPORT_ROOTS
    | PROCESS_IMPORT_ROOTS
    | {"control_plane_kit", "subprocess"}
)


def _source_imports() -> tuple[set[str], set[Path]]:
    imports: set[str] = set()
    psycopg_imports: set[Path] = set()
    for source_path in SRC_ROOT.rglob("*.py"):
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    root = alias.name.split(".", 1)[0]
                    imports.add(root)
                    if root == "psycopg":
                        psycopg_imports.add(source_path)
            elif isinstance(node, ast.ImportFrom) and node.module:
                root = node.module.split(".", 1)[0]
                imports.add(root)
                if root == "psycopg":
                    psycopg_imports.add(source_path)
    return imports, psycopg_imports



class OperationsPackageBoundaryTests(unittest.TestCase):
    def test_package_declares_core_dependency_and_no_entrypoints(self) -> None:
        metadata = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))

        project = metadata["project"]
        self.assertEqual(project["name"], "control-plane-kit-operations")
        self.assertEqual(
            project["dependencies"],
            [
                "control-plane-kit-core>=0.1.0",
                "Jinja2>=3.1",
                "psycopg[binary]>=3.2",
            ],
        )
        self.assertNotIn("optional-dependencies", project)
        self.assertNotIn("scripts", project)

    def test_boundary_descriptor_preserves_deployment_spine(self) -> None:
        descriptor = OPERATIONS_PACKAGE_BOUNDARY.descriptor()

        self.assertEqual(descriptor["distribution"], "control-plane-kit-operations")
        self.assertEqual(descriptor["depends_on"], ["control-plane-kit-core"])
        self.assertEqual(
            descriptor["deployment_spine"],
            [stage.value for stage in DeploymentProgramStage],
        )
        self.assertIn("DeploymentProgram", descriptor["future_owners"])
        self.assertIn("RegisteredProduct", descriptor["future_owners"])
        self.assertIn("cpk-server process", descriptor["excluded_owners"])

    def test_operations_source_does_not_import_servers_or_process_packages(self) -> None:
        imports, psycopg_imports = _source_imports()

        self.assertFalse(imports & FORBIDDEN_SOURCE_IMPORT_ROOTS)
        self.assertIn("control_plane_kit_core", imports)
        self.assertEqual(
            {
                path.relative_to(SRC_ROOT)
                for path in psycopg_imports
                if path.relative_to(SRC_ROOT).parts[:1] != ("postgres",)
            },
            set(),
        )

    def test_operations_has_no_concrete_runtime_provider_imports(self) -> None:
        imports, _ = _source_imports()

        self.assertFalse(imports & CONCRETE_RUNTIME_IMPORT_ROOTS)

    def test_runtime_dispatcher_bootstrap_is_not_authority_truth(self) -> None:
        from control_plane_kit_core.types import RuntimeKind
        from control_plane_kit_operations import RuntimeDispatcherBootstrapConfiguration

        config = RuntimeDispatcherBootstrapConfiguration.allow((RuntimeKind.DOCKER,))
        rendered = f"{config!r} {config.descriptor()!r}".lower()

        self.assertEqual(config.descriptor()["runtime_interpreters"], ["docker"])
        for forbidden in (
            "authority",
            "credential",
            "secret",
            "token",
            "tls",
            "endpoint",
            "socket",
            "host",
            "path",
        ):
            self.assertNotIn(forbidden, rendered)

    def test_secret_provider_public_types_are_exported(self) -> None:
        import control_plane_kit_operations

        self.assertTrue(
            {
                "AuthorizeSecretUse",
                "AuthorizedSecretUse",
                "SecretMetadataCollectionReadModel",
                "SecretProviderRegistrationService",
                "SecretUseAuthorizationConflict",
                "SecretUseAuthorizationService",
            }.issubset(control_plane_kit_operations.__all__)
        )


if __name__ == "__main__":
    unittest.main()
