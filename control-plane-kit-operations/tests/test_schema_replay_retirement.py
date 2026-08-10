from __future__ import annotations

import ast
import hashlib
import inspect
import unittest

from control_plane_kit_operations.postgres import migration_inspection
from control_plane_kit_operations.postgres import migration_runner
from control_plane_kit_operations.postgres import schema
from tests import graph_lineage_fixture


_RETIRED_NAMES_BY_MODULE = {
    schema: {
        "_CURRENT_POSTGRES_SCHEMA",
        "_GRAPH_LINEAGE_COLUMN_COMPATIBILITY",
    },
    migration_runner: {"_CURRENT_POSTGRES_SCHEMA"},
    migration_inspection: {
        "_V1_COLUMNS_BY_TABLE",
        "_historical_append_order",
        "_V1_HISTORICAL_COLUMN_ORDERS",
        "_V1_PRE_GRAPH_LINEAGE_COLUMN_ORDERS",
        "_is_accepted_current_manifest",
    },
}
_HISTORICAL_FIXTURE_SHA256 = (
    "76404858922b834b44b0adcdccfc8a7d841ab961932596ef5bfec911e361f568"
)


class SchemaReplayRetirementTests(unittest.TestCase):
    def test_retired_replay_names_are_absent_from_production_ast(self) -> None:
        for module, retired_names in _RETIRED_NAMES_BY_MODULE.items():
            with self.subTest(module=module.__name__):
                inventory = self._identifier_inventory(module)
                self.assertEqual(inventory & retired_names, set())
                for name in retired_names:
                    self.assertFalse(hasattr(module, name))

    def test_historical_fixture_is_test_owned_and_pinned(self) -> None:
        tree = ast.parse(inspect.getsource(graph_lineage_fixture))
        imported_modules = {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
        }
        self.assertNotIn(
            "control_plane_kit_operations.postgres.schema",
            imported_modules,
        )

        specifications = (
            graph_lineage_fixture._HISTORICAL_GRAPH_LINEAGE_CONSTRAINT_SPECS
        )
        self.assertEqual(len(specifications), 10)
        self.assertEqual(
            len({(table, name) for table, name, _ddl in specifications}),
            10,
        )
        encoded = "\n".join("\x1f".join(specification) for specification in specifications)
        self.assertEqual(
            hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
            _HISTORICAL_FIXTURE_SHA256,
        )

    def _identifier_inventory(self, module) -> set[str]:
        tree = ast.parse(inspect.getsource(module))
        inventory = {
            node.id for node in ast.walk(tree) if isinstance(node, ast.Name)
        }
        inventory.update(
            node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
        )
        inventory.update(
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, (ast.Import, ast.ImportFrom))
            for alias in node.names
        )
        inventory.update(
            node.name
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        )
        return inventory


if __name__ == "__main__":
    unittest.main()
