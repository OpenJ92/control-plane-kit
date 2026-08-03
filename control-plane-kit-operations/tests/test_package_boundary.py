from __future__ import annotations

import ast
from dataclasses import fields
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
    | {"aiohttp", "control_plane_kit", "requests", "subprocess", "urllib3"}
)
ROTATION_SOURCES = tuple(sorted(SRC_ROOT.glob("gateway_key_rotation*.py")))
ROTATION_EFFECT_IMPORT_ROOTS = FORBIDDEN_SOURCE_IMPORT_ROOTS | {
    "http",
    "os",
    "pathlib",
    "shutil",
    "socket",
    "ssl",
    "tempfile",
    "urllib",
}
ROTATION_OWNER_MODULES = {
    "GatewayKeyRotation": "gateway_key_rotations.py",
    "GatewayKeyRotationTransition": "gateway_key_rotations.py",
    "GatewayKeyRotationReadModel": "gateway_key_rotations.py",
    "GatewayKeyRotationDeploymentCheckpoint": "gateway_key_rotations.py",
    "GatewayKeyRotationGenerationProgram": "gateway_key_rotation_program.py",
    "GatewayKeyRotationOverlapProjectionService": (
        "gateway_key_rotation_overlap.py"
    ),
    "GatewayKeyRotationOverlapPreparationProgram": (
        "gateway_key_rotation_overlap_program.py"
    ),
    "GatewayKeyRotationOverlapExecutionProgram": (
        "gateway_key_rotation_overlap_execution.py"
    ),
    "GatewayKeyRotationActivationProgram": "gateway_key_rotation_activation.py",
    "GatewayKeyRotationRetirementPreparationProgram": (
        "gateway_key_rotation_retirement_program.py"
    ),
    "GatewayKeyRotationRetirementExecutionProgram": (
        "gateway_key_rotation_retirement_execution.py"
    ),
    "GatewayKeyRotationCompletionProgram": (
        "gateway_key_rotation_completion_program.py"
    ),
    "GatewayKeyRotationRetirementProjectionService": (
        "gateway_key_rotation_retirement.py"
    ),
}


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


def _imports_for(source_path: Path) -> set[str]:
    imports: set[str] = set()
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module.split(".", 1)[0])
    return imports


def _rotation_dependency_graph() -> dict[str, set[str]]:
    module_names = {source_path.stem for source_path in ROTATION_SOURCES}
    graph = {module_name: set() for module_name in module_names}
    prefix = "control_plane_kit_operations."
    for source_path in ROTATION_SOURCES:
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom) or not node.module:
                continue
            if not node.module.startswith(prefix):
                continue
            dependency = node.module.removeprefix(prefix).split(".", 1)[0]
            if dependency in module_names:
                graph[source_path.stem].add(dependency)
    return graph


def _class_owners(class_names: set[str]) -> dict[str, list[str]]:
    owners = {class_name: [] for class_name in class_names}
    for source_path in SRC_ROOT.rglob("*.py"):
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        for node in tree.body:
            if isinstance(node, ast.ClassDef) and node.name in owners:
                owners[node.name].append(str(source_path.relative_to(SRC_ROOT)))
    return owners


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

    def test_rotation_modules_are_acyclic_and_import_no_effect_clients(self) -> None:
        for source_path in ROTATION_SOURCES:
            with self.subTest(module=source_path.stem):
                self.assertFalse(
                    _imports_for(source_path) & ROTATION_EFFECT_IMPORT_ROOTS
                )

        graph = _rotation_dependency_graph()
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(module_name: str, path: tuple[str, ...]) -> None:
            if module_name in visiting:
                self.fail(
                    "rotation import cycle: "
                    + " -> ".join((*path, module_name))
                )
            if module_name in visited:
                return
            visiting.add(module_name)
            for dependency in sorted(graph[module_name]):
                visit(dependency, (*path, module_name))
            visiting.remove(module_name)
            visited.add(module_name)

        for module_name in sorted(graph):
            visit(module_name, ())

    def test_rotation_concepts_have_one_canonical_owner(self) -> None:
        from control_plane_kit_core.approval_subjects import (
            GatewayKeyRotationApprovalSubject,
        )
        from control_plane_kit_core.delegation_authority import (
            DelegationVerifierProjection,
        )
        from control_plane_kit_core.topology import DeploymentGraph
        from control_plane_kit_operations.delegation_signing_keys import (
            RegisteredDelegationSigningKey,
        )
        from control_plane_kit_operations.lifecycle import RunLifecycleCommandService

        owners = _class_owners(set(ROTATION_OWNER_MODULES))
        self.assertEqual(
            owners,
            {
                name: [module]
                for name, module in ROTATION_OWNER_MODULES.items()
            },
        )
        self.assertEqual(
            GatewayKeyRotationApprovalSubject.__module__,
            "control_plane_kit_core.approval_subjects",
        )
        self.assertEqual(
            DeploymentGraph.__module__,
            "control_plane_kit_core.topology.graph",
        )
        self.assertEqual(
            DelegationVerifierProjection.__module__,
            "control_plane_kit_core.delegation_authority",
        )
        self.assertEqual(
            RegisteredDelegationSigningKey.__module__,
            "control_plane_kit_operations.delegation_signing_keys",
        )
        self.assertEqual(
            RunLifecycleCommandService.__module__,
            "control_plane_kit_operations.lifecycle",
        )

    def test_rotation_public_contract_fields_are_secret_free(self) -> None:
        from control_plane_kit_core.approval_subjects import (
            GatewayKeyRotationApprovalSubject,
        )
        from control_plane_kit_operations.gateway_key_rotations import (
            GatewayKeyRotationReadModel,
            GatewayKeyRotationTransition,
        )

        forbidden = {
            "compact",
            "credential",
            "environment",
            "pem",
            "private",
            "provider",
            "secret",
        }
        for contract in (
            GatewayKeyRotationApprovalSubject,
            GatewayKeyRotationReadModel,
            GatewayKeyRotationTransition,
        ):
            with self.subTest(contract=contract.__name__):
                field_names = {field.name.lower() for field in fields(contract)}
                self.assertFalse(
                    {
                        field_name
                        for field_name in field_names
                        if any(fragment in field_name for fragment in forbidden)
                    }
                )

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
