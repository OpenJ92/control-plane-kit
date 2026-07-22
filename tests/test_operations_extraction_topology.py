from __future__ import annotations

from pathlib import Path
import unittest

from extraction_parity.validation import read_bounded_json


ARTIFACT_ROOT = Path(__file__).parents[1] / "artifacts" / "extraction"


class OperationsExtractionTopologyTests(unittest.TestCase):
    def test_operations_topology_places_registered_product_after_package_schema_and_uow(self) -> None:
        topology = read_bounded_json(ARTIFACT_ROOT / "operations-topology-refresh.json")

        self.assertEqual(topology["schema"], "cpk.operations-topology-refresh")
        self.assertEqual(topology["issue"], "#835")
        self.assertEqual(topology["parent"], "#821")
        self.assertEqual(topology["completed_prerequisites"], ["#831"])

        order = topology["canonical_order"]
        self.assertLess(order.index("#831"), order.index("#835"))
        self.assertLess(order.index("#835"), order.index("#836"))
        self.assertLess(order.index("#836"), order.index("#837"))
        self.assertLess(order.index("#837"), order.index("#838"))
        self.assertLess(order.index("#838"), order.index("#832"))
        self.assertLess(order.index("#832"), order.index("#839"))

        registered = topology["issues"]["#832"]
        self.assertEqual(registered["owner"], "control-plane-kit-operations")
        self.assertEqual(
            registered["requires"],
            ["#831", "#836", "#837", "#838"],
        )
        self.assertIn("RegisteredProductStore", registered["outputs"])
        self.assertNotIn("control-plane-kit-core", registered["owner"])

    def test_frozen_law_inventory_keeps_data_engineering_precedents_visible(self) -> None:
        topology = read_bounded_json(ARTIFACT_ROOT / "operations-topology-refresh.json")

        inventory = topology["frozen_law_inventory"]
        self.assertEqual(
            sorted(inventory),
            [
                "acceptance_and_runtime_boundary",
                "command_services",
                "postgres_schema_and_stores",
                "read_services_and_adapters",
                "unit_of_work",
            ],
        )
        self.assertIn(
            "tests/test_operation_postgres_primitives.py",
            inventory["postgres_schema_and_stores"]["references"],
        )
        self.assertIn(
            "control_plane_kit/stores/unit_of_work.py",
            inventory["unit_of_work"]["references"],
        )
        self.assertIn(
            "plan -> approve -> admit -> claim -> execute -> advance",
            topology["deployment_program_north_star"],
        )


if __name__ == "__main__":
    unittest.main()
