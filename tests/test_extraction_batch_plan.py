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
        self.assertEqual(plan["source_counts"], inventory["counts"])
        self.assertEqual(plan["totals"]["entries"], inventory["counts"]["entries"])
        self.assertEqual(plan["totals"]["families"], inventory["counts"]["families"])

        inventory_families = {family["family"] for family in inventory["families"]}
        planned_families = [
            family["family"]
            for batch in plan["batches"].values()
            for family in batch["families"]
        ]

        self.assertEqual(set(planned_families), inventory_families)
        self.assertEqual(len(planned_families), len(set(planned_families)))
        self.assertEqual(
            sorted(plan["totals"]["issues"]),
            ["#738", "#739", "#740", "#741", "#742", "#743"],
        )


if __name__ == "__main__":
    unittest.main()
