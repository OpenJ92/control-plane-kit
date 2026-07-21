from __future__ import annotations

import unittest
from pathlib import Path

from extraction_parity.validation import read_bounded_json


ARTIFACT_ROOT = Path(__file__).parents[1] / "artifacts" / "extraction"


class ExtractionContractBoundaryTests(unittest.TestCase):
    def classification(self) -> dict[str, object]:
        return read_bounded_json(ARTIFACT_ROOT / "contract-boundary-classification.json")

    def test_contract_classification_covers_classified_contract_laws_once(self) -> None:
        inventory = read_bounded_json(ARTIFACT_ROOT / "required-core-family-inventory.json")
        manifest = read_bounded_json(ARTIFACT_ROOT / "parity-manifest.json")
        classification = self.classification()

        remaining = next(
            family for family in inventory["families"]
            if family["family"] == "test_contracts"
        )
        remaining_references = {
            entry["reference"]
            for entry in remaining["entries"]
        }
        manifest_references = {
            entry["reference"]
            for entry in manifest["entries"]
            if entry["reference"].startswith("tests.test_contracts.")
        }
        classified_references = {
            decision["reference"]
            for decision in classification["decisions"]
        }
        operations_references = {
            decision["reference"]
            for decision in classification["decisions"]
            if decision["decision"] == "move-to-operations"
        }

        self.assertEqual(
            classification["schema"],
            "cpk.contract-boundary-classification",
        )
        self.assertEqual(classification["source_family"], "test_contracts")
        self.assertTrue(classified_references <= manifest_references)
        self.assertEqual(remaining_references, operations_references)
        self.assertEqual(
            classification["summary"]["entries"],
            len(classification["decisions"]),
        )

    def test_contract_classification_has_only_explicit_targets(self) -> None:
        classification = self.classification()

        target_by_decision = {
            "pure-successor": "#764",
            "move-to-operations": "#740",
        }
        counts = {
            "pure-successor": 0,
            "move-to-operations": 0,
        }

        for decision in classification["decisions"]:
            with self.subTest(reference=decision["reference"]):
                self.assertIn(decision["decision"], target_by_decision)
                self.assertEqual(
                    decision["target_issue"],
                    target_by_decision[decision["decision"]],
                )
                self.assertTrue(decision["rationale"])
                counts[decision["decision"]] += 1

        self.assertEqual(classification["summary"]["pure_successor"], counts["pure-successor"])
        self.assertEqual(
            classification["summary"]["moved_to_operations"],
            counts["move-to-operations"],
        )

    def test_derived_resource_and_publication_laws_move_to_operations(self) -> None:
        classification = self.classification()
        decisions = {
            decision["reference"]: decision
            for decision in classification["decisions"]
        }

        operation_markers = (
            "DerivedResourceTests",
            "apply_patch_updates_holder",
            "mutation_prepares",
            "mutation_rejects_stale",
            "preparation_validates",
            "prepared_mutation_identity",
            "published_mutation_identity",
            "mutation_and_candidate_descriptors",
            "runtime_contract_patch_updates_holder",
            "access_is_always_lookup",
        )

        for reference, decision in decisions.items():
            if any(marker in reference for marker in operation_markers):
                with self.subTest(reference=reference):
                    self.assertEqual(decision["decision"], "move-to-operations")
                    self.assertEqual(decision["target_issue"], "#740")

    def test_pure_value_laws_are_reserved_for_control_contract_mapping(self) -> None:
        classification = self.classification()
        decisions = {
            decision["reference"]: decision
            for decision in classification["decisions"]
        }

        pure_references = {
            "tests.test_contracts.ControlVariableProtocolTests.test_variable_descriptor_is_json_friendly",
            "tests.test_contracts.ControlVariableProtocolTests.test_required_value_validation_fails_structurally",
            "tests.test_contracts.ConcreteControlVariableTests.test_protocol_variables_validate_shape",
            "tests.test_contracts.ConcreteControlVariableTests.test_secret_descriptor_never_exposes_raw_value",
            "tests.test_contracts.ContractDescriptorRedactionTests.test_contract_descriptor_redacts_secret_and_non_secret_values",
            "tests.test_contracts.RuntimeContractTests.test_runtime_contract_does_not_read_process_environment",
        }

        for reference in pure_references:
            with self.subTest(reference=reference):
                self.assertEqual(decisions[reference]["decision"], "pure-successor")
                self.assertEqual(decisions[reference]["target_issue"], "#764")


if __name__ == "__main__":
    unittest.main()
