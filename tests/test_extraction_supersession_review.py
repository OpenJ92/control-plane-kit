from __future__ import annotations

import unittest
from pathlib import Path

from extraction_parity.validation import read_bounded_json


ARTIFACT_ROOT = Path(__file__).parents[1] / "artifacts" / "extraction"


class ExtractionSupersessionReviewTests(unittest.TestCase):
    def test_reviewed_supersession_artifact_matches_manifest_state(self) -> None:
        review_paths = sorted((ARTIFACT_ROOT / "supersession-reviews").glob("*.json"))
        reviews = [read_bounded_json(path) for path in review_paths]
        manifest = read_bounded_json(ARTIFACT_ROOT / "parity-manifest.json")

        self.assertGreaterEqual(len(reviews), 1)
        for review in reviews:
            self.assertEqual(review["schema"], "cpk.supersession-review")
            self.assertIsInstance(review["review"], str)
            self.assertTrue(review["review"])
            self.assertTrue(review["candidate_reviews"])
            self.assertIsInstance(review["reviewed_supersessions"], list)

        issue_732_review = read_bounded_json(
            ARTIFACT_ROOT
            / "supersession-reviews"
            / "extract-e-732-reviewed-supersession-classification.json"
        )
        self.assertEqual(issue_732_review["review"], "issue-732")
        self.assertEqual(issue_732_review["reviewed_supersessions"], [])
        self.assertEqual(
            {
                candidate["decision"]
                for candidate in issue_732_review["candidate_reviews"]
            },
            {"not_superseded"},
        )

        manifest_supersessions = sorted(
            (
                {
                    "law": entry["law"],
                    "reference": entry["reference"],
                }
                for entry in manifest["entries"]
                if entry["supersession"] is not None
            ),
            key=lambda item: (item["law"], item["reference"]),
        )
        reviewed_supersessions = sorted(
            (
                supersession
                for review in reviews
                for supersession in review["reviewed_supersessions"]
            ),
            key=lambda item: (item["law"], item["reference"]),
        )
        self.assertEqual(
            reviewed_supersessions,
            manifest_supersessions,
        )


if __name__ == "__main__":
    unittest.main()
