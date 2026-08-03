from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from extraction_parity.migration_inventory import SourceLane, scan_test_root
from extraction_parity.reconciliation_builder import (
    apply_issue_slice,
    _current_test_index,
    _decode_decisions,
)
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
    def test_mutable_only_decisions_require_exact_current_or_archive_evidence(
        self,
    ) -> None:
        source_id = "legacy-mutable:tests.test_old.OldTests.test_law"
        inventory = {
            "schema": "cpk.semantic-test-migration-inventory",
            "reference_assignments": [],
            "mutable_only_methods": [
                {
                    "id": source_id,
                    "method": "test_law",
                    "provisional_target": {
                        "issue": 1345,
                        "distribution": "control-plane-kit-parity",
                    },
                }
            ],
        }
        manifest = {
            "schema": "cpk.parity-manifest",
            "reference": {"tag": "tag", "commit": "commit"},
            "entries": [],
        }
        reconciliation = {
            "schema": "cpk.semantic-test-reconciliation",
            "reviews": [],
            "mutable_only_reviews": [],
        }

        def decisions(mutable: dict[str, object]) -> dict[str, object]:
            return {
                "schema": "cpk.semantic-test-reconciliation-decisions",
                "slices": [
                    {
                        "issue": 1345,
                        "current_distributions": ["control-plane-kit-parity"],
                        "evidence_id": "parity-evidence",
                        "default_disposition": "current-isomorphic",
                        "strengthened_references": [],
                        "successor_overrides": {},
                        "future_issue_reviews": {},
                        "non_current_reviews": {},
                        "mutable_only_reviews": mutable,
                        "current_review_overrides": {},
                    }
                ],
            }

        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            tests = root / "extraction_parity" / "tests"
            tests.mkdir(parents=True)
            (tests / "test_current.py").write_text(
                "import unittest\n\n"
                "class CurrentTests(unittest.TestCase):\n"
                "    def test_law(self):\n"
                "        pass\n",
                encoding="utf-8",
            )
            current_id = (
                "control-plane-kit-parity:tests.test_current.CurrentTests.test_law"
            )

            with self.assertRaisesRegex(
                ReconciliationError,
                "decisions differ from assigned inputs",
            ):
                apply_issue_slice(
                    root=root,
                    issue=1345,
                    inventory=inventory,
                    manifest=manifest,
                    reconciliation=reconciliation,
                    decisions=decisions({}),
                )

            with self.assertRaisesRegex(
                ReconciliationError,
                "nonexistent current tests",
            ):
                apply_issue_slice(
                    root=root,
                    issue=1345,
                    inventory=inventory,
                    manifest=manifest,
                    reconciliation=reconciliation,
                    decisions=decisions(
                        {
                            source_id: {
                                "disposition": "current-strengthened",
                                "current_tests": [current_id + "-missing"],
                                "rationale": "Moved to the durable parity suite.",
                                "archive_artifact": None,
                            }
                        }
                    ),
                )

            with self.assertRaisesRegex(
                ReconciliationError,
                "archive artifact does not exist",
            ):
                apply_issue_slice(
                    root=root,
                    issue=1345,
                    inventory=inventory,
                    manifest=manifest,
                    reconciliation=reconciliation,
                    decisions=decisions(
                        {
                            source_id: {
                                "disposition": "reviewed-archived",
                                "current_tests": [],
                                "rationale": "Completed extraction checkpoint.",
                                "archive_artifact": "artifacts/missing.json",
                            }
                        }
                    ),
                )

            _, updated, _ = apply_issue_slice(
                root=root,
                issue=1345,
                inventory=inventory,
                manifest=manifest,
                reconciliation=reconciliation,
                decisions=decisions(
                    {
                        source_id: {
                            "disposition": "current-strengthened",
                            "current_tests": [current_id],
                            "rationale": "Moved to the durable parity suite.",
                            "archive_artifact": None,
                        }
                    }
                ),
            )

        self.assertEqual(
            updated["mutable_only_reviews"][0]["current_tests"],
            [current_id],
        )

        duplicate = {
            **updated,
            "mutable_only_reviews": [
                updated["mutable_only_reviews"][0],
                dict(updated["mutable_only_reviews"][0]),
            ],
        }
        with self.assertRaisesRegex(ReconciliationError, "duplicate mutable-only"):
            decode_reconciliation(duplicate)

    def test_archived_mutable_only_decision_requires_named_artifact(self) -> None:
        document = {
            "schema": "cpk.semantic-test-reconciliation-decisions",
            "slices": [
                {
                    "issue": 1345,
                    "current_distributions": ["control-plane-kit-parity"],
                    "evidence_id": "parity-evidence",
                    "default_disposition": "current-isomorphic",
                    "strengthened_references": [],
                    "successor_overrides": {},
                    "future_issue_reviews": {},
                    "non_current_reviews": {},
                    "mutable_only_reviews": {
                        "legacy-mutable:tests.test_old.Case.test_old": {
                            "disposition": "reviewed-archived",
                            "current_tests": [],
                            "rationale": "One-time extraction checkpoint.",
                            "archive_artifact": None,
                        }
                    },
                    "current_review_overrides": {},
                }
            ],
        }

        with self.assertRaisesRegex(ReconciliationError, "must name an artifact"):
            _decode_decisions(document)

    def test_current_test_index_requires_and_scans_explicit_external_root(self) -> None:
        with TemporaryDirectory() as temporary:
            external_root = Path(temporary)
            tests = external_root / "tests"
            tests.mkdir()
            (tests / "test_external.py").write_text(
                "import unittest\n\n"
                "class ExternalTests(unittest.TestCase):\n"
                "    def test_law(self):\n"
                "        pass\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(
                ReconciliationError,
                "explicit current root",
            ):
                _current_test_index(
                    external_root,
                    ("control-plane-kit-interpreters",),
                )

            identities, methods = _current_test_index(
                Path("/coordination-root-is-not-used"),
                ("control-plane-kit-interpreters",),
                current_roots={
                    "control-plane-kit-interpreters": external_root,
                },
            )

        identity = (
            "control-plane-kit-interpreters:"
            "tests.test_external.ExternalTests.test_law"
        )
        self.assertEqual(identities, {identity})
        self.assertEqual(methods, {"test_law": [identity]})

    def test_server_index_includes_repository_and_product_owned_tests(self) -> None:
        with TemporaryDirectory() as temporary:
            external_root = Path(temporary)
            repository_tests = external_root / "tests"
            product_tests = external_root / "products" / "hello" / "tests"
            repository_tests.mkdir()
            product_tests.mkdir(parents=True)
            (repository_tests / "test_catalogue.py").write_text(
                "import unittest\n\n"
                "class CatalogueTests(unittest.TestCase):\n"
                "    def test_catalogue(self):\n"
                "        pass\n",
                encoding="utf-8",
            )
            (product_tests / "test_product.py").write_text(
                "import unittest\n\n"
                "class ProductTests(unittest.TestCase):\n"
                "    def test_product(self):\n"
                "        pass\n",
                encoding="utf-8",
            )

            identities, methods = _current_test_index(
                Path("/coordination-root-is-not-used"),
                ("control-plane-kit-servers",),
                current_roots={
                    "control-plane-kit-servers": external_root,
                },
            )

        catalogue = (
            "control-plane-kit-servers:"
            "tests.test_catalogue.CatalogueTests.test_catalogue"
        )
        product = (
            "control-plane-kit-servers:products.hello.tests."
            "test_product.ProductTests.test_product"
        )
        self.assertEqual(identities, {catalogue, product})
        self.assertEqual(
            methods,
            {
                "test_catalogue": [catalogue],
                "test_product": [product],
            },
        )

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
                    "mutable_only_reviews": {},
                    "current_review_overrides": {},
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
                    "mutable_only_reviews": {},
                    "current_review_overrides": {},
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

    def test_issue_1322_artifact_records_external_interpreter_evidence(self) -> None:
        root = Path(__file__).resolve().parents[2]
        artifact_root = root / "artifacts/extraction"
        reconciliation = json.loads(
            (artifact_root / "semantic-test-reconciliation.json").read_text(
                encoding="utf-8"
            )
        )
        evidence = json.loads(
            (artifact_root / "harden-tests-parity-1322-evidence.json").read_text(
                encoding="utf-8"
            )
        )
        reviews = [
            value
            for value in reconciliation["reviews"]
            if value["reviewed_by_issue"] == 1322
        ]

        self.assertEqual(len(reviews), 117)
        self.assertEqual(
            {value["disposition"] for value in reviews},
            {
                "current-strengthened",
                "future-issue",
                "reviewed-supersession",
            },
        )
        self.assertEqual(
            sum(value["disposition"] == "current-strengthened" for value in reviews),
            86,
        )
        self.assertEqual(
            sum(value["disposition"] == "future-issue" for value in reviews),
            27,
        )
        self.assertEqual(
            sum(value["disposition"] == "reviewed-supersession" for value in reviews),
            4,
        )
        self.assertEqual(
            evidence["interpreter_commit"],
            "e9f19d558026d92af79df81fb9184d29da00110b",
        )
        self.assertEqual(evidence["interpreters"]["tests"], 147)
        self.assertEqual(evidence["core"]["tests"], 484)

    def test_issue_1323_artifact_records_server_product_evidence(self) -> None:
        root = Path(__file__).resolve().parents[2]
        artifact_root = root / "artifacts/extraction"
        reconciliation = json.loads(
            (artifact_root / "semantic-test-reconciliation.json").read_text(
                encoding="utf-8"
            )
        )
        evidence = json.loads(
            (artifact_root / "harden-tests-parity-1323-evidence.json").read_text(
                encoding="utf-8"
            )
        )
        reviews = [
            value
            for value in reconciliation["reviews"]
            if value["reviewed_by_issue"] == 1323
        ]

        self.assertEqual(len(reviews), 268)
        self.assertEqual(
            {value["disposition"] for value in reviews},
            {
                "current-strengthened",
                "future-issue",
                "reviewed-supersession",
            },
        )
        self.assertEqual(
            sum(value["disposition"] == "current-strengthened" for value in reviews),
            17,
        )
        self.assertEqual(
            sum(value["disposition"] == "future-issue" for value in reviews),
            247,
        )
        self.assertEqual(
            sum(value["disposition"] == "reviewed-supersession" for value in reviews),
            4,
        )
        self.assertEqual(
            evidence["server_products"]["repository_merge_commit"],
            "d2fe4cbde6766616981d67e87e3e81462a3f58e7",
        )
        self.assertEqual(evidence["server_products"]["full_tests"], 166)
        self.assertEqual(
            evidence["published_images"]["http-active-router"],
            "ghcr.io/openj92/control-plane-kit-servers/http-active-router@"
            "sha256:a58938fdc5c37bfda1b2b0dbd95fc0bf3ba7391f5ce3b8fdfb3956dccf0a01c8",
        )
        self.assertEqual(
            evidence["published_images"]["http-multiplexer"],
            "ghcr.io/openj92/control-plane-kit-servers/http-multiplexer@"
            "sha256:7fd15d9477db02c122e834d62074268a3b947b49b31fa3cad10d6a7737ca4fcb",
        )
        self.assertEqual(
            evidence["catalogue_checksum"],
            "8221cc76d3f5de19242aa59a86cd3b16e768f4c7fb4767d52ba0fb204a2118b9",
        )

    def test_issue_1324_artifact_records_durable_secret_evidence(self) -> None:
        root = Path(__file__).resolve().parents[2]
        artifact_root = root / "artifacts/extraction"
        reconciliation = json.loads(
            (artifact_root / "semantic-test-reconciliation.json").read_text(
                encoding="utf-8"
            )
        )
        evidence = json.loads(
            (artifact_root / "harden-tests-parity-1324-evidence.json").read_text(
                encoding="utf-8"
            )
        )
        reviews = [
            value
            for value in reconciliation["reviews"]
            if value["reviewed_by_issue"] == 1324
        ]

        self.assertEqual(len(reviews), 22)
        self.assertEqual(
            {value["disposition"] for value in reviews},
            {"current-strengthened", "future-issue"},
        )
        self.assertEqual(
            sum(value["disposition"] == "current-strengthened" for value in reviews),
            17,
        )
        self.assertEqual(
            sum(value["disposition"] == "future-issue" for value in reviews),
            5,
        )
        self.assertEqual(
            {
                value["future_issue"]["number"]
                for value in reviews
                if value["future_issue"] is not None
            },
            {1070},
        )
        self.assertEqual(
            evidence["secrets"]["commit"],
            "313fd26cb15a362ef5196547a3f6b27122877609",
        )
        self.assertEqual(evidence["secrets"]["tests"], 51)
        self.assertEqual(
            evidence["interpreter"]["commit"],
            "e9f19d558026d92af79df81fb9184d29da00110b",
        )
        self.assertEqual(evidence["interpreter"]["tests"], 147)

    def test_issue_1345_artifact_closes_every_mutable_only_law(self) -> None:
        root = Path(__file__).resolve().parents[2]
        artifact_root = root / "artifacts" / "extraction"
        reconciliation = json.loads(
            (artifact_root / "semantic-test-reconciliation.json").read_text(
                encoding="utf-8"
            )
        )
        evidence = json.loads(
            (artifact_root / "harden-tests-parity-1345-evidence.json").read_text(
                encoding="utf-8"
            )
        )
        reviews = [
            value
            for value in reconciliation["mutable_only_reviews"]
            if value["reviewed_by_issue"] == 1345
        ]

        self.assertEqual(len(reviews), 118)
        self.assertEqual(
            sum(value["disposition"] == "current-strengthened" for value in reviews),
            52,
        )
        self.assertEqual(
            sum(value["disposition"] == "reviewed-archived" for value in reviews),
            66,
        )
        self.assertTrue(
            all(
                value["archive_artifact"] is not None
                for value in reviews
                if value["disposition"] == "reviewed-archived"
            )
        )
        self.assertEqual(evidence["standalone_parity_tests"], 76)

    def test_issue_1346_artifact_closes_architecture_and_package_laws(self) -> None:
        root = Path(__file__).resolve().parents[2]
        artifact_root = root / "artifacts" / "extraction"
        reconciliation = json.loads(
            (artifact_root / "semantic-test-reconciliation.json").read_text(
                encoding="utf-8"
            )
        )
        evidence = json.loads(
            (artifact_root / "harden-tests-parity-1346-evidence.json").read_text(
                encoding="utf-8"
            )
        )
        reviews = [
            value
            for value in reconciliation["reviews"]
            if value["reviewed_by_issue"] == 1346
        ]

        self.assertEqual(len(reviews), 117)
        self.assertEqual(
            sum(value["disposition"] == "current-strengthened" for value in reviews),
            58,
        )
        self.assertEqual(
            sum(value["disposition"] == "future-issue" for value in reviews),
            29,
        )
        self.assertEqual(
            sum(value["disposition"] == "reviewed-supersession" for value in reviews),
            30,
        )
        self.assertEqual(
            {
                value["future_issue"]["number"]
                for value in reviews
                if value["future_issue"] is not None
            },
            {670, 1096, 1316, 1317},
        )
        self.assertEqual(
            evidence["future_owners"],
            {"670": 2, "1096": 3, "1316": 6, "1317": 18},
        )


if __name__ == "__main__":
    unittest.main()
