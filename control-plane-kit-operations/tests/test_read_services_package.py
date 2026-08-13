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
    "authority_secrets",
    "errors",
    "instance",
    "models",
    "observations",
    "operations_history",
    "protocols",
    "workspace_graph",
}

_PUBLIC_OWNERS = {
    "ControlSurfaceReadModel": "workspace_graph",
    "FocusedDetailReadModel": "models",
    "GraphPointerReadModel": "workspace_graph",
    "InstanceReadService": "instance",
    "ObservationFreshnessPolicy": "observations",
    "ProjectedObservation": "observations",
    "ReadModelError": "errors",
    "WorkspaceReadModel": "workspace_graph",
    "WorkspaceSummary": "workspace_graph",
    "project_observation": "observations",
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


def _local_module_imports(
    tree: ast.AST,
    module_names: set[str],
) -> set[str]:
    local: set[str] = set()
    package = "control_plane_kit_operations.read_services"
    prefix = "control_plane_kit_operations.read_services."
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.level:
                candidates = (
                    (node.module.split(".", 1)[0],)
                    if node.module
                    else tuple(alias.name.split(".", 1)[0] for alias in node.names)
                )
                local.update(
                    candidate
                    for candidate in candidates
                    if candidate in module_names
                )
            elif node.module == package:
                local.update(
                    alias.name.split(".", 1)[0]
                    for alias in node.names
                    if alias.name.split(".", 1)[0] in module_names
                )
            elif node.module and node.module.startswith(prefix):
                candidate = node.module.removeprefix(prefix).split(".", 1)[0]
                if candidate in module_names:
                    local.add(candidate)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith(prefix):
                    candidate = alias.name.removeprefix(prefix).split(".", 1)[0]
                    if candidate in module_names:
                        local.add(candidate)
    return local


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
                for name in {
                    "errors",
                    "instance",
                    "models",
                    "observations",
                    "protocols",
                    "workspace_graph",
                }
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

    def test_workspace_graph_owns_exact_family_without_a_facade_edge(self) -> None:
        paths = tuple(getattr(read_services, "__path__", ()))
        self.assertEqual(len(paths), 1, "read_services must be one installed package")
        package_path = Path(paths[0])
        for module_name in _EXPECTED_MODULES - {"__init__"}:
            self.assertTrue(
                (package_path / f"{module_name}.py").is_file(),
                f"read-services module {module_name!r} is absent",
            )

        trees = {
            module_name: ast.parse(
                (package_path / f"{module_name}.py").read_text(encoding="utf-8")
            )
            for module_name in _EXPECTED_MODULES - {"__init__"}
        }
        instance_classes = {
            node.name
            for node in trees["instance"].body
            if isinstance(node, ast.ClassDef)
        }
        workspace_classes = {
            node.name
            for node in trees["workspace_graph"].body
            if isinstance(node, ast.ClassDef)
        }
        moved = {
            "ControlSurfaceReadModel",
            "GraphPointerReadModel",
            "WorkspaceReadModel",
            "WorkspaceSummary",
        }
        self.assertTrue(moved <= workspace_classes)
        self.assertTrue(moved.isdisjoint(instance_classes))

        workspace_functions = {
            node.name
            for node in trees["workspace_graph"].body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        instance_functions = {
            node.name
            for node in trees["instance"].body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        self.assertIn("_list", workspace_functions)
        self.assertNotIn("_list", instance_functions)
        self.assertIn("_mapping", workspace_functions)
        self.assertIn("_mapping", instance_functions)
        self.assertNotIn("_recovery_for_plan", workspace_functions)

        projection = next(
            node
            for node in trees["workspace_graph"].body
            if isinstance(node, ast.ClassDef)
            and node.name == "_WorkspaceGraphReadProjection"
        )
        self.assertEqual(
            {
                node.name
                for node in projection.body
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            },
            {
                "__init__",
                "workspace",
                "current_graph",
                "desired_graph",
                "operator_graph",
                "control_surface",
                "require_workspace",
                "_graph_pointer",
            },
        )

        imported = {
            node.module or ""
            for node in ast.walk(trees["workspace_graph"])
            if isinstance(node, ast.ImportFrom)
        } | {
            alias.name
            for node in ast.walk(trees["workspace_graph"])
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        self.assertFalse(
            any(name.endswith(".instance") or name == "instance" for name in imported)
        )
        self.assertFalse(any("planning" in name for name in imported))

    def test_local_import_parser_resolves_alias_import_forms(self) -> None:
        candidates = (
            "from .instance import InstanceReadService\n",
            "from . import instance\n",
            (
                "from control_plane_kit_operations.read_services.instance "
                "import InstanceReadService\n"
            ),
            (
                "from control_plane_kit_operations.read_services "
                "import instance\n"
            ),
            "import control_plane_kit_operations.read_services.instance\n",
        )
        for source in candidates:
            with self.subTest(source=source):
                self.assertEqual(
                    _local_module_imports(
                        ast.parse(source),
                        {"instance", "workspace_graph"},
                    ),
                    {"instance"},
                )

    def test_current_local_module_graph_is_acyclic(self) -> None:
        paths = tuple(getattr(read_services, "__path__", ()))
        self.assertEqual(len(paths), 1, "read_services must be one installed package")
        package_path = Path(paths[0])
        module_names = _EXPECTED_MODULES - {"__init__"}
        for module_name in module_names:
            self.assertTrue(
                (package_path / f"{module_name}.py").is_file(),
                f"read-services module {module_name!r} is absent",
            )
        edges: dict[str, set[str]] = {name: set() for name in module_names}
        for module_name in module_names:
            tree = ast.parse(
                (package_path / f"{module_name}.py").read_text(encoding="utf-8")
            )
            edges[module_name] = _local_module_imports(tree, module_names)

        self.assertNotIn("instance", edges["workspace_graph"])
        for start in module_names:
            pending = list(edges[start])
            visited: set[str] = set()
            while pending:
                candidate = pending.pop()
                self.assertNotEqual(candidate, start, f"import cycle returns to {start}")
                if candidate in visited:
                    continue
                visited.add(candidate)
                pending.extend(edges[candidate])

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
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    if not node.level and node.module:
                        imported.add(node.module)
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        imported.add(alias.name)
            imports[module_name] = imported
            local_imports[module_name] = _local_module_imports(
                tree,
                _EXPECTED_MODULES,
            )

        self.assertEqual(imports, _EXPECTED_FOUNDATION_IMPORTS)

        for start in _FOUNDATION_LEAVES:
            self.assertNotIn(
                "instance",
                local_imports[start],
                f"foundation leaf {start} must not import the service facade",
            )
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
