"""Protect the pure topology and planning package boundaries."""

from __future__ import annotations

import ast
import importlib.util
from pathlib import Path
import unittest

import control_plane_kit
from control_plane_kit.application import deploy as deployment
from control_plane_kit import effects, saga, scheduling
from control_plane_kit.core import (
    algebra,
    configuration,
    secrets,
    planning,
    topology,
    types,
    verification,
)
from control_plane_kit.operations import planning as operational_planning
from control_plane_kit.domains import discovery, idempotency, load_generation, webhook


class PackageStructureTests(unittest.TestCase):
    def test_deployment_program_has_one_intentional_package_entrance(self) -> None:
        public = set(deployment.__all__)

        self.assertTrue(
            {
                "Admit",
                "Advance",
                "Approve",
                "Claim",
                "Deploy",
                "DeploymentProgram",
                "DeploymentProgramServices",
                "Execute",
                "ExecuteApprovedDeployment",
                "Plan",
                "PrepareDeployment",
            }.issubset(public)
        )
        self.assertTrue(all(hasattr(deployment, name) for name in public))
        self.assertFalse(hasattr(control_plane_kit, "Deploy"))
        self.assertFalse(hasattr(control_plane_kit, "DeploymentProgram"))
        self.assertTrue({"program", "stages", "values"}.isdisjoint(public))

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
        self.assertFalse(hasattr(control_plane_kit, "RecoveryCandidate"))
        self.assertFalse(hasattr(planning, "RecoveryCandidate"))
        self.assertIs(control_plane_kit.SagaStep, saga.SagaStep)
        self.assertIs(control_plane_kit.ExecutionSchedule, scheduling.ExecutionSchedule)
        self.assertIs(control_plane_kit.EffectRequest, effects.EffectRequest)
        self.assertIs(control_plane_kit.EffectInterpreter, effects.EffectInterpreter)

    def test_canonical_types_report_their_new_module_homes(self) -> None:
        self.assertEqual(
            algebra.DeploymentRecipe.__module__,
            "control_plane_kit.core.algebra",
        )
        self.assertEqual(
            configuration.ConfigurationArtifact.__module__,
            "control_plane_kit.core.configuration",
        )
        self.assertEqual(
            secrets.SecretReference.__module__,
            "control_plane_kit.core.secrets",
        )
        self.assertEqual(types.Protocol.__module__, "control_plane_kit.core.types")
        self.assertEqual(
            verification.VerificationContract.__module__,
            "control_plane_kit.core.verification",
        )
        self.assertEqual(topology.DeploymentGraph.__module__, "control_plane_kit.core.topology.graph")
        self.assertEqual(topology.GraphDiff.__module__, "control_plane_kit.core.topology.changes")
        self.assertEqual(planning.ActivityPlan.__module__, "control_plane_kit.core.planning.activity_plan")
        self.assertEqual(
            operational_planning.RecoveryCandidate.__module__,
            "control_plane_kit.operations.planning.recovery",
        )
        self.assertEqual(saga.SagaStep.__module__, "control_plane_kit.saga.program")
        self.assertEqual(
            scheduling.ExecutionSchedule.__module__,
            "control_plane_kit.scheduling.schedule",
        )
        self.assertEqual(effects.EffectRequest.__module__, "control_plane_kit.effects.values")
        self.assertEqual(
            discovery.DiscoveryIdentity.__module__,
            "control_plane_kit.domains.discovery.language",
        )
        self.assertEqual(
            idempotency.IdempotencyIdentity.__module__,
            "control_plane_kit.domains.idempotency.language",
        )
        self.assertEqual(
            load_generation.LoadRunCommand.__module__,
            "control_plane_kit.domains.load_generation.language",
        )
        self.assertEqual(
            webhook.WebhookDeliveryIntent.__module__,
            "control_plane_kit.domains.webhook.language",
        )

    def test_retired_flat_modules_are_not_importable(self) -> None:
        retired_modules = (
            "algebra",
            "activity_plan",
            "activity_plan_codec",
            "activity_plan_compiler",
            "compiler",
            "capabilities",
            "configuration",
            "control_routes",
            "environment",
            "graph",
            "graph_changes",
            "graph_codec",
            "graph_diff",
            "recovery",
            "lifecycle",
            "secrets",
            "types",
            "validation",
            "verification",
            "topology.changes",
            "topology.codec",
            "topology.compiler",
            "topology.diff",
            "topology.graph",
            "topology.validation",
            "planning.activity_plan",
            "planning.codec",
            "planning.compiler",
            "planning.recovery",
            "discovery",
            "idempotency",
            "load_generation",
            "webhook.language",
        )

        for module in retired_modules:
            with self.subTest(module=module):
                self.assertIsNone(importlib.util.find_spec(f"control_plane_kit.{module}"))

    def test_pure_packages_do_not_import_workflow_or_store_layers(self) -> None:
        topology_forbidden = (
            "control_plane_kit.operations",
            "control_plane_kit.stores",
            "control_plane_kit.workflows",
        )
        planning_forbidden = (
            "control_plane_kit.operations",
            "control_plane_kit.policies",
            "control_plane_kit.stores",
            "control_plane_kit.workflows",
        )
        saga_forbidden = (
            "control_plane_kit.stores",
            "control_plane_kit.workflows",
            "control_plane_kit.docker_runtime",
            "control_plane_kit.servers",
        )
        scheduling_forbidden = (
            "control_plane_kit.stores",
            "control_plane_kit.workflows",
            "control_plane_kit.docker_runtime",
            "control_plane_kit.servers",
        )
        effects_forbidden = (
            "control_plane_kit.stores",
            "control_plane_kit.workflows",
            "control_plane_kit.docker_runtime",
            "control_plane_kit.servers",
            "httpx",
            "requests",
            "subprocess",
        )

        self._assert_package_avoids(topology.__file__, topology_forbidden)
        self._assert_package_avoids(planning.__file__, planning_forbidden)
        self._assert_package_avoids(saga.__file__, saga_forbidden)
        self._assert_package_avoids(scheduling.__file__, scheduling_forbidden)
        self._assert_package_avoids(effects.__file__, effects_forbidden)

    def _assert_package_avoids(
        self,
        package_file: str | None,
        forbidden_imports: tuple[str, ...],
    ) -> None:
        self.assertIsNotNone(package_file)
        package_dir = Path(package_file).parent

        for source_path in package_dir.glob("*.py"):
            tree = ast.parse(source_path.read_text(encoding="utf-8"))
            imported_modules = {
                module
                for node in ast.walk(tree)
                for module in self._imported_modules(node)
            }
            for forbidden_import in forbidden_imports:
                with self.subTest(source=source_path.name, forbidden=forbidden_import):
                    self.assertFalse(
                        any(
                            module == forbidden_import
                            or module.startswith(f"{forbidden_import}.")
                            for module in imported_modules
                        ),
                        f"{source_path.name} imports forbidden module {forbidden_import}",
                    )

    @staticmethod
    def _imported_modules(node: ast.AST) -> tuple[str, ...]:
        match node:
            case ast.Import(names=names):
                return tuple(alias.name for alias in names)
            case ast.ImportFrom(module=module) if module is not None:
                return (module,)
            case _:
                return ()


if __name__ == "__main__":
    unittest.main()
