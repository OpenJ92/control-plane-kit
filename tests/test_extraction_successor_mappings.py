from __future__ import annotations

import unittest
from pathlib import Path

from extraction_parity.validation import (
    inventory_unmapped_required_core_families,
    read_bounded_json,
    validate_required_core_closeout,
)


ARTIFACT_ROOT = Path(__file__).parents[1] / "artifacts" / "extraction"


class ExtractionSuccessorMappingTests(unittest.TestCase):
    def closeout(self) -> dict[str, object]:
        return validate_required_core_closeout(
            read_bounded_json(ARTIFACT_ROOT / "parity-manifest.json"),
            read_bounded_json(ARTIFACT_ROOT / "reference-law-ownership.json"),
            read_bounded_json(ARTIFACT_ROOT / "reference-demos.json"),
            read_bounded_json(ARTIFACT_ROOT / "successor-evidence.json"),
        )

    def test_activity_plan_family_is_fully_mapped_to_passing_successor_evidence(self) -> None:
        inventory = inventory_unmapped_required_core_families(self.closeout())
        families = {
            family["family"]: family
            for family in inventory["families"]
        }

        remaining = families.get("test_activity_plan")
        self.assertIsNone(
            remaining,
            f"test_activity_plan still has {remaining['count']} unmapped laws"
            if remaining is not None
            else "",
        )

    def test_activity_plan_codec_and_compiler_families_are_fully_mapped(self) -> None:
        inventory = inventory_unmapped_required_core_families(self.closeout())
        families = {
            family["family"]: family
            for family in inventory["families"]
        }

        for family_name in ("test_activity_plan_codec", "test_activity_plan_compiler"):
            with self.subTest(family=family_name):
                remaining = families.get(family_name)
                self.assertIsNone(
                    remaining,
                    f"{family_name} still has {remaining['count']} unmapped laws"
                    if remaining is not None
                    else "",
                )

    def test_planning_scenario_families_are_fully_mapped_or_superseded(self) -> None:
        inventory = inventory_unmapped_required_core_families(self.closeout())
        families = {
            family["family"]: family
            for family in inventory["families"]
        }

        for family_name in (
            "test_planning_scenarios",
            "test_execution_scenario_expectations",
        ):
            with self.subTest(family=family_name):
                remaining = families.get(family_name)
                self.assertIsNone(
                    remaining,
                    f"{family_name} still has {remaining['count']} unmapped laws"
                    if remaining is not None
                    else "",
                )

    def test_saga_program_state_and_journal_families_are_fully_mapped(self) -> None:
        inventory = inventory_unmapped_required_core_families(self.closeout())
        families = {
            family["family"]: family
            for family in inventory["families"]
        }

        for family_name in (
            "test_saga_program",
            "test_saga_state",
            "test_saga_journal",
        ):
            with self.subTest(family=family_name):
                remaining = families.get(family_name)
                self.assertIsNone(
                    remaining,
                    f"{family_name} still has {remaining['count']} unmapped laws"
                    if remaining is not None
                    else "",
                )

    def test_scheduling_families_are_fully_mapped(self) -> None:
        inventory = inventory_unmapped_required_core_families(self.closeout())
        families = {
            family["family"]: family
            for family in inventory["families"]
        }

        for family_name in (
            "test_scheduling",
            "test_scheduling_scenarios",
        ):
            with self.subTest(family=family_name):
                remaining = families.get(family_name)
                self.assertIsNone(
                    remaining,
                    f"{family_name} still has {remaining['count']} unmapped laws"
                    if remaining is not None
                    else "",
                )

    def test_compensation_and_recovery_planning_families_are_fully_mapped(self) -> None:
        inventory = inventory_unmapped_required_core_families(self.closeout())
        families = {
            family["family"]: family
            for family in inventory["families"]
        }

        for family_name in (
            "test_compensation_planning",
            "test_recovery_planning",
        ):
            with self.subTest(family=family_name):
                remaining = families.get(family_name)
                self.assertIsNone(
                    remaining,
                    f"{family_name} still has {remaining['count']} unmapped laws"
                    if remaining is not None
                    else "",
                )

    def test_environment_secret_families_are_fully_mapped_to_passing_successor_evidence(self) -> None:
        inventory = inventory_unmapped_required_core_families(self.closeout())
        families = {
            family["family"]: family
            for family in inventory["families"]
        }

        for family_name in (
            "test_environment_bindings",
            "test_secret_delivery_topology",
            "test_secrets",
        ):
            with self.subTest(family=family_name):
                remaining = families.get(family_name)
                self.assertIsNone(
                    remaining,
                    f"{family_name} still has {remaining['count']} unmapped laws"
                    if remaining is not None
                    else "",
                )

    def test_verification_capability_families_are_fully_mapped_to_passing_successor_evidence(self) -> None:
        inventory = inventory_unmapped_required_core_families(self.closeout())
        families = {
            family["family"]: family
            for family in inventory["families"]
        }

        for family_name in (
            "test_capabilities",
            "test_capability_compile",
            "test_verification_contract",
            "test_verification_dispatch",
        ):
            with self.subTest(family=family_name):
                remaining = families.get(family_name)
                self.assertIsNone(
                    remaining,
                    f"{family_name} still has {remaining['count']} unmapped laws"
                    if remaining is not None
                    else "",
                )

    def test_pure_contract_laws_are_mapped_without_claiming_operations_laws(self) -> None:
        closeout = self.closeout()
        classification = read_bounded_json(
            ARTIFACT_ROOT / "contract-boundary-classification.json"
        )
        manifest = read_bounded_json(ARTIFACT_ROOT / "parity-manifest.json")

        incomplete = {
            entry["reference"]
            for entry in closeout["incomplete_required_core_entries"]
        }
        manifest_by_reference = {
            entry["reference"]: entry
            for entry in manifest["entries"]
        }
        pure_references = {
            decision["reference"]
            for decision in classification["decisions"]
            if decision["decision"] == "pure-successor"
        }
        operations_references = {
            decision["reference"]
            for decision in classification["decisions"]
            if decision["decision"] == "move-to-operations"
        }
        split_decisions = [
            decision
            for decision in classification["decisions"]
            if decision["decision"] == "split-boundary"
        ]
        split_references = {
            decision["reference"]
            for decision in split_decisions
        }

        self.assertEqual(len(pure_references), 18)
        self.assertEqual(len(split_references), 23)
        self.assertFalse(pure_references & incomplete)
        self.assertTrue(operations_references <= incomplete)
        self.assertFalse(split_references & incomplete)
        for decision in split_decisions:
            with self.subTest(reference=decision["reference"]):
                self.assertEqual(
                    decision["core_successor"],
                    "extract-e-791.persistence-boundary-contract.unittest",
                )
                self.assertEqual(decision["operations_target_issue"], "#792")
                self.assertIn("holder mutation", decision["operations_handoff"])
                successors = manifest_by_reference[decision["reference"]]["successors"]
                self.assertTrue(
                    any(
                        successor["evidence"] == decision["core_successor"]
                        and successor["status"] == "passing"
                        for successor in successors
                    )
                )

    def test_policy_family_is_fully_mapped_to_passing_successor_evidence(self) -> None:
        inventory = inventory_unmapped_required_core_families(self.closeout())
        families = {
            family["family"]: family
            for family in inventory["families"]
        }

        remaining = families.get("test_policies")
        self.assertIsNone(
            remaining,
            f"test_policies still has {remaining['count']} unmapped laws"
            if remaining is not None
            else "",
        )

    def test_probe_intent_family_is_fully_mapped_to_passing_successor_evidence(self) -> None:
        inventory = inventory_unmapped_required_core_families(self.closeout())
        families = {
            family["family"]: family
            for family in inventory["families"]
        }

        remaining = families.get("test_probe_intents")
        self.assertIsNone(
            remaining,
            f"test_probe_intents still has {remaining['count']} unmapped laws"
            if remaining is not None
            else "",
        )

    def test_control_route_family_is_fully_mapped_to_passing_successor_evidence(self) -> None:
        inventory = inventory_unmapped_required_core_families(self.closeout())
        families = {
            family["family"]: family
            for family in inventory["families"]
        }

        remaining = families.get("test_control_routes")
        self.assertIsNone(
            remaining,
            f"test_control_routes still has {remaining['count']} unmapped laws"
            if remaining is not None
            else "",
        )

    def test_deployment_program_boundary_families_are_fully_mapped(self) -> None:
        inventory = inventory_unmapped_required_core_families(self.closeout())
        families = {
            family["family"]: family
            for family in inventory["families"]
        }

        for family_name in (
            "test_backend_boundaries",
            "test_deployment_application_values",
            "test_deployment_plan_approve_stages",
            "test_deployment_admit_claim_stages",
        ):
            with self.subTest(family=family_name):
                remaining = families.get(family_name)
                self.assertIsNone(
                    remaining,
                    f"{family_name} still has {remaining['count']} unmapped laws"
                    if remaining is not None
                    else "",
                )

    def test_command_workflow_contract_families_are_fully_mapped(self) -> None:
        inventory = inventory_unmapped_required_core_families(self.closeout())
        families = {
            family["family"]: family
            for family in inventory["families"]
        }

        for family_name in (
            "test_activity_planning_command_service",
            "test_approval_command_service",
            "test_desired_graph_command_service",
            "test_desired_graph_commands",
            "test_operation_command_service",
            "test_operation_commands",
        ):
            with self.subTest(family=family_name):
                remaining = families.get(family_name)
                self.assertIsNone(
                    remaining,
                    f"{family_name} still has {remaining['count']} unmapped laws"
                    if remaining is not None
                    else "",
                )

    def test_read_projection_contract_families_are_fully_mapped(self) -> None:
        inventory = inventory_unmapped_required_core_families(self.closeout())
        families = {
            family["family"]: family
            for family in inventory["families"]
        }

        for family_name in (
            "test_instance_read_service",
            "test_instance_read_fastapi",
            "test_mcp_read",
            "test_focused_read_hardening",
            "test_focused_workflow_reads",
            "test_operator_graph_projection",
            "test_observation_projection",
            "test_operator_recovery_projection",
        ):
            with self.subTest(family=family_name):
                remaining = families.get(family_name)
                self.assertIsNone(
                    remaining,
                    f"{family_name} still has {remaining['count']} unmapped laws"
                    if remaining is not None
                    else "",
                )

    def test_admission_lifecycle_recovery_and_advancement_families_are_fully_mapped(self) -> None:
        inventory = inventory_unmapped_required_core_families(self.closeout())
        families = {
            family["family"]: family
            for family in inventory["families"]
        }

        for family_name in (
            "test_execution_values",
            "test_execution_admission",
            "test_run_lifecycle",
            "test_recovery_decisions",
            "test_execution_concurrency",
            "test_recovery_concurrency",
            "test_current_graph_advancement",
        ):
            with self.subTest(family=family_name):
                remaining = families.get(family_name)
                self.assertIsNone(
                    remaining,
                    f"{family_name} still has {remaining['count']} unmapped laws"
                    if remaining is not None
                    else "",
                )

    def test_execution_coordinator_and_verification_command_families_are_fully_mapped(self) -> None:
        inventory = inventory_unmapped_required_core_families(self.closeout())
        families = {
            family["family"]: family
            for family in inventory["families"]
        }

        for family_name in (
            "test_execution_coordinator",
            "test_verification_command_service",
        ):
            with self.subTest(family=family_name):
                remaining = families.get(family_name)
                self.assertIsNone(
                    remaining,
                    f"{family_name} still has {remaining['count']} unmapped laws"
                    if remaining is not None
                    else "",
                )

    def test_store_uow_postgres_and_mutation_holder_families_are_fully_mapped(self) -> None:
        inventory = inventory_unmapped_required_core_families(self.closeout())
        families = {
            family["family"]: family
            for family in inventory["families"]
        }

        for family_name in (
            "test_stores",
            "test_unit_of_work",
            "test_operation_postgres_primitives",
            "test_execution_schema_migration",
            "test_execution_store",
            "test_contracts",
        ):
            with self.subTest(family=family_name):
                remaining = families.get(family_name)
                self.assertIsNone(
                    remaining,
                    f"{family_name} still has {remaining['count']} unmapped laws"
                    if remaining is not None
                    else "",
                )


if __name__ == "__main__":
    unittest.main()
