"""Protect the pure topology and planning package boundaries."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import unittest

import control_plane_kit
from control_plane_kit import planning, saga, topology


class PackageStructureTests(unittest.TestCase):
    def test_root_api_reexports_canonical_package_objects(self) -> None:
        self.assertIs(control_plane_kit.DeploymentGraph, topology.DeploymentGraph)
        self.assertIs(control_plane_kit.GraphDescriptorCodec, topology.GraphDescriptorCodec)
        self.assertIs(control_plane_kit.GraphDiff, topology.GraphDiff)
        self.assertIs(control_plane_kit.compile_recipe, topology.compile_recipe)
        self.assertIs(control_plane_kit.ActivityPlan, planning.ActivityPlan)
        self.assertIs(
            control_plane_kit.ActivityPlanDescriptorCodec,
            planning.ActivityPlanDescriptorCodec,
        )
        self.assertIs(control_plane_kit.compile_activity_plan, planning.compile_activity_plan)
        self.assertIs(control_plane_kit.RecoveryCandidate, planning.RecoveryCandidate)
        self.assertIs(control_plane_kit.SagaStep, saga.SagaStep)

    def test_canonical_types_report_their_new_module_homes(self) -> None:
        self.assertEqual(topology.DeploymentGraph.__module__, "control_plane_kit.topology.graph")
        self.assertEqual(topology.GraphDiff.__module__, "control_plane_kit.topology.changes")
        self.assertEqual(planning.ActivityPlan.__module__, "control_plane_kit.planning.activity_plan")
        self.assertEqual(planning.RecoveryCandidate.__module__, "control_plane_kit.planning.recovery")
        self.assertEqual(saga.SagaStep.__module__, "control_plane_kit.saga.program")

    def test_retired_flat_modules_are_not_importable(self) -> None:
        retired_modules = (
            "activity_plan",
            "activity_plan_codec",
            "activity_plan_compiler",
            "compiler",
            "graph",
            "graph_changes",
            "graph_codec",
            "graph_diff",
            "recovery",
            "validation",
        )

        for module in retired_modules:
            with self.subTest(module=module):
                self.assertIsNone(importlib.util.find_spec(f"control_plane_kit.{module}"))

    def test_pure_packages_do_not_import_workflow_or_store_layers(self) -> None:
        topology_forbidden = (
            "control_plane_kit.planning",
            "control_plane_kit.stores",
            "control_plane_kit.workflows",
        )
        planning_forbidden = (
            "control_plane_kit.stores",
            "control_plane_kit.workflows",
        )
        saga_forbidden = (
            "control_plane_kit.stores",
            "control_plane_kit.workflows",
            "control_plane_kit.docker_runtime",
            "control_plane_kit.servers",
        )

        self._assert_package_avoids(topology.__file__, topology_forbidden)
        self._assert_package_avoids(planning.__file__, planning_forbidden)
        self._assert_package_avoids(saga.__file__, saga_forbidden)

    def _assert_package_avoids(
        self,
        package_file: str | None,
        forbidden_imports: tuple[str, ...],
    ) -> None:
        self.assertIsNotNone(package_file)
        package_dir = Path(package_file).parent

        for source_path in package_dir.glob("*.py"):
            source = source_path.read_text(encoding="utf-8")
            for forbidden_import in forbidden_imports:
                with self.subTest(source=source_path.name, forbidden=forbidden_import):
                    self.assertNotIn(forbidden_import, source)


if __name__ == "__main__":
    unittest.main()
