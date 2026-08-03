from __future__ import annotations

import unittest

from control_plane_kit_core.operations import (
    ContractEnforcementOwner,
    EffectBoundaryKind,
    EffectMaterialPolicy,
    EffectResultKind,
    ExecutionCoordinatorCommandKind,
    ExecutionCoordinatorContractSet,
    ExternalEffectPolicy,
    InvalidExecutionCoordinatorContract,
    VerificationCommandKind,
    VerificationResultKind,
    canonical_execution_coordinator_contract_set,
)


class ExecutionCoordinatorContractTests(unittest.TestCase):
    def test_canonical_contract_names_closed_coordinator_and_verification_values(self) -> None:
        contract = canonical_execution_coordinator_contract_set()

        self.assertEqual(
            {command.kind for command in contract.coordinator_commands},
            set(ExecutionCoordinatorCommandKind),
        )
        self.assertEqual(
            {command.kind for command in contract.verification_commands},
            set(VerificationCommandKind),
        )
        self.assertEqual(
            set(contract.effect_result_kinds),
            set(EffectResultKind),
        )
        self.assertEqual(
            {
                result
                for command in contract.verification_commands
                for result in command.result_kinds
            },
            set(VerificationResultKind),
        )

    def test_descriptor_round_trips_strictly_and_rejects_open_values(self) -> None:
        contract = canonical_execution_coordinator_contract_set()
        descriptor = contract.descriptor()

        self.assertEqual(
            ExecutionCoordinatorContractSet.from_descriptor(descriptor),
            contract,
        )

        with self.assertRaises(InvalidExecutionCoordinatorContract):
            ExecutionCoordinatorContractSet.from_descriptor(
                {**descriptor, "callback": "execute"}
            )

        broken = {
            **descriptor,
            "effect_result_kinds": [
                *descriptor["effect_result_kinds"],
                "eventually-worked",
            ],
        }
        with self.assertRaises(InvalidExecutionCoordinatorContract):
            ExecutionCoordinatorContractSet.from_descriptor(broken)

    def test_external_effect_law_is_contract_data_not_core_execution(self) -> None:
        contract = canonical_execution_coordinator_contract_set()

        dispatch = next(
            boundary
            for boundary in contract.effect_boundaries
            if boundary.boundary is EffectBoundaryKind.DISPATCH
        )
        intent = next(
            boundary
            for boundary in contract.effect_boundaries
            if boundary.boundary is EffectBoundaryKind.INTENT
        )
        result = next(
            boundary
            for boundary in contract.effect_boundaries
            if boundary.boundary is EffectBoundaryKind.RESULT
        )

        self.assertIs(dispatch.external_effect_policy, ExternalEffectPolicy.AFTER_COMMIT)
        self.assertTrue(dispatch.durable_before_effect)
        self.assertTrue(dispatch.may_leave_uncertainty)
        self.assertTrue(intent.durable_before_effect)
        self.assertFalse(intent.durable_after_effect)
        self.assertTrue(result.durable_after_effect)
        self.assertTrue(result.may_leave_uncertainty)

        for boundary in contract.effect_boundaries:
            self.assertIs(
                boundary.enforcement_owner,
                ContractEnforcementOwner.OPERATIONS,
            )

    def test_coordinator_commands_preserve_pinned_material_and_uncertainty_laws(self) -> None:
        contract = canonical_execution_coordinator_contract_set()

        for command in contract.coordinator_commands:
            with self.subTest(command=command.operation_id):
                self.assertIs(
                    command.enforcement_owner,
                    ContractEnforcementOwner.OPERATIONS,
                )
                self.assertTrue(command.requires_worker)
                self.assertTrue(command.requires_pinned_plan)
                self.assertIs(
                    command.material_policy,
                    EffectMaterialPolicy.PINNED_APPROVED_PLAN,
                )
                self.assertEqual(command.uncertainty_policy.value, "never-blind-replay")
                self.assertIs(
                    command.external_effect_policy,
                    ExternalEffectPolicy.AFTER_COMMIT,
                )

        effect_commands = {
            ExecutionCoordinatorCommandKind.EXECUTE_READY_EFFECT,
            ExecutionCoordinatorCommandKind.EXECUTE_COMPENSATION_EFFECT,
            ExecutionCoordinatorCommandKind.RESUME_AFTER_RESTART,
        }
        for command in contract.coordinator_commands:
            if command.kind in effect_commands:
                self.assertTrue(command.records_intent_before_effect)

    def test_verification_commands_preserve_probe_and_projection_laws(self) -> None:
        contract = canonical_execution_coordinator_contract_set()

        for command in contract.verification_commands:
            with self.subTest(command=command.operation_id):
                self.assertIs(
                    command.enforcement_owner,
                    ContractEnforcementOwner.OPERATIONS,
                )
                self.assertIs(
                    command.material_policy,
                    EffectMaterialPolicy.CANONICAL_GRAPH_PROBE,
                )
                self.assertTrue(command.requires_graph_ownership)
                self.assertTrue(command.stale_on_graph_change)
                self.assertTrue(command.redacted_projection)
                self.assertTrue(command.unsupported_is_durable)

    def test_invalid_contracts_fail_before_open_execution_language_enters_core(self) -> None:
        descriptor = canonical_execution_coordinator_contract_set().descriptor()
        command = dict(descriptor["coordinator_commands"][0])
        command["external_effect_policy"] = ExternalEffectPolicy.INSIDE_TRANSACTION.value
        broken = {
            **descriptor,
            "coordinator_commands": [
                command,
                *descriptor["coordinator_commands"][1:],
            ],
        }

        with self.assertRaises(InvalidExecutionCoordinatorContract):
            ExecutionCoordinatorContractSet.from_descriptor(broken)

        verification = dict(descriptor["verification_commands"][0])
        verification["unsupported_is_durable"] = False
        broken_verification = {
            **descriptor,
            "verification_commands": [
                verification,
                *descriptor["verification_commands"][1:],
            ],
        }

        with self.assertRaises(InvalidExecutionCoordinatorContract):
            ExecutionCoordinatorContractSet.from_descriptor(broken_verification)


if __name__ == "__main__":
    unittest.main()
