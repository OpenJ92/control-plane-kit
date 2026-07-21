from __future__ import annotations

import copy
import unittest

from extraction_parity.manifest import build_manifest
from extraction_parity.validation import (
    ValidationError,
    ValidationPolicy,
    decode_evidence_index,
    inventory_unmapped_required_core_families,
    validate_required_core_closeout,
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

    def test_required_core_closeout_fails_on_unmapped_core_only(self) -> None:
        ownership, demos = inventories()
        manifest = build_manifest(ownership, demos)

        report = validate_required_core_closeout(
            manifest,
            ownership,
            demos,
            empty_evidence(),
        )

        self.assertFalse(report["valid"])
        self.assertFalse(report["required_core_complete"])
        self.assertEqual(report["counts"]["required_core"], 1)
        self.assertEqual(report["counts"]["required_non_core"], 1)
        self.assertEqual(report["counts"]["deferred"], 1)
        self.assertEqual(report["counts"]["incomplete_required_core"], 1)
        self.assertEqual(
            {finding["code"] for finding in report["findings"]},
            {"required_core_without_completion"},
        )
        self.assertEqual(
            report["deferred_entries"],
            [
                {
                    "kind": "test",
                    "reference": "tests.product",
                    "law": "behavior.product",
                    "owner_kind": "deferred-product",
                    "owner": "control-plane-kit-servers:x",
                }
            ],
        )
        self.assertEqual(
            report["incomplete_required_core_entries"],
            [
                {
                    "kind": "test",
                    "reference": "tests.core",
                    "law": "behavior.core",
                    "owner_kind": "core",
                    "owner": "control-plane-kit-core",
                }
            ],
        )

    def test_required_core_closeout_accepts_explicit_successor_or_supersession(self) -> None:
        ownership, demos = inventories()
        manifest = build_manifest(ownership, demos)
        for entry in manifest["entries"]:
            if entry["owner_kind"] == "core":
                entry["successors"] = [
                    {
                        "id": "control-plane-kit-core.tests.test_core.CoreTests.test_core_law",
                        "status": "passing",
                        "evidence": "core-proof",
                    }
                ]
        evidence = {
            "schema": "cpk.successor-evidence-index",
            "evidence": [
                {"id": "core-proof", "status": "passing", "digest": "sha256:" + "a" * 64},
            ],
        }

        report = validate_required_core_closeout(
            manifest,
            ownership,
            demos,
            evidence,
        )

        self.assertTrue(report["valid"])
        self.assertTrue(report["required_core_complete"])
        self.assertEqual(report["counts"]["completed_required_core"], 1)
        self.assertEqual(report["counts"]["incomplete_required_core"], 0)

        superseded = build_manifest(ownership, demos)
        for entry in superseded["entries"]:
            if entry["owner_kind"] == "core":
                entry["supersession"] = {
                    "rationale": "obsolete structural assertion replaced by stronger core boundary",
                    "review": "issue-728",
                }
        supersession_report = validate_required_core_closeout(
            superseded,
            ownership,
            demos,
            empty_evidence(),
        )
        self.assertTrue(supersession_report["valid"])
        self.assertEqual(supersession_report["counts"]["reviewed_core_supersessions"], 1)

    def test_required_core_closeout_rejects_missing_or_failed_successor_evidence(self) -> None:
        ownership, demos = inventories()
        manifest = build_manifest(ownership, demos)
        for entry in manifest["entries"]:
            if entry["owner_kind"] == "core":
                entry["successors"] = [
                    {"id": "successor", "status": "passing", "evidence": "missing-proof"}
                ]

        missing = validate_required_core_closeout(
            manifest,
            ownership,
            demos,
            empty_evidence(),
        )
        self.assertEqual(
            {finding["code"] for finding in missing["findings"]},
            {"missing_evidence", "required_core_without_completion"},
        )

        failed_evidence = {
            "schema": "cpk.successor-evidence-index",
            "evidence": [
                {"id": "missing-proof", "status": "failed", "digest": "sha256:" + "b" * 64},
            ],
        }
        failed = validate_required_core_closeout(
            manifest,
            ownership,
            demos,
            failed_evidence,
        )
        self.assertIn(
            "evidence_status_mismatch",
            {finding["code"] for finding in failed["findings"]},
        )

    def test_required_core_family_inventory_groups_unmapped_laws_deterministically(self) -> None:
        ownership, demos = inventories()
        ownership["laws"].extend(
            [
                {
                    "reference": "tests.test_graph.GraphTests.test_adds_node",
                    "law": "graph.adds-node",
                    "collection_occurrences": 1,
                    "owner_kind": "core",
                    "owner": "control-plane-kit-core",
                },
                {
                    "reference": "tests.test_graph.GraphTests.test_rejects_edge",
                    "law": "graph.rejects-edge",
                    "collection_occurrences": 1,
                    "owner_kind": "core",
                    "owner": "control-plane-kit-core",
                },
                {
                    "reference": "tests.test_diff.DiffTests.test_detects_change",
                    "law": "diff.detects-change",
                    "collection_occurrences": 1,
                    "owner_kind": "core",
                    "owner": "control-plane-kit-core",
                },
            ]
        )
        ownership["count"] = len(ownership["laws"])
        ownership["law_count"] = len(ownership["laws"])
        closeout = validate_required_core_closeout(
            build_manifest(ownership, demos),
            ownership,
            demos,
            empty_evidence(),
        )

        inventory = inventory_unmapped_required_core_families(closeout)

        self.assertEqual(inventory["schema"], "cpk.required-core-family-inventory")
        self.assertEqual(inventory["counts"]["entries"], 4)
        self.assertEqual(inventory["counts"]["families"], 3)
        self.assertEqual(
            [
                (family["family"], family["count"])
                for family in inventory["families"]
            ],
            [
                ("test_graph", 2),
                ("core", 1),
                ("test_diff", 1),
            ],
        )
        laws = [
            entry["law"]
            for family in inventory["families"]
            for entry in family["entries"]
        ]
        self.assertEqual(
            laws,
            [
                "graph.adds-node",
                "graph.rejects-edge",
                "behavior.core",
                "diff.detects-change",
            ],
        )

    def test_required_core_family_inventory_fails_closed_on_invalid_report_shape(self) -> None:
        closeout = {
            "schema": "cpk.required-core-parity-closeout",
            "counts": {},
            "incomplete_required_core_entries": [
                {
                    "kind": "test",
                    "reference": "tests.core",
                    "owner_kind": "core",
                    "owner": "control-plane-kit-core",
                }
            ],
        }
        with self.assertRaises(ValidationError):
            inventory_unmapped_required_core_families(closeout)

        closeout["incomplete_required_core_entries"][0]["law"] = "duplicate"
        closeout["incomplete_required_core_entries"].append(
            dict(closeout["incomplete_required_core_entries"][0])
        )
        with self.assertRaises(ValidationError):
            inventory_unmapped_required_core_families(closeout)


if __name__ == "__main__":
    unittest.main()
