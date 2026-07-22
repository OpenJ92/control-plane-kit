from __future__ import annotations

import unittest
from pathlib import Path

from extraction_parity.validation import read_bounded_json


ARTIFACT_ROOT = Path(__file__).parents[1] / "artifacts" / "extraction"


class ExtractionBatchPlanTests(unittest.TestCase):
    def test_required_core_batch_plan_partitions_inventory_exactly_once(self) -> None:
        inventory = read_bounded_json(ARTIFACT_ROOT / "required-core-family-inventory.json")
        plan = read_bounded_json(ARTIFACT_ROOT / "required-core-batch-plan.json")

        self.assertEqual(plan["schema"], "cpk.required-core-batch-plan")
        planned_family_records = [
            family
            for batch in plan["batches"].values()
            for family in batch["families"]
        ]
        planned_families = [family["family"] for family in planned_family_records]
        planned_reference_count = sum(
            len(family["references"]) for family in planned_family_records
        )
        planned_family_counts = sum(family["count"] for family in planned_family_records)

        self.assertEqual(plan["source_counts"]["entries"], planned_reference_count)
        self.assertEqual(plan["source_counts"]["entries"], planned_family_counts)
        self.assertEqual(plan["source_counts"]["families"], len(planned_family_records))
        self.assertEqual(plan["totals"]["entries"], plan["source_counts"]["entries"])
        self.assertEqual(plan["totals"]["families"], plan["source_counts"]["families"])

        self.assertEqual(len(planned_families), len(set(planned_families)))
        self.assertEqual(
            sorted(plan["totals"]["issues"]),
            ["#738", "#739", "#740", "#741", "#742", "#743"],
        )

        inventory_families = {family["family"] for family in inventory["families"]}
        planned_family_set = set(planned_families)
        self.assertLessEqual(
            inventory["counts"]["entries"],
            plan["source_counts"]["entries"],
        )
        self.assertLessEqual(
            inventory["counts"]["families"],
            plan["source_counts"]["families"],
        )
        self.assertLessEqual(inventory_families, planned_family_set)

        planned_references = {
            reference
            for family in planned_family_records
            for reference in family["references"]
        }
        inventory_references = {
            entry["reference"]
            for family in inventory["families"]
            for entry in family["entries"]
        }
        self.assertLessEqual(inventory_references, planned_references)

    def test_pure_core_batch_closeout_maps_retained_families_and_keeps_moves_visible(
        self,
    ) -> None:
        closeout = read_bounded_json(ARTIFACT_ROOT / "pure-core-batch-closeout.json")

        self.assertEqual(closeout["schema"], "cpk.pure-core-batch-closeout")
        self.assertEqual(closeout["issue"], "#750")
        self.assertEqual(closeout["parent"], "#738")
        self.assertEqual(closeout["summary"]["unexpected_remaining_retained_families"], 0)
        self.assertEqual(closeout["summary"]["mapped_retained_families"], 17)
        self.assertEqual(closeout["summary"]["moved_families"], 2)
        self.assertEqual(closeout["summary"]["split_families"], 1)

        families = {
            family["family"]: family
            for family in closeout["families"]
        }
        self.assertEqual(families["test_postgres_scenario_runner"]["status"], "moved_to_active_issue")
        self.assertEqual(families["test_postgres_scenario_runner"]["target_issue"], "#740")
        self.assertEqual(families["test_block_control_fastapi"]["status"], "moved_to_active_issue")
        self.assertEqual(families["test_block_control_fastapi"]["target_issue"], "#743")
        self.assertEqual(families["test_contracts"]["status"], "split_mapped_and_moved")
        self.assertEqual(families["test_contracts"]["target_issue"], "#748,#740")

        retained = [
            family
            for family in families.values()
            if family["audit_decision"] == "retain"
        ]
        self.assertTrue(retained)
        self.assertTrue(
            all(family["remaining_live_inventory_count"] == 0 for family in retained)
        )

    def test_planning_saga_batch_audit_retains_only_pure_data_interpreters(
        self,
    ) -> None:
        audit = read_bounded_json(ARTIFACT_ROOT / "planning-saga-batch-audit.json")

        self.assertEqual(audit["schema"], "cpk.planning-saga-batch-audit")
        self.assertEqual(audit["issue"], "#771")
        self.assertEqual(audit["parent"], "#739")
        self.assertEqual(audit["source_batch"], "planning_saga")
        self.assertEqual(
            audit["summary"],
            {
                "families": 11,
                "entries": 74,
                "retained_families": 11,
                "moved_families": 0,
                "split_families": 0,
                "retained_entries": 74,
                "moved_entries": 0,
                "split_entries": 0,
            },
        )

        families = audit["families"]
        self.assertEqual(families["test_activity_plan_codec"]["target_issue"], "#772")
        self.assertEqual(families["test_activity_plan_compiler"]["target_issue"], "#772")
        self.assertEqual(families["test_planning_scenarios"]["target_issue"], "#773")
        self.assertEqual(
            families["test_execution_scenario_expectations"]["target_issue"],
            "#773",
        )
        self.assertEqual(families["test_saga_program"]["target_issue"], "#774")
        self.assertEqual(families["test_saga_state"]["target_issue"], "#774")
        self.assertEqual(families["test_saga_journal"]["target_issue"], "#774")
        self.assertEqual(families["test_scheduling"]["target_issue"], "#775")
        self.assertEqual(families["test_scheduling_scenarios"]["target_issue"], "#775")
        self.assertEqual(families["test_compensation_planning"]["target_issue"], "#776")
        self.assertEqual(families["test_recovery_planning"]["target_issue"], "#776")
        self.assertTrue(
            all(family["decision"] == "retain" for family in families.values())
        )
        self.assertIn(
            "closed catalogue/partial-order data",
            families["test_execution_scenario_expectations"]["rationale"],
        )

    def test_planning_saga_batch_closeout_maps_all_retained_families(
        self,
    ) -> None:
        closeout = read_bounded_json(
            ARTIFACT_ROOT / "planning-saga-batch-closeout.json"
        )

        self.assertEqual(closeout["schema"], "cpk.planning-saga-batch-closeout")
        self.assertEqual(closeout["issue"], "#777")
        self.assertEqual(closeout["parent"], "#739")
        self.assertEqual(closeout["summary"]["source_families"], 11)
        self.assertEqual(closeout["summary"]["source_entries"], 74)
        self.assertEqual(closeout["summary"]["mapped_retained_families"], 11)
        self.assertEqual(closeout["summary"]["retained_source_entries"], 74)
        self.assertEqual(closeout["summary"]["unexpected_remaining_retained_families"], 0)

        families = {
            family["family"]: family
            for family in closeout["families"]
        }
        expected = {
            "test_activity_plan_codec",
            "test_activity_plan_compiler",
            "test_planning_scenarios",
            "test_execution_scenario_expectations",
            "test_saga_program",
            "test_saga_state",
            "test_saga_journal",
            "test_scheduling",
            "test_scheduling_scenarios",
            "test_compensation_planning",
            "test_recovery_planning",
        }
        self.assertEqual(set(families), expected)
        self.assertTrue(
            all(family["status"] == "mapped" for family in families.values())
        )
        self.assertTrue(
            all(
                family["remaining_live_inventory_count"] == 0
                for family in families.values()
            )
        )

    def test_operations_contract_batch_audit_partitions_live_scope(
        self,
    ) -> None:
        audit = read_bounded_json(
            ARTIFACT_ROOT / "operations-contract-batch-audit.json"
        )

        self.assertEqual(audit["schema"], "cpk.operations-contract-batch-audit")
        self.assertEqual(audit["issue"], "#785")
        self.assertEqual(audit["parent"], "#740")
        self.assertEqual(audit["summary"]["families"], 34)
        self.assertEqual(audit["summary"]["entries"], 298)
        self.assertEqual(audit["summary"]["original_batch_families"], 33)
        self.assertEqual(audit["summary"]["split_contract_families"], 1)
        self.assertEqual(audit["summary"]["split_contract_entries"], 23)

        families = audit["families"]
        self.assertEqual(families["test_backend_boundaries"]["target_issue"], "#786")
        self.assertEqual(families["test_operation_commands"]["target_issue"], "#787")
        self.assertEqual(families["test_instance_read_service"]["target_issue"], "#788")
        self.assertEqual(families["test_run_lifecycle"]["target_issue"], "#789")
        self.assertEqual(families["test_execution_coordinator"]["target_issue"], "#790")
        self.assertEqual(families["test_contracts"]["target_issue"], "#791")
        self.assertEqual(families["test_workflows"]["target_issue"], "#792")
        self.assertEqual(families["test_contracts"]["count"], 23)
        self.assertEqual(
            families["test_contracts"]["source"],
            "artifacts/extraction/contract-boundary-classification.json",
        )

    def test_operations_contract_batch_closeout_has_no_unexpected_live_families(
        self,
    ) -> None:
        closeout = read_bounded_json(
            ARTIFACT_ROOT / "operations-contract-batch-closeout.json"
        )

        self.assertEqual(closeout["schema"], "cpk.operations-contract-batch-closeout")
        self.assertEqual(closeout["issue"], "#792")
        self.assertEqual(closeout["parent"], "#740")
        self.assertEqual(closeout["summary"]["source_families"], 34)
        self.assertEqual(closeout["summary"]["source_entries"], 298)
        self.assertEqual(closeout["summary"]["mapped_successor_families"], 32)
        self.assertEqual(closeout["summary"]["mapped_successor_entries"], 272)
        self.assertEqual(closeout["summary"]["split_boundary_families"], 1)
        self.assertEqual(closeout["summary"]["split_boundary_entries"], 23)
        self.assertEqual(
            closeout["summary"]["reviewed_operations_handoff_families"],
            1,
        )
        self.assertEqual(
            closeout["summary"]["reviewed_operations_handoff_entries"],
            3,
        )
        self.assertEqual(closeout["summary"]["unexpected_remaining_families"], 0)
        self.assertEqual(closeout["summary"]["unexpected_remaining_entries"], 0)

        families = {
            family["family"]: family
            for family in closeout["families"]
        }
        self.assertEqual(
            families["test_contracts"]["status"],
            "split_core_mapped_operations_handoff",
        )
        self.assertEqual(
            families["test_contracts"]["core_successor"],
            "extract-e-791.persistence-boundary-contract.unittest",
        )
        self.assertEqual(
            families["test_workflows"]["status"],
            "reviewed_operations_handoff",
        )
        self.assertEqual(
            families["test_workflows"]["operations_handoff_issue"],
            "#792",
        )

        self.assertTrue(
            all(
                family["remaining_live_inventory_count"] == 0
                for family in families.values()
            )
        )

    def test_architecture_test_harness_closeout_maps_all_families_without_supersession(
        self,
    ) -> None:
        closeout = read_bounded_json(
            ARTIFACT_ROOT / "architecture-test-harness-batch-closeout.json"
        )

        self.assertEqual(
            closeout["schema"],
            "cpk.architecture-test-harness-batch-closeout",
        )
        self.assertEqual(closeout["issue"], "#741")
        self.assertEqual(closeout["parent"], "#643")
        self.assertEqual(closeout["source_batch"], "architecture_test_harness")
        self.assertEqual(closeout["successor"], "extract-e-741.architecture-test-harness.unittest")
        self.assertEqual(closeout["summary"]["source_families"], 10)
        self.assertEqual(closeout["summary"]["source_entries"], 58)
        self.assertEqual(closeout["summary"]["mapped_successor_families"], 10)
        self.assertEqual(closeout["summary"]["mapped_successor_entries"], 58)
        self.assertEqual(closeout["summary"]["neutral_harness_families"], 8)
        self.assertEqual(closeout["summary"]["core_import_guard_families"], 2)
        self.assertEqual(closeout["summary"]["reviewed_supersession_families"], 0)
        self.assertEqual(closeout["summary"]["reviewed_supersession_entries"], 0)
        self.assertEqual(closeout["summary"]["unexpected_remaining_families"], 0)
        self.assertEqual(closeout["summary"]["unexpected_remaining_entries"], 0)

        families = {
            family["family"]: family
            for family in closeout["families"]
        }
        self.assertEqual(
            set(families),
            {
                "test_architecture_analysis",
                "test_architecture_dependencies",
                "test_architecture_ownership",
                "test_architecture_test_integrity",
                "test_root_api",
                "test_architecture_scenarios",
                "test_architecture_read_routes",
                "test_architecture_deploy",
                "test_architecture_protocol",
                "test_optional_dependencies",
            },
        )
        self.assertEqual(
            families["test_root_api"]["classification"],
            "extracted-core-public-import-guard",
        )
        self.assertEqual(
            families["test_optional_dependencies"]["classification"],
            "extracted-core-public-import-guard",
        )
        for family_name, family in families.items():
            with self.subTest(family=family_name):
                self.assertEqual(
                    family["status"],
                    "mapped_to_passing_successor_evidence",
                )
                self.assertEqual(
                    family["successor"],
                    "extract-e-741.architecture-test-harness.unittest",
                )
                self.assertEqual(family["remaining_live_inventory_count"], 0)
                self.assertIn(
                    "no package-name churn is treated as supersession",
                    family["rationale"],
                )

    def test_validation_packaging_demo_closeout_splits_core_contracts_from_process_handoffs(
        self,
    ) -> None:
        closeout = read_bounded_json(
            ARTIFACT_ROOT / "validation-packaging-demo-batch-closeout.json"
        )

        self.assertEqual(
            closeout["schema"],
            "cpk.validation-packaging-demo-batch-closeout",
        )
        self.assertEqual(closeout["issue"], "#742")
        self.assertEqual(closeout["parent"], "#643")
        self.assertEqual(closeout["source_batch"], "validation_packaging_demo")
        self.assertEqual(
            closeout["summary"],
            {
                "source_families": 4,
                "source_entries": 17,
                "mapped_successor_entries": 7,
                "reviewed_supersession_entries": 10,
                "split_families": 1,
                "reviewed_handoff_families": 2,
                "mapped_validation_families": 1,
                "unexpected_remaining_entries": 0,
                "unexpected_remaining_families": 0,
            },
        )

        families = {
            family["family"]: family
            for family in closeout["families"]
        }
        self.assertEqual(
            set(families),
            {"test_cli", "demo", "test_read_interface_demo_server", "validation"},
        )
        self.assertEqual(
            families["test_cli"]["status"],
            "reviewed_entrypoint_or_server_handoff",
        )
        self.assertEqual(
            families["test_read_interface_demo_server"]["status"],
            "reviewed_entrypoint_or_server_handoff",
        )
        self.assertEqual(
            families["validation"]["status"],
            "mapped_to_passing_successor_evidence",
        )
        self.assertEqual(
            families["demo"]["status"],
            "split_core_successor_and_interpreter_handoff",
        )
        self.assertEqual(
            families["demo"]["superseded_references"],
            ["demo.docker-publication"],
        )
        self.assertEqual(
            set(families["demo"]["successor_references"]),
            {
                "demo.configuration-artifact",
                "demo.read-interface",
                "demo.secret-delivery",
                "demo.transport",
                "demo.verification-observation",
            },
        )
        for family in families.values():
            with self.subTest(family=family["family"]):
                self.assertEqual(family["remaining_live_inventory_count"], 0)


if __name__ == "__main__":
    unittest.main()
