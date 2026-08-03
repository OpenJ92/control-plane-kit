from __future__ import annotations

import json
from pathlib import Path
import unittest

from extraction_parity.migration_inventory import SourceLane, scan_test_root
from extraction_parity.reconciliation_builder import _decode_decisions
from extraction_parity.reconciliation import (
    ReconciliationError,
    decode_reconciliation,
    validate_reconciliation,
)


def _inventory() -> dict[str, object]:
    return {
        "schema": "cpk.semantic-test-migration-inventory",
        "reference_assignments": [
            {
                "reference": "tests.test_example.ExampleTests.test_law",
                "law": "behavior.example-law",
                "provisional_target": {
                    "issue": 1320,
                    "distribution": "control-plane-kit-core",
                },
            },
            {
                "reference": "tests.test_future.FutureTests.test_law",
                "law": "behavior.future-law",
                "provisional_target": {
                    "issue": 1320,
                    "distribution": "control-plane-kit-core",
                },
            },
        ],
        "mutable_only_methods": [],
    }


def _manifest() -> dict[str, object]:
    return {
        "schema": "cpk.parity-manifest",
        "entries": [
            {
                "kind": "test",
                "reference": "tests.test_example.ExampleTests.test_law",
                "law": "behavior.example-law",
                "successors": [],
                "supersession": None,
            },
            {
                "kind": "test",
                "reference": "tests.test_future.FutureTests.test_law",
                "law": "behavior.future-law",
                "successors": [],
                "supersession": None,
            },
        ],
    }


def _document() -> dict[str, object]:
    return {
        "schema": "cpk.semantic-test-reconciliation",
        "reviews": [
            {
                "reference": "tests.test_example.ExampleTests.test_law",
                "law": "behavior.example-law",
                "reviewed_by_issue": 1320,
                "owner": "control-plane-kit-core",
                "disposition": "current-isomorphic",
                "current_tests": [
                    "control-plane-kit-core:tests.test_current.CurrentTests.test_law"
                ],
                "future_issue": None,
                "rationale": "The current pure test preserves the law.",
                "negative_case_disposition": "The rejection remains explicit.",
                "obsolete_assumption_disposition": "The aggregate import path is discarded.",
            },
            {
                "reference": "tests.test_future.FutureTests.test_law",
                "law": "behavior.future-law",
                "reviewed_by_issue": 1320,
                "owner": "OpenJ92/control-plane-kit#1096",
                "disposition": "future-issue",
                "current_tests": [],
                "future_issue": {
                    "repository": "OpenJ92/control-plane-kit",
                    "number": 1096,
                    "state": "open",
                    "evidence": "Issue body owns the exact law and negative cases.",
                },
                "rationale": "The application program is intentionally future-owned.",
                "negative_case_disposition": "The future issue retains invalid forms.",
                "obsolete_assumption_disposition": "The frozen application module is not restored.",
            },
        ],
        "mutable_only_reviews": [],
    }


class SemanticReconciliationTests(unittest.TestCase):
    def test_decision_slice_accepts_multiple_unique_current_distributions(self) -> None:
        document = {
            "schema": "cpk.semantic-test-reconciliation-decisions",
            "slices": [
                {
                    "issue": 1321,
                    "current_distributions": [
                        "control-plane-kit-core",
                        "control-plane-kit-operations",
                    ],
                    "evidence_id": "operations-evidence",
                    "default_disposition": "current-isomorphic",
                    "strengthened_references": [],
                    "successor_overrides": {},
                    "future_issue_reviews": {},
                    "non_current_reviews": {},
                }
            ],
        }

        self.assertIs(_decode_decisions(document), document)

    def test_decision_slice_rejects_empty_or_duplicate_distributions(self) -> None:
        document = {
            "schema": "cpk.semantic-test-reconciliation-decisions",
            "slices": [
                {
                    "issue": 1321,
                    "current_distributions": [],
                    "evidence_id": "operations-evidence",
                    "default_disposition": "current-isomorphic",
                    "strengthened_references": [],
                    "successor_overrides": {},
                    "future_issue_reviews": {},
                    "non_current_reviews": {},
                }
            ],
        }
        with self.assertRaisesRegex(ReconciliationError, "current distributions"):
            _decode_decisions(document)

        document["slices"][0]["current_distributions"] = [
            "control-plane-kit-operations",
            "control-plane-kit-operations",
        ]
        with self.assertRaisesRegex(ReconciliationError, "current distributions"):
            _decode_decisions(document)

    def test_closed_reconciliation_accepts_current_and_future_dispositions(self) -> None:
        decoded = decode_reconciliation(_document())

        self.assertEqual(len(decoded["reviews"]), 2)

    def test_validation_rejects_nonexistent_current_test(self) -> None:
        with self.assertRaisesRegex(ReconciliationError, "nonexistent current test"):
            validate_reconciliation(
                _document(),
                _inventory(),
                _manifest(),
                current_test_ids=frozenset(),
                issue=1320,
            )

    def test_validation_rejects_law_drift_and_duplicate_ownership(self) -> None:
        document = _document()
        document["reviews"][0]["law"] = "behavior.changed"
        document["reviews"].append(dict(document["reviews"][1]))

        with self.assertRaisesRegex(ReconciliationError, "duplicate review reference"):
            decode_reconciliation(document)

        document["reviews"].pop()
        with self.assertRaisesRegex(ReconciliationError, "law differs"):
            validate_reconciliation(
                document,
                _inventory(),
                _manifest(),
                current_test_ids=frozenset(
                    {
                        "control-plane-kit-core:tests.test_current.CurrentTests.test_law"
                    }
                ),
                issue=1320,
            )

    def test_future_issue_must_be_open_and_current_disposition_must_name_tests(self) -> None:
        document = _document()
        document["reviews"][1]["future_issue"]["state"] = "closed"
        with self.assertRaisesRegex(ReconciliationError, "future issue must be open"):
            decode_reconciliation(document)

        document = _document()
        document["reviews"][0]["current_tests"] = []
        with self.assertRaisesRegex(ReconciliationError, "must name current tests"):
            decode_reconciliation(document)

    def test_issue_slice_requires_exactly_one_review_per_assigned_law(self) -> None:
        document = _document()
        document["reviews"].pop()

        with self.assertRaisesRegex(ReconciliationError, "missing assigned reviews"):
            validate_reconciliation(
                document,
                _inventory(),
                _manifest(),
                current_test_ids=frozenset(
                    {
                        "control-plane-kit-core:tests.test_current.CurrentTests.test_law"
                    }
                ),
                issue=1320,
            )

    def test_issue_1320_artifact_names_only_live_core_tests(self) -> None:
        root = Path(__file__).resolve().parents[2]
        load = lambda name: json.loads(
            (root / "artifacts/extraction" / name).read_text(encoding="utf-8")
        )
        lane = scan_test_root(
            SourceLane(
                distribution="control-plane-kit-core",
                repository="working-tree",
                commit="working-tree",
                gate="focused-unittest",
                root=root,
                test_roots=("control-plane-kit-core/tests",),
            )
        )

        report = validate_reconciliation(
            load("semantic-test-reconciliation.json"),
            load("semantic-test-migration-inventory.json"),
            load("parity-manifest.json"),
            current_test_ids=frozenset(
                str(value["id"]) for value in lane["methods"]
            ),
            issue=1320,
        )

        self.assertTrue(report["valid"])
        self.assertEqual(
            report["counts"],
            {
                "reviews": 231,
                "mutable_only_reviews": 2,
                "current": 227,
                "future_issue": 4,
                "reviewed_non_current": 0,
            },
        )

    def test_issue_1321_artifact_names_only_live_core_and_operations_tests(
        self,
    ) -> None:
        root = Path(__file__).resolve().parents[2]
        load = lambda name: json.loads(
            (root / "artifacts/extraction" / name).read_text(encoding="utf-8")
        )
        current_ids = set()
        for distribution, test_roots in (
            ("control-plane-kit-core", ("control-plane-kit-core/tests",)),
            (
                "control-plane-kit-operations",
                ("control-plane-kit-operations/tests",),
            ),
        ):
            lane = scan_test_root(
                SourceLane(
                    distribution=distribution,
                    repository="working-tree",
                    commit="working-tree",
                    gate="focused-unittest",
                    root=root,
                    test_roots=test_roots,
                )
            )
            current_ids.update(str(value["id"]) for value in lane["methods"])

        report = validate_reconciliation(
            load("semantic-test-reconciliation.json"),
            load("semantic-test-migration-inventory.json"),
            load("parity-manifest.json"),
            current_test_ids=frozenset(current_ids),
            issue=1321,
        )

        self.assertTrue(report["valid"])
        self.assertEqual(
            report["counts"],
            {
                "reviews": 303,
                "mutable_only_reviews": 0,
                "current": 239,
                "future_issue": 63,
                "reviewed_non_current": 1,
            },
        )


if __name__ == "__main__":
    unittest.main()
