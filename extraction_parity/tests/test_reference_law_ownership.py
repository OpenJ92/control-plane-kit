from __future__ import annotations

import unittest

from extraction_parity.ownership import (
    LawOwner,
    OwnerKind,
    OwnershipError,
    classify_inventory,
    classify_module,
)


RULES = {
    "hello": ["test_hello_runtime"],
    "system": ["test_cross_repository"],
    "deferred_products": {"coredns": ["test_coredns"]},
    "guarded_product_terms": ["coredns", "server_block"],
}


class ReferenceLawOwnershipTests(unittest.TestCase):
    def test_closed_owner_kinds_and_named_product(self) -> None:
        self.assertEqual(classify_module("test_graph_diff", RULES), LawOwner(OwnerKind.CORE, "control-plane-kit-core"))
        self.assertEqual(classify_module("test_hello_runtime", RULES), LawOwner(OwnerKind.HELLO, "control-plane-kit-servers:hello"))
        self.assertEqual(classify_module("test_cross_repository", RULES), LawOwner(OwnerKind.SYSTEM, "control-plane-kit-test:cross-repository"))
        self.assertEqual(classify_module("test_coredns", RULES), LawOwner(OwnerKind.DEFERRED_PRODUCT, "control-plane-kit-servers:coredns"))

    def test_unlisted_product_vocabulary_cannot_fall_through_to_core(self) -> None:
        with self.assertRaises(OwnershipError):
            classify_module("test_unknown_server_block", RULES)

    def test_inventory_preserves_occurrences_and_assigns_exactly_one_owner(self) -> None:
        inventory = {
            "schema": "cpk.reference-test-inventory",
            "reference": {"tag": "tag", "commit": "commit"},
            "count": 3,
            "law_count": 2,
            "skipped": 0,
            "tests": [
                {"reference": "tests.test_graph_diff.Case.test_a", "law": "behavior.a", "dimensions": [], "skip": None, "collection_occurrences": 2},
                {"reference": "tests.test_coredns.Case.test_b", "law": "behavior.b", "dimensions": [], "skip": None, "collection_occurrences": 1},
            ],
        }
        result = classify_inventory(inventory, RULES)

        self.assertEqual(result["count"], 3)
        self.assertEqual(result["law_count"], 2)
        self.assertEqual({entry["owner_kind"] for entry in result["laws"]}, {"core", "deferred-product"})


if __name__ == "__main__":
    unittest.main()
