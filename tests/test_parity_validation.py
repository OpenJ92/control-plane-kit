from __future__ import annotations

import copy
import unittest

from extraction_parity.manifest import build_manifest
from extraction_parity.validation import (
    ValidationError,
    ValidationPolicy,
    decode_evidence_index,
    validate_parity,
)


def inventories() -> tuple[dict[str, object], dict[str, object]]:
    reference = {"tag": "tag", "commit": "commit"}
    ownership = {
        "schema": "cpk.reference-law-ownership",
        "reference": reference,
        "count": 2,
        "law_count": 2,
        "owner_counts": {"core": 1, "hello": 0, "deferred-product": 1, "system": 0},
        "laws": [
            {"reference": "tests.core", "law": "behavior.core", "collection_occurrences": 1, "owner_kind": "core", "owner": "control-plane-kit-core"},
            {"reference": "tests.product", "law": "behavior.product", "collection_occurrences": 1, "owner_kind": "deferred-product", "owner": "control-plane-kit-servers:x"},
        ],
    }
    demos = {
        "schema": "cpk.reference-demo-inventory",
        "reference": reference,
        "demos": [
            {"id": "demo.system", "scripts": ["x.sh"], "fixtures": [], "documentation": [], "kind": "live-demo", "owner_kind": "system", "owner": "control-plane-kit-test:cross-repository", "bootstrap_state": "required", "prerequisites": ["Docker"], "inputs": ["input"], "observables": ["result"], "normalization": [], "cleanup": ["clean"]},
        ],
    }
    return ownership, demos


def empty_evidence() -> dict[str, object]:
    return {"schema": "cpk.successor-evidence-index", "evidence": []}


class ParityValidationTests(unittest.TestCase):
    def test_foundation_accepts_exhaustive_mapping_without_claiming_migration(self) -> None:
        ownership, demos = inventories()
        report = validate_parity(
            build_manifest(ownership, demos), ownership, demos, empty_evidence(),
            policy=ValidationPolicy.FOUNDATION,
        )
        self.assertTrue(report["valid"])
        self.assertFalse(report["migration_complete"])
        self.assertEqual(report["counts"]["required"], 2)
        self.assertEqual(report["counts"]["deferred"], 1)
        self.assertEqual(report["counts"]["incomplete_required"], 2)

    def test_missing_and_stale_mappings_fail_closed(self) -> None:
        ownership, demos = inventories()
        manifest = build_manifest(ownership, demos)
        missing = copy.deepcopy(manifest)
        missing["entries"].pop()
        missing_report = validate_parity(missing, ownership, demos, empty_evidence(), policy=ValidationPolicy.FOUNDATION)
        self.assertFalse(missing_report["valid"])
        self.assertEqual({finding["code"] for finding in missing_report["findings"]}, {"missing_mapping"})

        stale = copy.deepcopy(manifest)
        stale["entries"].append({**stale["entries"][0], "reference": "tests.stale", "law": "behavior.stale"})
        stale_report = validate_parity(stale, ownership, demos, empty_evidence(), policy=ValidationPolicy.FOUNDATION)
        self.assertFalse(stale_report["valid"])
        self.assertIn("stale_mapping", {finding["code"] for finding in stale_report["findings"]})

        wrong_reference = copy.deepcopy(manifest)
        wrong_reference["reference"]["commit"] = "different"
        reference_report = validate_parity(
            wrong_reference,
            ownership,
            demos,
            empty_evidence(),
            policy=ValidationPolicy.FOUNDATION,
        )
        self.assertEqual(
            {finding["code"] for finding in reference_report["findings"]},
            {"reference_mismatch"},
        )

        wrong_owner = copy.deepcopy(manifest)
        wrong_owner["entries"][0]["owner"] = "control-plane-kit-core"
        owner_report = validate_parity(
            wrong_owner,
            ownership,
            demos,
            empty_evidence(),
            policy=ValidationPolicy.FOUNDATION,
        )
        self.assertEqual(
            {finding["code"] for finding in owner_report["findings"]},
            {"mapping_mismatch"},
        )

    def test_migration_policy_requires_passing_successor_evidence(self) -> None:
        ownership, demos = inventories()
        manifest = build_manifest(ownership, demos)
        report = validate_parity(manifest, ownership, demos, empty_evidence(), policy=ValidationPolicy.MIGRATION_COMPLETE)
        self.assertFalse(report["valid"])
        self.assertEqual(report["counts"]["incomplete_required"], 2)
        self.assertEqual({finding["code"] for finding in report["findings"]}, {"required_without_completion"})

    def test_successor_must_resolve_to_matching_passing_evidence(self) -> None:
        ownership, demos = inventories()
        manifest = build_manifest(ownership, demos)
        required = [entry for entry in manifest["entries"] if entry["migration_state"] == "required"]
        for index, entry in enumerate(required):
            entry["successors"] = [{"id": f"successor-{index}", "status": "passing", "evidence": f"proof-{index}"}]

        missing = validate_parity(manifest, ownership, demos, empty_evidence(), policy=ValidationPolicy.MIGRATION_COMPLETE)
        self.assertEqual(
            {finding["code"] for finding in missing["findings"]},
            {"missing_evidence", "required_without_completion"},
        )

        evidence = {
            "schema": "cpk.successor-evidence-index",
            "evidence": [
                {"id": "proof-0", "status": "passing", "digest": "sha256:" + "a" * 64},
                {"id": "proof-1", "status": "passing", "digest": "sha256:" + "b" * 64},
            ],
        }
        passing = validate_parity(manifest, ownership, demos, evidence, policy=ValidationPolicy.MIGRATION_COMPLETE)
        self.assertTrue(passing["valid"])
        self.assertTrue(passing["migration_complete"])

        evidence["evidence"][0]["status"] = "failed"
        mismatch = validate_parity(manifest, ownership, demos, evidence, policy=ValidationPolicy.MIGRATION_COMPLETE)
        self.assertIn("evidence_status_mismatch", {finding["code"] for finding in mismatch["findings"]})

        manifest["entries"][0]["successors"][0]["status"] = "failed"
        failed = validate_parity(manifest, ownership, demos, evidence, policy=ValidationPolicy.MIGRATION_COMPLETE)
        self.assertIn("failed_evidence", {finding["code"] for finding in failed["findings"]})

    def test_evidence_index_is_closed_unique_and_digest_bounded(self) -> None:
        valid = {
            "schema": "cpk.successor-evidence-index",
            "evidence": [
                {"id": "proof", "status": "passing", "digest": "sha256:" + "a" * 64}
            ],
        }
        self.assertEqual(decode_evidence_index(valid), valid)
        for invalid in (
            {**valid, "extra": True},
            {**valid, "evidence": valid["evidence"] * 2},
            {**valid, "evidence": [{**valid["evidence"][0], "status": "unknown"}]},
            {**valid, "evidence": [{**valid["evidence"][0], "digest": "sha256:short"}]},
        ):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValidationError):
                    decode_evidence_index(invalid)

    def test_reviewed_supersession_completes_required_entry_but_not_deferred_work(self) -> None:
        ownership, demos = inventories()
        manifest = build_manifest(ownership, demos)
        for entry in manifest["entries"]:
            if entry["migration_state"] == "required":
                entry["supersession"] = {"rationale": "law replaced by stronger invariant", "review": "issue-42"}
        report = validate_parity(manifest, ownership, demos, empty_evidence(), policy=ValidationPolicy.MIGRATION_COMPLETE)
        self.assertTrue(report["valid"])
        self.assertTrue(report["migration_complete"])
        self.assertEqual(report["counts"]["reviewed_supersessions"], 2)
        self.assertEqual(report["counts"]["deferred"], 1)


if __name__ == "__main__":
    unittest.main()
