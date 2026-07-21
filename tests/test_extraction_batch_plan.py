from __future__ import annotations

import unittest
from pathlib import Path

from extraction_parity.validation import read_bounded_json


ARTIFACT_ROOT = Path(__file__).parents[1] / "artifacts" / "extraction"


class ExtractionBatchPlanTests(unittest.TestCase):
    def test_required_core_batch_plan_partitions_inventory_exactly_once(self) -> None:
        inventory = read_bounded_json(ARTIFACT_ROOT / "required-core-family-inventory.json")
        plan = read_bounded_json(ARTIFACT_ROOT / "required-core-batch-plan.json")

        self.assertEqual(plan["schema"], "cpk.required-core-batch-plan")
        planned_family_records = [
            family
            for batch in plan["batches"].values()
            for family in batch["families"]
        ]
        planned_families = [family["family"] for family in planned_family_records]
        planned_reference_count = sum(
            len(family["references"]) for family in planned_family_records
        )
        planned_family_counts = sum(family["count"] for family in planned_family_records)

        self.assertEqual(plan["source_counts"]["entries"], planned_reference_count)
        self.assertEqual(plan["source_counts"]["entries"], planned_family_counts)
        self.assertEqual(plan["source_counts"]["families"], len(planned_family_records))
        self.assertEqual(plan["totals"]["entries"], plan["source_counts"]["entries"])
        self.assertEqual(plan["totals"]["families"], plan["source_counts"]["families"])

        self.assertEqual(len(planned_families), len(set(planned_families)))
        self.assertEqual(
            sorted(plan["totals"]["issues"]),
            ["#738", "#739", "#740", "#741", "#742", "#743"],
        )

        inventory_families = {family["family"] for family in inventory["families"]}
        planned_family_set = set(planned_families)
        self.assertLessEqual(
            inventory["counts"]["entries"],
            plan["source_counts"]["entries"],
        )
        self.assertLessEqual(
            inventory["counts"]["families"],
            plan["source_counts"]["families"],
        )
        self.assertLessEqual(inventory_families, planned_family_set)

        planned_references = {
            reference
            for family in planned_family_records
            for reference in family["references"]
        }
        inventory_references = {
            entry["reference"]
            for family in inventory["families"]
            for entry in family["entries"]
        }
        self.assertLessEqual(inventory_references, planned_references)


if __name__ == "__main__":
    unittest.main()
