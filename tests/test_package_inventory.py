from __future__ import annotations

import json
from pathlib import Path
import sys
import unittest

from tests.architecture import analyze_file


OWNERS = {
    "core",
    "domain",
    "operation",
    "interpreter",
    "product",
    "entrypoint",
}
REQUIRED_FIELDS = {
    "module",
    "source",
    "owner",
    "destination",
    "role",
    "motivation",
    "internal_dependencies",
    "optional_external_dependencies",
    "semantic_roles",
    "canonical_public_exports",
    "known_package_consumers",
    "migration_prerequisites",
    "movement",
    "protecting_tests",
}


class PackageModuleInventoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(__file__).parents[1]
        path = self.root / "docs/architecture/package-module-inventory.json"
        self.document = json.loads(path.read_text(encoding="utf-8"))
        self.records = self.document["modules"]

    def test_inventory_is_exhaustive_and_assigns_one_owner(self) -> None:
        actual = {
            self._module_name(path)
            for path in (self.root / "control_plane_kit").rglob("*.py")
        }
        recorded = [record["module"] for record in self.records]

        self.assertEqual(len(recorded), len(set(recorded)))
        self.assertEqual(set(recorded), actual)
        self.assertEqual(
            self.document["owner_vocabulary"],
            ["core", "domain", "operation", "interpreter", "product", "entrypoint"],
        )
        for record in self.records:
            self.assertEqual(set(record), REQUIRED_FIELDS | self._server_fields(record))
            self.assertIn(record["owner"], OWNERS)
            self.assertTrue(record["destination"].startswith("control_plane_kit"))
            self.assertTrue(record["motivation"])
            self.assertTrue(record["migration_prerequisites"])
            self.assertTrue(record["protecting_tests"])
            self.assertTrue((self.root / record["source"]).is_file())
            self.assertTrue(
                all((self.root / path).is_file() for path in record["protecting_tests"])
            )
            self.assertTrue(
                set(record["known_package_consumers"]).issubset(actual)
            )

    def test_every_current_server_module_has_uniform_product_exterior(self) -> None:
        server_records = [
            record
            for record in self.records
            if record["module"] == "control_plane_kit.servers"
            or record["module"].startswith("control_plane_kit.servers.")
        ]

        self.assertTrue(server_records)
        for record in server_records:
            self.assertEqual(record["owner"], "product")
            self.assertEqual(
                record["server_product_exterior"],
                "control_plane_kit.products.servers",
            )
            self.assertEqual(
                record["forbidden_product_imports"],
                [
                    "FastAPI apps",
                    "stores",
                    "UnitOfWork",
                    "HTTP/Docker clients",
                    "process bootstrap",
                ],
            )

    def test_core_inventory_is_exactly_the_effect_free_pipeline_floor(self) -> None:
        core = {record["module"] for record in self.records if record["owner"] == "core"}

        self.assertIn("control_plane_kit.core.topology.graph", core)
        self.assertIn("control_plane_kit.core.planning.compiler", core)
        self.assertIn("control_plane_kit.core.verification", core)
        self.assertNotIn("control_plane_kit.contracts", core)
        self.assertNotIn("control_plane_kit.saga.state", core)
        self.assertNotIn("control_plane_kit.execution.values", core)
        self.assertNotIn("control_plane_kit.operations.planning.recovery", core)

    def test_domain_inventory_is_exactly_the_admitted_closed_languages(self) -> None:
        domains = {record["destination"] for record in self.records if record["owner"] == "domain"}

        self.assertEqual(
            domains,
            {
                "control_plane_kit.domains",
                "control_plane_kit.domains.discovery",
                "control_plane_kit.domains.webhook",
                "control_plane_kit.domains.webhook.language",
                "control_plane_kit.domains.idempotency",
                "control_plane_kit.domains.load_generation",
            },
        )

    def test_coredns_relocation_invariants_are_recorded(self) -> None:
        record = next(
            value
            for value in self.records
            if value["module"] == "control_plane_kit.products.servers.coredns"
        )

        self.assertEqual(record["destination"], "control_plane_kit.products.servers.coredns")
        self.assertIs(record["domain_qualification"]["qualifies"], False)
        self.assertIn("control_plane_kit.domains.discovery", record["internal_dependencies"])
        self.assertIn("tests/test_coredns.py", record["protecting_tests"])

    def test_current_core_tree_has_only_declared_pure_dependencies(self) -> None:
        core_paths = tuple(
            sorted((self.root / "control_plane_kit" / "core").rglob("*.py"))
        )
        facts = tuple(
            analyze_file(path, root=self.root)
            for path in core_paths
        )
        external = {
            imported.qualified_name.split(".", 1)[0]
            for source in facts
            for imported in source.imports
            if not imported.qualified_name.startswith("control_plane_kit.core")
            and imported.qualified_name.split(".", 1)[0]
            not in sys.stdlib_module_names
            and imported.qualified_name != "__future__.annotations"
        }
        internal = {
            imported.qualified_name
            for source in facts
            for imported in source.imports
            if imported.qualified_name.startswith("control_plane_kit")
        }

        self.assertEqual(external, {"yaml"})
        self.assertTrue(
            all(value.startswith("control_plane_kit.core") for value in internal)
        )

    def _module_name(self, path: Path) -> str:
        relative = path.relative_to(self.root).with_suffix("")
        parts = list(relative.parts)
        if parts[-1] == "__init__":
            parts.pop()
        return ".".join(parts)

    @staticmethod
    def _server_fields(record: dict[str, object]) -> set[str]:
        if record["owner"] != "product":
            return set()
        return {
            "server_product_exterior",
            "domain_qualification",
            "forbidden_product_imports",
        }


if __name__ == "__main__":
    unittest.main()
