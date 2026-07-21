from __future__ import annotations

import unittest
from pathlib import Path

from extraction_parity.validation import read_bounded_json


ARTIFACT_ROOT = Path(__file__).parents[1] / "artifacts" / "extraction"


class ExtractionSupersessionReviewTests(unittest.TestCase):
    def test_reviewed_supersession_artifact_matches_manifest_state(self) -> None:
        review = read_bounded_json(
            ARTIFACT_ROOT
            / "supersession-reviews"
            / "extract-e-732-reviewed-supersession-classification.json"
        )
        manifest = read_bounded_json(ARTIFACT_ROOT / "parity-manifest.json")

        self.assertEqual(review["schema"], "cpk.supersession-review")
        self.assertEqual(review["review"], "issue-732")
        self.assertEqual(review["reviewed_supersessions"], [])
        self.assertTrue(review["candidate_reviews"])
        self.assertEqual(
            {candidate["decision"] for candidate in review["candidate_reviews"]},
            {"not_superseded"},
        )
        self.assertEqual(
            [
                entry
                for entry in manifest["entries"]
                if entry["supersession"] is not None
            ],
            [],
        )


if __name__ == "__main__":
    unittest.main()
