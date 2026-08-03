from __future__ import annotations

import hashlib
import json
from pathlib import Path
import unittest

from extraction_parity.completion import (
    CompletionError,
    validate_semantic_completion,
)


def _digest(value: dict[str, object]) -> str:
    payload = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _supersession() -> dict[str, str]:
    return {
        "rationale": "Old structure is replaced by a stronger boundary.",
        "review": "#1326",
        "obsolete_assumption": "The old package path remains authoritative.",
        "replacement": "The current package gate owns the behavior.",
        "negative_case_disposition": "The current boundary test rejects the old path.",
    }


def _documents() -> tuple[dict[str, object], ...]:
    manifest = {
        "schema": "cpk.parity-manifest",
        "reference": {"tag": "reference-tag", "commit": "reference-commit"},
        "entries": [
            {
                "kind": "test",
                "reference": "tests.current.Case.test_law",
                "law": "behavior.current-law",
                "owner_kind": "core",
                "owner": "control-plane-kit-core",
                "migration_state": "required",
                "successors": [
                    {
                        "id": "control-plane-kit-core:tests.test_current.Case.test_law",
                        "status": "passing",
                        "evidence": "current-evidence",
                    }
                ],
                "supersession": None,
            },
            {
                "kind": "test",
                "reference": "tests.future.Case.test_law",
                "law": "behavior.future-law",
                "owner_kind": "system",
                "owner": "control-plane-kit-test:cross-repository",
                "migration_state": "required",
                "successors": [],
                "supersession": None,
            },
            {
                "kind": "demo",
                "reference": "demo.superseded",
                "law": "demo.superseded",
                "owner_kind": "core",
                "owner": "control-plane-kit-core",
                "migration_state": "required",
                "successors": [],
                "supersession": _supersession(),
            },
        ],
    }
    reconciliation = {
        "schema": "cpk.semantic-test-reconciliation",
        "reviews": [
            {
                "reference": "tests.current.Case.test_law",
                "law": "behavior.current-law",
                "reviewed_by_issue": 1320,
                "owner": "control-plane-kit-core",
                "disposition": "current-strengthened",
                "current_tests": [
                    "control-plane-kit-core:tests.test_current.Case.test_law"
                ],
                "future_issue": None,
                "rationale": "The current package proves a stronger law.",
                "negative_case_disposition": "Invalid input still fails closed.",
                "obsolete_assumption_disposition": "The old package path is discarded.",
            },
            {
                "reference": "tests.future.Case.test_law",
                "law": "behavior.future-law",
                "reviewed_by_issue": 1325,
                "owner": "OpenJ92/control-plane-kit#9000",
                "disposition": "future-issue",
                "current_tests": [],
                "future_issue": {
                    "repository": "OpenJ92/control-plane-kit",
                    "number": 9000,
                    "state": "open",
                    "evidence": "Issue #9000 owns the exact law and negative cases.",
                },
                "rationale": "The behavior remains desired but is not implemented.",
                "negative_case_disposition": "The issue names the failure cases.",
                "obsolete_assumption_disposition": "The old structure is not retained.",
            },
        ],
        "mutable_only_reviews": [],
    }
    inventory = {
        "schema": "cpk.semantic-test-migration-inventory",
        "reference": manifest["reference"],
        "parity_manifest_digest": _digest(manifest),
        "recorded_parity_counts": {
            "with_successors": 1,
            "with_supersession": 0,
            "without_completion_record": 1,
        },
        "counts": {
            "reference_laws": 2,
            "mutable_only_methods": 0,
            "current_methods": 1,
            "legacy_scripts": 1,
            "legacy_helpers": 0,
            "current_helpers": 0,
            "current_scripts": 1,
        },
        "sources": [
            {
                "distribution": "control-plane-kit-core",
                "repository": "OpenJ92/control-plane-kit",
                "commit": "core-commit",
                "gate": "./test.sh",
                "method_count": 1,
                "helper_count": 0,
                "script_count": 1,
            }
        ],
        "provisional_target_counts": [
            {
                "issue": 1320,
                "distribution": "control-plane-kit-core",
                "reference_laws": 2,
                "mutable_only_methods": 0,
            }
        ],
        "legacy_module_imports": [],
        "reference_assignments": [
            {
                "reference": "tests.current.Case.test_law",
                "law": "behavior.current-law",
                "source": {},
                "provisional_target": {
                    "issue": 1320,
                    "distribution": "control-plane-kit-core",
                },
                "current_successor_candidates": [],
            },
            {
                "reference": "tests.future.Case.test_law",
                "law": "behavior.future-law",
                "source": {},
                "provisional_target": {
                    "issue": 1320,
                    "distribution": "control-plane-kit-core",
                },
                "current_successor_candidates": [],
            },
        ],
        "mutable_only_methods": [],
        "legacy_helpers": [],
        "legacy_scripts": [{"path": "old.sh", "provisional_issue": 1318}],
        "current_helpers": [],
        "current_scripts": [
            {"distribution": "control-plane-kit-core", "path": "test.sh"}
        ],
        "current_tests": [
            {
                "id": "control-plane-kit-core:tests.test_current.Case.test_law",
                "module": "tests.test_current",
                "class": "Case",
                "method": "test_law",
                "path": "tests/test_current.py",
                "line": 10,
                "assertions": ["assertEqual"],
                "negative_case_hints": [],
                "subtest_dimensions": [],
            }
        ],
    }
    evidence = {
        "schema": "cpk.successor-evidence-index",
        "evidence": [
            {
                "id": "current-evidence",
                "status": "passing",
                "digest": "sha256:" + "a" * 64,
            }
        ],
    }
    aggregate = {
        "schema": "cpk.harden-tests-parity.cross-repository-aggregate",
        "issue": 1348,
        "package_gate_evidence": {
            "control-plane-kit-core": {
                "tests": 1,
                "commit": "core-commit",
            }
        },
    }
    closeout = {
        "schema": "cpk.semantic-migration-closeout",
        "issue": 1326,
        "reference": manifest["reference"],
        "input_digests": {
            "manifest": _digest(manifest),
            "reconciliation": _digest(reconciliation),
            "inventory": _digest(inventory),
            "evidence": _digest(evidence),
            "aggregate": _digest(aggregate),
        },
        "additional_current_tests": [],
        "future_issues": [
            {
                "repository": "OpenJ92/control-plane-kit",
                "number": 9000,
                "state": "open",
                "url": "https://github.com/OpenJ92/control-plane-kit/issues/9000",
                "title": "Future exact law owner",
                "content_digest": "sha256:" + "b" * 64,
                "law_references": ["tests.future.Case.test_law"],
            }
        ],
        "demo_reviews": [
            {
                "reference": "demo.superseded",
                "law": "demo.superseded",
                "reviewed_by_issue": 1326,
                "disposition": "reviewed-supersession",
                "current_evidence": [],
                "future_issue": None,
                "rationale": "The exact obsolete demo structure was reviewed.",
                "negative_case_disposition": "The current boundary rejects the old path.",
                "obsolete_assumption_disposition": "The old process is not retained.",
            }
        ],
        "current_live_laws": [
            {
                "distribution": "control-plane-kit-core",
                "path": "test.sh",
                "classification": "package-gate",
                "owner": "OpenJ92/control-plane-kit#1316",
                "law": "The package gate executes current core tests.",
                "evidence": "Current source inventory at an exact commit.",
            }
        ],
        "expected_counts": {
            "manifest_entries": 3,
            "test_reviews": 2,
            "demo_reviews": 1,
            "mutable_only_reviews": 0,
            "current_test_identities": 1,
            "additional_current_tests": 0,
            "current_live_laws": 1,
            "future_owned": 1,
            "required_future_owned": 1,
            "unowned": 0,
            "stale_successors": 0,
        },
    }
    return manifest, reconciliation, inventory, evidence, aggregate, closeout


class SemanticCompletionTests(unittest.TestCase):
    def test_exact_ownership_distinguishes_implemented_and_future_laws(self) -> None:
        report = validate_semantic_completion(*_documents())

        self.assertTrue(report["valid"])
        self.assertTrue(report["zero_unowned"])
        self.assertEqual(report["counts"]["future_owned"], 1)
        self.assertEqual(report["counts"]["implemented_or_superseded"], 2)
        self.assertEqual(report["counts"]["required_implemented_or_superseded"], 2)

    def test_missing_demo_or_current_identity_fails_closed(self) -> None:
        manifest, reconciliation, inventory, evidence, aggregate, closeout = _documents()
        closeout["demo_reviews"] = []
        closeout["input_digests"]["inventory"] = _digest(inventory)

        with self.assertRaisesRegex(CompletionError, "demo reviews differ"):
            validate_semantic_completion(
                manifest, reconciliation, inventory, evidence, aggregate, closeout
            )

        manifest, reconciliation, inventory, evidence, aggregate, closeout = _documents()
        inventory["current_tests"] = []
        inventory["counts"]["current_methods"] = 0
        closeout["input_digests"]["inventory"] = _digest(inventory)
        with self.assertRaisesRegex(CompletionError, "nonexistent current test"):
            validate_semantic_completion(
                manifest, reconciliation, inventory, evidence, aggregate, closeout
            )

    def test_closed_or_incomplete_future_owner_fails_closed(self) -> None:
        documents = list(_documents())
        closeout = documents[-1]
        closeout["future_issues"][0]["state"] = "closed"
        with self.assertRaisesRegex(CompletionError, "future issue must be open"):
            validate_semantic_completion(*documents)

        documents = list(_documents())
        closeout = documents[-1]
        closeout["future_issues"][0]["law_references"] = []
        with self.assertRaisesRegex(CompletionError, "future issue laws differ"):
            validate_semantic_completion(*documents)

    def test_count_only_claim_and_uninventoried_live_entry_fail_closed(self) -> None:
        documents = list(_documents())
        closeout = documents[-1]
        closeout["expected_counts"]["unowned"] = 99
        with self.assertRaisesRegex(CompletionError, "expected counts differ"):
            validate_semantic_completion(*documents)

        documents = list(_documents())
        closeout = documents[-1]
        closeout["current_live_laws"] = []
        with self.assertRaisesRegex(CompletionError, "current live laws differ"):
            validate_semantic_completion(*documents)

    def test_stale_package_coordinate_fails_closed(self) -> None:
        documents = list(_documents())
        inventory = documents[2]
        closeout = documents[-1]
        inventory["sources"][0]["commit"] = "stale-commit"
        closeout["input_digests"]["inventory"] = _digest(inventory)

        with self.assertRaisesRegex(CompletionError, "source commit differs"):
            validate_semantic_completion(*documents)

    def test_repository_artifact_proves_zero_unowned_laws(self) -> None:
        root = Path(__file__).resolve().parents[2]
        load = lambda name: json.loads(
            (root / "artifacts" / "extraction" / name).read_text(encoding="utf-8")
        )

        closeout = load("semantic-migration-closeout.json")
        report = validate_semantic_completion(
            load("parity-manifest.json"),
            load("semantic-test-reconciliation.json"),
            load("semantic-test-migration-inventory.json"),
            load("successor-evidence.json"),
            load("harden-tests-parity-1348-evidence.json"),
            closeout,
        )

        self.assertTrue(report["valid"])
        self.assertTrue(report["zero_unowned"])
        self.assertEqual(report["counts"]["manifest_entries"], 1107)
        self.assertEqual(report["counts"]["unowned"], 0)
        self.assertEqual(
            report,
            load("semantic-migration-completion-report.json"),
        )
        for test in closeout["additional_current_tests"]:
            source_digest = "sha256:" + hashlib.sha256(
                (root / test["path"]).read_bytes()
            ).hexdigest()
            self.assertEqual(source_digest, test["source_digest"])


if __name__ == "__main__":
    unittest.main()
