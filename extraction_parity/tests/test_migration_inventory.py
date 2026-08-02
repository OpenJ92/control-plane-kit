from __future__ import annotations

import ast
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from extraction_parity.migration_inventory import (
    MigrationInventoryError,
    SourceLane,
    _method_record,
    build_migration_inventory,
    decode_migration_inventory,
    decode_rules,
    scan_test_root,
)


class MigrationRulesTests(unittest.TestCase):
    def test_rules_reject_duplicate_module_ownership(self) -> None:
        with self.assertRaisesRegex(MigrationInventoryError, "multiple owners"):
            decode_rules(
                {
                    "schema": "cpk.semantic-test-migration-rules",
                    "legacy_script_issue": 1325,
                    "assignments": [
                        {"issue": 1320, "distribution": "core", "modules": ["tests.test_graph"]},
                        {"issue": 1321, "distribution": "operations", "modules": ["tests.test_graph"]},
                    ],
                }
            )

    def test_rules_are_closed(self) -> None:
        value = {
            "schema": "cpk.semantic-test-migration-rules",
            "legacy_script_issue": 1325,
            "assignments": [
                {"issue": 1320, "distribution": "core", "modules": ["tests.test_graph"]}
            ],
        }
        self.assertEqual(decode_rules(value), value)
        with self.assertRaisesRegex(MigrationInventoryError, "unknown or missing"):
            decode_rules({**value, "fallback": "core"})


class MethodInventoryTests(unittest.TestCase):
    def test_snapshot_scan_records_only_configured_test_and_script_roots(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "tests").mkdir()
            (root / "scripts").mkdir()
            (root / "src").mkdir()
            (root / "tests" / "test_values.py").write_text(
                "class ValueTests:\n"
                "    def test_value(self):\n"
                "        self.assertEqual(1, 1)\n",
                encoding="utf-8",
            )
            (root / "tests" / "fixture.py").write_text("VALUE = 1\n", encoding="utf-8")
            (root / "test.sh").write_text("#!/bin/sh\n", encoding="utf-8")
            (root / "scripts" / "setup.sh").write_text("#!/bin/sh\n", encoding="utf-8")
            (root / "src" / "ignored.sh").write_text("#!/bin/sh\n", encoding="utf-8")
            lane = scan_test_root(
                SourceLane(
                    distribution="current",
                    repository="example/current",
                    commit="a" * 40,
                    gate="./test.sh",
                    root=root,
                    test_roots=("tests",),
                    script_roots=("", "scripts"),
                )
            )
        self.assertEqual(
            [method["id"] for method in lane["methods"]],
            ["current:tests.test_values.ValueTests.test_value"],
        )
        self.assertEqual(lane["helpers"], ["tests/fixture.py"])
        self.assertEqual(lane["scripts"], ["scripts/setup.sh", "test.sh"])

    def test_method_record_exposes_negative_and_subtest_hints(self) -> None:
        tree = ast.parse(
            """
def test_rejects_missing_value(self):
    with self.subTest(case='missing'):
        with self.assertRaisesRegex(ValueError, 'missing'):
            call()
"""
        )
        node = tree.body[0]
        self.assertIsInstance(node, ast.FunctionDef)
        record = _method_record(
            distribution="legacy-reference",
            module="tests.test_values",
            path="tests/test_values.py",
            class_name="ValueTests",
            node=node,
            imports=("control_plane_kit",),
        )
        self.assertEqual(record["subtest_dimensions"], ["case"])
        self.assertIn("name:missing", record["negative_case_hints"])
        self.assertIn("name:reject", record["negative_case_hints"])
        self.assertIn("assertion:assertRaisesRegex", record["negative_case_hints"])
        self.assertEqual(record["imports"], ["control_plane_kit"])

    def test_inventory_decoder_rejects_count_drift(self) -> None:
        value = {
            "schema": "cpk.semantic-test-migration-inventory",
            "reference": {"tag": "tag", "commit": "commit"},
            "parity_manifest_digest": "sha256:" + "a" * 64,
            "recorded_parity_counts": {
                "with_successors": 0,
                "with_supersession": 0,
                "without_completion_record": 1,
            },
            "counts": {
                "reference_laws": 1,
                "mutable_only_methods": 0,
                "current_methods": 1,
                "legacy_scripts": 0,
                "legacy_helpers": 0,
                "current_helpers": 0,
                "current_scripts": 0,
            },
            "sources": [],
            "provisional_target_counts": [
                {
                    "issue": 1320,
                    "distribution": "core",
                    "reference_laws": 1,
                    "mutable_only_methods": 0,
                }
            ],
            "legacy_module_imports": [
                {"module": "tests.test_a", "imports": ["control_plane_kit"]}
            ],
            "reference_assignments": [{"reference": "tests.test_a.ATests.test_a"}],
            "mutable_only_methods": [],
            "legacy_helpers": [],
            "legacy_scripts": [],
            "current_helpers": [],
            "current_scripts": [],
            "current_tests": [{"id": "core:tests.test_a.ATests.test_a"}],
        }
        self.assertEqual(decode_migration_inventory(value), value)
        value["counts"]["current_methods"] = 2
        with self.assertRaisesRegex(MigrationInventoryError, "current method count"):
            decode_migration_inventory(value)

    def test_build_requires_exhaustive_module_assignment_and_finds_candidate(self) -> None:
        reference = "tests.test_values.ValueTests.test_rejects_missing"
        reference_method = {
            "id": f"legacy-reference:{reference}",
            "module": "tests.test_values",
            "class": "ValueTests",
            "method": "test_rejects_missing",
            "path": "tests/test_values.py",
            "line": 10,
            "assertions": ["assertRaises"],
            "negative_case_hints": ["name:missing", "name:reject"],
            "subtest_dimensions": [],
            "imports": ["control_plane_kit"],
        }
        mutable_method = {**reference_method, "id": f"legacy-mutable:{reference}"}
        current_method = {
            **reference_method,
            "id": "control-plane-kit-core:tests.test_values.CurrentTests.test_rejects_missing",
            "class": "CurrentTests",
            "imports": ["control_plane_kit_core"],
        }

        def lane(distribution: str, methods: list[dict[str, object]]) -> dict[str, object]:
            return {
                "distribution": distribution,
                "repository": "example/repository",
                "commit": "a" * 40,
                "gate": "./test.sh",
                "test_roots": ["tests"],
                "methods": methods,
                "helpers": [],
                "scripts": [],
            }

        lanes = (
            lane("legacy-reference", [reference_method]),
            lane("legacy-mutable", [mutable_method]),
            lane("control-plane-kit-core", [current_method]),
            lane("control-plane-kit-operations", []),
            lane("control-plane-kit-interpreters", []),
            lane("control-plane-kit-servers", []),
            lane("control-plane-kit-secrets", []),
        )
        reference_tests = {
            "schema": "cpk.reference-test-inventory",
            "reference": {"tag": "tag", "commit": "a" * 40},
            "tests": [{"reference": reference, "law": "behavior.rejects-missing"}],
        }
        manifest = {
            "schema": "cpk.parity-manifest",
            "reference": reference_tests["reference"],
            "entries": [
                {
                    "kind": "test",
                    "reference": reference,
                    "law": "behavior.rejects-missing",
                    "successors": [],
                    "supersession": None,
                }
            ],
        }
        demos = {"schema": "cpk.reference-demo-inventory", "demos": []}
        rules = {
            "schema": "cpk.semantic-test-migration-rules",
            "legacy_script_issue": 1325,
            "assignments": [
                {
                    "issue": 1320,
                    "distribution": "control-plane-kit-core",
                    "modules": ["tests.test_values"],
                }
            ],
        }
        inventory = build_migration_inventory(
            reference_tests=reference_tests,
            manifest=manifest,
            demos=demos,
            rules=rules,
            lanes=lanes,
        )
        assignment = inventory["reference_assignments"][0]
        self.assertEqual(assignment["provisional_target"]["issue"], 1320)
        self.assertEqual(
            assignment["current_successor_candidates"], [current_method["id"]]
        )
        self.assertEqual(
            inventory["legacy_module_imports"],
            [{"module": "tests.test_values", "imports": ["control_plane_kit"]}],
        )

        rules["assignments"][0]["modules"] = ["tests.test_stale"]
        with self.assertRaisesRegex(MigrationInventoryError, "module assignments differ"):
            build_migration_inventory(
                reference_tests=reference_tests,
                manifest=manifest,
                demos=demos,
                rules=rules,
                lanes=lanes,
            )


if __name__ == "__main__":
    unittest.main()
