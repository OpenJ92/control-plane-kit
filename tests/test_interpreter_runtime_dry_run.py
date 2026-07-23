from __future__ import annotations

from pathlib import Path
import unittest

from extraction_parity.validation import read_bounded_json


ARTIFACT_ROOT = Path("artifacts/extraction")


class InterpreterRuntimeDryRunTests(unittest.TestCase):
    def artifact(self) -> dict[str, object]:
        return read_bounded_json(
            ARTIFACT_ROOT / "interpreter-runtime-dry-run.json"
        )

    def test_topology_is_coherent_and_orders_dispatch_before_docker_sdk(self) -> None:
        artifact = self.artifact()

        self.assertEqual(artifact["schema"], "cpk.interpreter-runtime-dry-run")
        self.assertEqual(artifact["parent"], "#896")
        self.assertEqual(artifact["issues"], ["#897", "#898"])
        self.assertEqual(artifact["status"], "dry-run-complete")

        topology = artifact["topology_decision"]
        self.assertTrue(topology["coherent"])
        self.assertEqual(topology["adjustments_required_before_899"], [])

        order = topology["canonical_order"]
        self.assertLess(order.index("#897"), order.index("#898"))
        self.assertLess(order.index("#898"), order.index("#899"))
        self.assertLess(order.index("#900"), order.index("#902"))
        self.assertLess(order.index("#901"), order.index("#902"))
        self.assertLess(order.index("#907"), order.index("#908"))
        self.assertLess(order.index("#908"), order.index("#910"))

    def test_dispatcher_boundary_keeps_effects_out_of_core_and_cpk_server(self) -> None:
        artifact = self.artifact()

        boundary = artifact["dispatcher_boundary"]
        self.assertEqual(
            boundary["decision"],
            (
                "operations owns runtime dispatch; cpk-server receives a "
                "configured dispatcher; interpreters own concrete effect "
                "implementations"
            ),
        )
        self.assertEqual(
            boundary["shape"],
            [
                "cpk-server",
                "configured operations application",
                "ExecutionCoordinator",
                "RuntimeInterpreterDispatcher",
                "DockerRuntimeInterpreter",
                "Python Docker SDK",
            ],
        )
        self.assertIn(
            "cpk-server must not own Docker behavior",
            boundary["must_not_happen"],
        )
        self.assertIn(
            "core must not import Docker SDK or concrete runtime effect code",
            boundary["must_not_happen"],
        )

    def test_ownership_partition_preserves_operations_context_and_moves_sdk_clients(self) -> None:
        artifact = self.artifact()
        partition = artifact["ownership_partition"]

        self.assertIn(
            "ActivityRealizationContext",
            partition["must_stay_in_operations"],
        )
        self.assertIn(
            "RuntimeInterpreterDispatcher",
            partition["must_stay_in_operations"],
        )
        self.assertIn(
            "Docker SDK realization client",
            partition["can_move_to_interpreters"],
        )
        self.assertIn(
            "Docker health/probe execution clients",
            partition["can_move_to_interpreters"],
        )
        self.assertIn(
            "Construction or receipt of a configured RuntimeInterpreterDispatcher",
            partition["cpk_server_composition_only"],
        )
        self.assertIn(
            "DockerRuntimeInterpreter.up",
            partition["frozen_inspiration_only"],
        )

    def test_law_cards_cover_dispatch_cpk_server_authority_and_future_cloud(self) -> None:
        artifact = self.artifact()
        laws = {law["id"]: law for law in artifact["law_cards"]}

        self.assertEqual(
            laws["runtime.dispatch.from-pinned-context"]["classification"],
            "operations dispatcher law",
        )
        self.assertEqual(
            laws["runtime.dispatch.from-pinned-context"]["next_issue"],
            "#900",
        )
        self.assertEqual(
            laws["runtime.cpk-server.authority"]["classification"],
            "cpk-server composition law",
        )
        self.assertEqual(
            laws["runtime.cpk-server.authority"]["next_issue"],
            "#908",
        )
        self.assertEqual(
            laws["runtime.cloud.future"]["classification"],
            "future cloud runtime law",
        )
        self.assertEqual(laws["runtime.cloud.future"]["next_issue"], "#911")

        required = {
            "runtime.network.ensure-owned",
            "runtime.container.lifecycle",
            "runtime.configuration.artifact",
            "runtime.secret.environment",
            "runtime.health.http",
            "runtime.host-publication",
            "runtime.cleanup.residue",
        }
        self.assertLessEqual(required, laws.keys())

    def test_docker_sdk_coverage_records_unresolved_secret_and_config_materialization(self) -> None:
        artifact = self.artifact()
        coverage = artifact["docker_sdk_coverage"]

        self.assertIn("Docker SDK", coverage["network_lifecycle"])
        self.assertIn("Docker SDK", coverage["container_lifecycle"])
        self.assertIn("not directly solved", coverage["secret_stdin_materialization"])
        self.assertIn("not directly solved", coverage["configuration_materialization"])

        hazards = artifact["implementation_hazards"]
        self.assertIn(
            "Do not let cpk-server import Docker SDK directly as its execution model.",
            hazards,
        )
        self.assertIn(
            "Do not treat Docker socket mount as ordinary descriptor data.",
            hazards,
        )


if __name__ == "__main__":
    unittest.main()
