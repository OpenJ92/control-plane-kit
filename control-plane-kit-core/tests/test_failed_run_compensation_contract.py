from __future__ import annotations

import importlib
import unittest

from control_plane_kit_core.operations import EffectAttemptIdentity, RunId
from control_plane_kit_core.planning import (
    CompensationMaterialSource,
    NodeTarget,
    RuntimeTarget,
    StopNode,
    StopRuntime,
)


TARGET_MODULE = "control_plane_kit_core.operations.compensation"


def target_module():
    try:
        return importlib.import_module(TARGET_MODULE)
    except ModuleNotFoundError as error:
        if error.name != TARGET_MODULE:
            raise
        return None


class FailedRunCompensationContractTests(unittest.TestCase):
    maxDiff = None

    def require_contract(self):
        module = target_module()
        self.assertIsNotNone(
            module,
            "failed-run compensation Core contract is missing",
        )
        return module

    def lineage(self, module):
        return module.FailedRunCompensationLineage(
            workspace_id="workspace-a",
            request_id="request-a",
            run_id=RunId("run-a"),
            plan_id="plan-a",
            current_graph_id="graph-current",
            desired_graph_id="graph-desired",
            desired_graph_revision=7,
            execution_intent_fingerprint="a" * 64,
        )

    def success(self, module, activity_id: str, ordinal: int):
        return module.SuccessfulEffectEvidence(
            attempt_identity=EffectAttemptIdentity(
                RunId("run-a"),
                activity_id,
                1,
            ),
            request_fingerprint="b" * 64,
            outcome_fingerprint=("c" if activity_id == "start-runtime" else "d")
            * 64,
            completion_event_id=f"{activity_id}-succeeded",
            completion_ordinal=ordinal,
        )

    def program(self, module):
        runtime = self.success(module, "start-runtime", 4)
        node = self.success(module, "start-node", 6)
        evidence = module.FailedRunCompensationEvidence(
            lineage=self.lineage(module),
            reason=module.FailedRunCompensationReason.POST_EFFECT_FAILURE,
            source_failure_fingerprint="e" * 64,
            successful_effects=(node, runtime),
        )
        return module.FailedRunCompensationProgram(
            program_id="compensation-a",
            evidence=evidence,
            steps=(
                module.FailedRunCompensationStep(
                    position=1,
                    source_effect=node,
                    operation=StopNode(NodeTarget("node-a")),
                    material_source=CompensationMaterialSource.DESIRED_GRAPH,
                ),
                module.FailedRunCompensationStep(
                    position=2,
                    source_effect=runtime,
                    operation=StopRuntime(RuntimeTarget("runtime-a")),
                    material_source=CompensationMaterialSource.DESIRED_GRAPH,
                ),
            ),
        )

    def test_closed_program_round_trip_and_fingerprint_are_exact(self) -> None:
        module = self.require_contract()
        program = self.program(module)

        self.assertEqual(
            program.descriptor(),
            {
                "schema": "cpk.failed-run-compensation-program",
                "version": 1,
                "program_id": "compensation-a",
                "evidence": {
                    "lineage": {
                        "workspace_id": "workspace-a",
                        "request_id": "request-a",
                        "run_id": "run-a",
                        "plan_id": "plan-a",
                        "current_graph_id": "graph-current",
                        "desired_graph_id": "graph-desired",
                        "desired_graph_revision": 7,
                        "execution_intent_fingerprint": "a" * 64,
                    },
                    "reason": "post-effect-failure",
                    "source_failure_fingerprint": "e" * 64,
                    "successful_effects": [
                        {
                            "attempt_identity": {
                                "run_id": "run-a",
                                "activity_id": "start-node",
                                "attempt": 1,
                            },
                            "request_fingerprint": "b" * 64,
                            "outcome_fingerprint": "d" * 64,
                            "completion_event_id": "start-node-succeeded",
                            "completion_ordinal": 6,
                        },
                        {
                            "attempt_identity": {
                                "run_id": "run-a",
                                "activity_id": "start-runtime",
                                "attempt": 1,
                            },
                            "request_fingerprint": "b" * 64,
                            "outcome_fingerprint": "c" * 64,
                            "completion_event_id": "start-runtime-succeeded",
                            "completion_ordinal": 4,
                        },
                    ],
                },
                "steps": [
                    {
                        "position": 1,
                        "source_effect": {
                            "attempt_identity": {
                                "run_id": "run-a",
                                "activity_id": "start-node",
                                "attempt": 1,
                            },
                            "request_fingerprint": "b" * 64,
                            "outcome_fingerprint": "d" * 64,
                            "completion_event_id": "start-node-succeeded",
                            "completion_ordinal": 6,
                        },
                        "operation": {
                            "kind": "stop-node",
                            "target": {"kind": "node", "node_id": "node-a"},
                        },
                        "material_source": "desired-graph",
                    },
                    {
                        "position": 2,
                        "source_effect": {
                            "attempt_identity": {
                                "run_id": "run-a",
                                "activity_id": "start-runtime",
                                "attempt": 1,
                            },
                            "request_fingerprint": "b" * 64,
                            "outcome_fingerprint": "c" * 64,
                            "completion_event_id": "start-runtime-succeeded",
                            "completion_ordinal": 4,
                        },
                        "operation": {
                            "kind": "stop-runtime",
                            "target": {
                                "kind": "runtime",
                                "runtime_id": "runtime-a",
                            },
                        },
                        "material_source": "desired-graph",
                    },
                ],
            },
        )
        self.assertEqual(
            module.FailedRunCompensationProgram.from_descriptor(
                program.descriptor()
            ),
            program,
        )
        self.assertRegex(program.fingerprint(), r"^[0-9a-f]{64}$")
        self.assertEqual(program.fingerprint(), program.fingerprint())

    def test_program_rejects_open_shapes_and_non_reverse_evidence(self) -> None:
        module = self.require_contract()
        program = self.program(module)
        Invalid = module.InvalidFailedRunCompensationContract

        invalid_factories = (
            lambda: module.FailedRunCompensationProgram(
                "compensation-a",
                program.evidence,
                tuple(reversed(program.steps)),
            ),
            lambda: module.FailedRunCompensationProgram(
                "compensation-a",
                program.evidence,
                (
                    program.steps[0],
                    module.FailedRunCompensationStep(
                        3,
                        program.steps[1].source_effect,
                        program.steps[1].operation,
                        program.steps[1].material_source,
                    ),
                ),
            ),
            lambda: module.FailedRunCompensationEvidence(
                program.evidence.lineage,
                program.evidence.reason,
                "not-a-fingerprint",
                program.evidence.successful_effects,
            ),
            lambda: module.FailedRunCompensationProgram.from_descriptor(
                {**program.descriptor(), "scenario_payload": {"open": True}}
            ),
            lambda: module.FailedRunCompensationProgram.from_descriptor(
                {
                    **program.descriptor(),
                    "steps": [
                        {
                            **program.descriptor()["steps"][0],
                            "provider_message": "registry-token-canary",
                        },
                        program.descriptor()["steps"][1],
                    ],
                }
            ),
        )
        for factory in invalid_factories:
            with self.subTest(factory=factory):
                with self.assertRaises(Invalid):
                    factory()

    def test_program_values_export_only_through_core_operations(self) -> None:
        module = self.require_contract()
        operations = importlib.import_module("control_plane_kit_core.operations")
        core = importlib.import_module("control_plane_kit_core")
        for name in (
            "FailedRunCompensationEvidence",
            "FailedRunCompensationLineage",
            "FailedRunCompensationProgram",
            "FailedRunCompensationReason",
            "FailedRunCompensationStep",
            "InvalidFailedRunCompensationContract",
            "SuccessfulEffectEvidence",
        ):
            with self.subTest(name=name):
                self.assertIs(getattr(operations, name), getattr(module, name))
                self.assertIs(getattr(core, name), getattr(module, name))


if __name__ == "__main__":
    unittest.main()
