from __future__ import annotations

import ast
import importlib
import unittest
from pathlib import Path

import control_plane_kit_operations as operations_root
import control_plane_kit_operations.read_services as read_services


_EXPECTED_MODULES = {
    "__init__",
    "_redaction",
    "errors",
    "instance",
    "models",
    "protocols",
}

_PUBLIC_OWNERS = {
    "ControlSurfaceReadModel": "instance",
    "FocusedDetailReadModel": "models",
    "GraphPointerReadModel": "instance",
    "InstanceReadService": "instance",
    "ObservationFreshnessPolicy": "instance",
    "ProjectedObservation": "instance",
    "ReadModelError": "errors",
    "WorkspaceReadModel": "instance",
    "WorkspaceSummary": "instance",
    "project_observation": "instance",
}

_INTERNAL_PROTOCOLS = {
    "ActivityHistoryStore",
    "DelegationSigningKeyStore",
    "ExecutionStore",
    "GatewayProbeStore",
    "GraphTopologyStore",
    "IngressAuthorityStore",
    "ObservedStateStore",
    "RuntimeAuthorityDeliveryStore",
    "RuntimeAuthorityStore",
    "SecretProviderStore",
    "SecretReferenceStore",
    "WorkspaceStore",
}

_FOUNDATION_LEAVES = {"_redaction", "errors", "models", "protocols"}

_EXPECTED_FOUNDATION_IMPORTS = {
    "_redaction": {"__future__", "typing"},
    "errors": set(),
    "models": {"__future__", "dataclasses", "typing"},
    "protocols": {
        "__future__",
        "typing",
        "control_plane_kit_core.delegation_keys",
        "control_plane_kit_core.public_ingress",
        "control_plane_kit_core.runtime_authority",
        "control_plane_kit_core.secrets",
        "control_plane_kit_operations.delegation_signing_keys",
        "control_plane_kit_operations.read_pages",
        "control_plane_kit_operations.records",
        "control_plane_kit_operations.secret_providers",
    },
}


class ReadServicesPackageTests(unittest.TestCase):
    def test_current_read_services_subtree_is_exact(self) -> None:
        paths = tuple(getattr(read_services, "__path__", ()))
        self.assertEqual(len(paths), 1, "read_services must be one installed package")

        modules = {
            path.stem
            for path in Path(paths[0]).glob("*.py")
            if path.is_file()
        }
        self.assertEqual(modules, _EXPECTED_MODULES)

    def test_public_objects_have_one_canonical_installed_identity(self) -> None:
        try:
            modules = {
                name: importlib.import_module(
                    f"control_plane_kit_operations.read_services.{name}"
                )
                for name in {"errors", "instance", "models", "protocols"}
            }
        except ModuleNotFoundError as error:
            self.fail(f"read-services package leaves are missing: {error.name}")

        for public_name, owner_name in _PUBLIC_OWNERS.items():
            with self.subTest(public_name=public_name):
                canonical = getattr(modules[owner_name], public_name)
                self.assertIs(getattr(read_services, public_name), canonical)
                self.assertIs(getattr(operations_root, public_name), canonical)

        for protocol_name in _INTERNAL_PROTOCOLS:
            with self.subTest(protocol_name=protocol_name):
                self.assertTrue(hasattr(modules["protocols"], protocol_name))
                self.assertFalse(hasattr(read_services, protocol_name))
                self.assertFalse(hasattr(operations_root, protocol_name))

    def test_foundation_leaves_do_not_import_the_service_or_each_other_cyclically(
        self,
    ) -> None:
        paths = tuple(getattr(read_services, "__path__", ()))
        self.assertEqual(len(paths), 1, "read_services must be one installed package")
        package_path = Path(paths[0])

        imports: dict[str, set[str]] = {}
        local_imports: dict[str, set[str]] = {}
        for module_name in _FOUNDATION_LEAVES:
            tree = ast.parse(
                (package_path / f"{module_name}.py").read_text(encoding="utf-8")
            )
            imported: set[str] = set()
            local: set[str] = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    if node.level:
                        if node.module:
                            local.add(node.module.split(".", 1)[0])
                    elif node.module:
                        imported.add(node.module)
                    if node.module and node.module.startswith(
                        "control_plane_kit_operations.read_services."
                    ):
                        local.add(node.module.rsplit(".", 1)[-1])
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        imported.add(alias.name)
                        prefix = "control_plane_kit_operations.read_services."
                        if alias.name.startswith(prefix):
                            local.add(
                                alias.name.removeprefix(prefix).split(".", 1)[0]
                            )
            imports[module_name] = imported
            local_imports[module_name] = local & _EXPECTED_MODULES

        self.assertEqual(imports, _EXPECTED_FOUNDATION_IMPORTS)

        for start in _FOUNDATION_LEAVES:
            reachable = list(local_imports[start] & _FOUNDATION_LEAVES)
            visited: set[str] = set()
            while reachable:
                candidate = reachable.pop()
                self.assertNotEqual(
                    candidate,
                    start,
                    f"foundation import cycle returns to {start}",
                )
                if candidate in visited:
                    continue
                visited.add(candidate)
                reachable.extend(
                    local_imports[candidate] & _FOUNDATION_LEAVES
                )


if __name__ == "__main__":
    unittest.main()
