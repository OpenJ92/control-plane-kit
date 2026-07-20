from __future__ import annotations

import unittest
from pathlib import Path

from extraction_parity.inventory import (
    CollectedTest,
    InventoryError,
    canonical_reference,
    inventory_descriptor,
    semantic_law_id,
    subtest_dimensions,
    validate_inventory,
)


class ReferenceTestInventoryTests(unittest.TestCase):
    def test_optional_discovery_package_prefix_canonicalizes_to_one_reference(self) -> None:
        self.assertEqual(
            canonical_reference("test_effect_dispatch.EffectDispatchTests.test_attempted"),
            "tests.test_effect_dispatch.EffectDispatchTests.test_attempted",
        )
        self.assertEqual(
            canonical_reference("tests.test_effect_dispatch.EffectDispatchTests.test_attempted"),
            "tests.test_effect_dispatch.EffectDispatchTests.test_attempted",
        )

    def test_law_identity_uses_behavior_name_not_module_or_class(self) -> None:
        first = "tests.test_graph_diff.GraphDiffTests.test_socket_change_is_structural"
        moved = "new.tests.anywhere.RenamedTests.test_socket_change_is_structural"

        self.assertEqual(semantic_law_id(first), "behavior.socket-change-is-structural")
        self.assertEqual(semantic_law_id(moved), semantic_law_id(first))

    def test_ambiguous_or_invalid_test_names_fail_closed(self) -> None:
        with self.assertRaises(InventoryError):
            semantic_law_id("tests.module.Case.not_a_test")
        with self.assertRaises(InventoryError):
            semantic_law_id(
                "tests.a.Case.test_empty",
                ambiguous_method_names=frozenset({"test_empty"}),
            )

        self.assertEqual(
            semantic_law_id(
                "tests.a.Case.test_empty",
                overrides={"tests.a.Case.test_empty": "behavior.graph.empty-is-valid"},
            ),
            "behavior.graph.empty-is-valid",
        )

    def test_subtest_dimensions_preserve_parameter_names(self) -> None:
        source = """
def test_matrix(self):
    for protocol in protocols:
        with self.subTest(protocol=protocol, address=address):
            self.assertTrue(protocol)
"""
        self.assertEqual(subtest_dimensions(source), ("address", "protocol"))

    def test_inventory_rejects_duplicate_reference_and_law_identity(self) -> None:
        first = CollectedTest("tests.a.Case.test_a", "behavior.a", (), None)
        second_reference = CollectedTest("tests.a.Case.test_a", "behavior.b", (), None)
        second_law = CollectedTest("tests.b.Case.test_b", "behavior.a", (), None)

        with self.assertRaises(InventoryError):
            validate_inventory((first, second_reference))
        with self.assertRaises(InventoryError):
            validate_inventory((first, second_law))

    def test_descriptor_is_sorted_and_closed(self) -> None:
        tests = (
            CollectedTest("tests.z.Case.test_z", "behavior.z", (), None),
            CollectedTest("tests.a.Case.test_a", "behavior.a", ("case",), None),
        )
        descriptor = inventory_descriptor(
            reference_tag="tag", reference_commit="commit", tests=tests
        )

        self.assertEqual(descriptor["schema"], "cpk.reference-test-inventory")
        self.assertEqual(descriptor["count"], 2)
        self.assertEqual(descriptor["law_count"], 2)
        self.assertEqual(
            [entry["reference"] for entry in descriptor["tests"]],
            ["tests.a.Case.test_a", "tests.z.Case.test_z"],
        )

    def test_descriptor_preserves_duplicate_collection_as_one_law_with_occurrences(self) -> None:
        test = CollectedTest(
            "tests.a.Case.test_a", "behavior.a", (), None, collection_occurrences=3
        )
        descriptor = inventory_descriptor(
            reference_tag="tag", reference_commit="commit", tests=(test,)
        )

        self.assertEqual(descriptor["count"], 3)
        self.assertEqual(descriptor["law_count"], 1)
        self.assertEqual(descriptor["tests"][0]["collection_occurrences"], 3)

    def test_inventory_runner_uses_the_frozen_archive(self) -> None:
        runner = (Path(__file__).parents[1] / "reference-inventory.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn('git -C "$ROOT_DIR" archive "$REFERENCE_TAG"', runner)
        self.assertIn("--target test", runner)
        self.assertNotIn("docker system prune", runner)
        self.assertNotIn("pip install -e", runner)


if __name__ == "__main__":
    unittest.main()
