from __future__ import annotations

from dataclasses import dataclass
import unittest

from control_plane_kit_core.algebra import (
    ApplicationBlock,
    BlockSockets,
    BlockSpec,
    DeploymentTopology,
    DockerRuntime,
    ProviderSocket,
)
from control_plane_kit_core.lifecycle import OWNED_EPHEMERAL
from control_plane_kit_core.planning import StartNode, StartRuntime, StopNode, StopRuntime
from control_plane_kit_core.planning.recovery import (
    RECOVERY_CANDIDATE_SCHEMA,
    RECOVERY_CANDIDATE_VERSION,
    RecoveryDisposition,
    RecoveryLimitationCode,
    RecoveryMode,
    plan_reconstruction,
    plan_recovery_transition,
)
from control_plane_kit_core.topology import DeploymentGraph, compile_topology, validate_graph
from control_plane_kit_core.topology.graph import Endpoint, LiteralAddress
from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_core.types import Protocol


@dataclass(frozen=True)
class MaterializedBlock:
    kind: str
    endpoints: dict[str, Endpoint]
    metadata: dict[str, object]
    lifecycle: object = OWNED_EPHEMERAL
    public_environment: tuple[object, ...] = ()
    configuration_artifacts: tuple[object, ...] = ()
    secret_deliveries: tuple[object, ...] = ()


@dataclass(frozen=True)
class PureImplementation:
    kind: str
    endpoint: str

    def materialize(self, block_id: str, sockets: BlockSockets, runtime: object) -> MaterializedBlock:
        return MaterializedBlock(
            kind=self.kind,
            endpoints={
                "internal": Endpoint(
                    LiteralAddress(self.endpoint),
                    sockets.provider("internal").protocol,
                )
            },
            metadata={"image": f"example/{block_id}:latest"},
        )


def topology(active_name: str = "api-v1") -> DeploymentTopology:
    api = ApplicationBlock(
        BlockSpec(active_name),
        PureImplementation("application", f"http://{active_name}"),
        BlockSockets(providers=(ProviderSocket("internal", Protocol.HTTP),)),
    )
    return DeploymentTopology(
        "router-recovery",
        DockerRuntime(children=(api,)),
    )


def validated(active_name: str = "api-v1"):
    return validate_graph(compile_topology(topology(active_name)))


class RecoveryPlanningSuccessorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.populated = validated("api-v1")
        self.empty = validate_graph(DeploymentGraph(self.populated.graph.name))

    def test_reverse_transition_is_a_fresh_canonical_plan_with_limitations(self) -> None:
        candidate = plan_recovery_transition(self.populated, self.empty)

        self.assertEqual(candidate.mode, RecoveryMode.REVERSE_TRANSITION)
        self.assertTrue(
            any(
                isinstance(activity.operation, StopNode | StopRuntime)
                for activity in candidate.plan.activities
            )
        )
        self.assertTrue(candidate.approval.destructive)
        self.assertEqual(candidate.approval.required_scope, PolicyScope.PLAN_APPROVE_DESTRUCTIVE)
        self.assertIn(
            RecoveryLimitationCode.GRAPH_STATE_ONLY,
            {value.code for value in candidate.limitations},
        )
        self.assertIn(
            RecoveryLimitationCode.DESTRUCTIVE_ACTIVITY,
            {value.code for value in candidate.limitations},
        )

    def test_reconstruction_uses_empty_baseline_without_claiming_absence(self) -> None:
        candidate = plan_reconstruction(self.populated)

        self.assertEqual(candidate.mode, RecoveryMode.RECONSTRUCTION)
        self.assertIsNone(candidate.source_graph_name)
        self.assertTrue(
            any(
                isinstance(activity.operation, StartNode | StartRuntime)
                for activity in candidate.plan.activities
            )
        )
        self.assertIn(
            RecoveryLimitationCode.SOURCE_STATE_UNKNOWN,
            {value.code for value in candidate.limitations},
        )
        self.assertTrue(
            any(
                value.disposition is RecoveryDisposition.COMPENSATION_REQUIRED
                for value in candidate.assessments
            )
        )

    def test_router_recovery_uses_same_compiler_and_is_deterministic(self) -> None:
        version_one = validated("api-v1")
        version_two = validated("api-v2")

        first = plan_recovery_transition(version_two, version_one)
        second = plan_recovery_transition(version_two, version_one)

        self.assertEqual(first, second)
        self.assertEqual(first.descriptor(), second.descriptor())
        self.assertEqual(first.descriptor()["schema"], RECOVERY_CANDIDATE_SCHEMA)
        self.assertEqual(first.descriptor()["version"], RECOVERY_CANDIDATE_VERSION)
        self.assertEqual(
            first.plan.__class__.__module__,
            "control_plane_kit_core.planning.activity_plan",
        )
        self.assertNotIn("rollback", str(first.descriptor()).lower())

    def test_invalid_inputs_fail_before_planning(self) -> None:
        with self.assertRaises(TypeError):
            plan_recovery_transition(self.populated.graph, self.empty)
        with self.assertRaises(TypeError):
            plan_reconstruction(self.populated.graph)


if __name__ == "__main__":
    unittest.main()
