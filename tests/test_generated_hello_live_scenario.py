from __future__ import annotations

import unittest

from control_plane_kit.planning import (
    AddSocketConnection,
    RemoveSocketConnection,
    SwitchSocketConnection,
    compile_activity_plan,
)
from control_plane_kit.topology import DeploymentGraph, diff_graphs, validate_graph
from control_plane_kit.types import Protocol
from examples.generated_hello_graphs import HelloGraphShape, generated_hello_graph
from examples.generated_hello_live import _probe_address


class GeneratedHelloLiveScenarioTests(unittest.TestCase):
    def test_default_live_shape_is_a_valid_paired_dependency_topology(self) -> None:
        shape = HelloGraphShape(2, 1, root_host_port=18280)
        graph = validate_graph(generated_hello_graph(shape)).require_valid()

        self.assertEqual(shape.application_count, 3)
        self.assertEqual(shape.database_count, 2)
        self.assertEqual(len(graph.nodes), 5)
        self.assertEqual(len(graph.edges), 4)

    def test_generated_startup_wiring_never_becomes_control_route_effects(self) -> None:
        desired = validate_graph(
            generated_hello_graph(HelloGraphShape(2, 1, root_host_port=18280))
        )
        plan = compile_activity_plan(
            diff_graphs(validate_graph(DeploymentGraph("empty")), desired)
        )

        self.assertFalse(
            any(
                isinstance(
                    activity.operation,
                    (
                        AddSocketConnection,
                        SwitchSocketConnection,
                        RemoveSocketConnection,
                    ),
                )
                for activity in plan.activities
            )
        )

    def test_probe_authorities_drop_driver_and_database_path_information(self) -> None:
        self.assertEqual(
            _probe_address(
                "postgresql+psycopg://postgres@runtime-db:5432/hello",
                Protocol.POSTGRES,
            ),
            "postgresql://runtime-db:5432",
        )
        self.assertEqual(
            _probe_address("http://runtime-api:8000/path", Protocol.HTTP),
            "http://runtime-api:8000",
        )


if __name__ == "__main__":
    unittest.main()
