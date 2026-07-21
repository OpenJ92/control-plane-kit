from __future__ import annotations

import unittest
from pathlib import Path

from extraction_parity.validation import read_bounded_json


ARTIFACT_ROOT = Path(__file__).parents[1] / "artifacts" / "extraction"


class ExtractionPureCoreAuditTests(unittest.TestCase):
    def test_pure_core_audit_covers_every_assigned_family_once(self) -> None:
        plan = read_bounded_json(ARTIFACT_ROOT / "required-core-batch-plan.json")
        audit = read_bounded_json(ARTIFACT_ROOT / "pure-core-batch-audit.json")

        source_families = {
            family["family"]: family["count"]
            for family in plan["batches"]["pure_core_language"]["families"]
        }
        audited_families = audit["families"]

        self.assertEqual(audit["schema"], "cpk.required-core-batch-audit")
        self.assertEqual(audit["source_batch"], "pure_core_language")
        self.assertEqual(set(audited_families), set(source_families))

        for family, source_count in source_families.items():
            self.assertEqual(audited_families[family]["count"], source_count)

    def test_pure_core_audit_moves_only_to_active_mapping_batches(self) -> None:
        audit = read_bounded_json(ARTIFACT_ROOT / "pure-core-batch-audit.json")
        active_mapping_issues = {"#740", "#743", "#746", "#747", "#748", "#749"}

        decisions = {record["decision"] for record in audit["families"].values()}
        self.assertEqual(decisions, {"retain", "move", "split"})

        for record in audit["families"].values():
            if record["decision"] in {"retain", "move"}:
                self.assertIn(record["target_issue"], active_mapping_issues)
            else:
                self.assertEqual(record["decision"], "split")
                self.assertTrue(set(record["target_issues"]).issubset(active_mapping_issues))

    def test_pure_core_audit_summary_matches_records(self) -> None:
        audit = read_bounded_json(ARTIFACT_ROOT / "pure-core-batch-audit.json")
        records = tuple(audit["families"].values())

        self.assertEqual(audit["summary"]["families"], len(records))
        self.assertEqual(audit["summary"]["entries"], sum(record["count"] for record in records))
        self.assertEqual(
            audit["summary"]["retained_families"],
            sum(1 for record in records if record["decision"] == "retain"),
        )
        self.assertEqual(
            audit["summary"]["moved_families"],
            sum(1 for record in records if record["decision"] == "move"),
        )
        self.assertEqual(
            audit["summary"]["split_families"],
            sum(1 for record in records if record["decision"] == "split"),
        )


if __name__ == "__main__":
    unittest.main()
