import ast
from pathlib import Path
import unittest

from control_plane_kit_core.operations import (
    ApplicationServiceBinding,
    ControlPlaneServiceRole,
    DeploymentProgramStage,
    DeploymentProgramBoundary,
    DeploymentStagePipeline,
    InvalidDeploymentProgramBoundary,
    canonical_deployment_stage_pipeline,
)


SRC_ROOT = Path(__file__).resolve().parents[1] / "src" / "control_plane_kit_core"


def _binding(role: ControlPlaneServiceRole) -> ApplicationServiceBinding:
    return ApplicationServiceBinding(
        role=role,
        service_name=f"{role.value}-service",
        parameters=("stores", "clock"),
    )


class DeploymentProgramBoundaryTests(unittest.TestCase):
    def test_boundary_requires_exactly_one_generic_service_per_role(self) -> None:
        boundary = DeploymentProgramBoundary(
            tuple(_binding(role) for role in ControlPlaneServiceRole)
        )

        self.assertEqual(
            tuple(binding.role for binding in boundary.services),
            tuple(ControlPlaneServiceRole),
        )
        self.assertEqual(
            boundary.service(ControlPlaneServiceRole.PLANNING).service_name,
            "planning-service",
        )

    def test_boundary_rejects_missing_or_duplicate_service_roles(self) -> None:
        missing = tuple(
            _binding(role)
            for role in ControlPlaneServiceRole
            if role is not ControlPlaneServiceRole.RECOVERY
        )
        with self.assertRaises(InvalidDeploymentProgramBoundary):
            DeploymentProgramBoundary(missing)

        duplicate = tuple(_binding(role) for role in ControlPlaneServiceRole) + (
            _binding(ControlPlaneServiceRole.PLANNING),
        )
        with self.assertRaises(InvalidDeploymentProgramBoundary):
            DeploymentProgramBoundary(duplicate)

    def test_boundary_descriptor_is_deterministic_and_generic(self) -> None:
        boundary = DeploymentProgramBoundary(
            tuple(reversed([_binding(role) for role in ControlPlaneServiceRole]))
        )

        descriptor = boundary.descriptor()

        self.assertEqual(
            [entry["role"] for entry in descriptor["services"]],
            [role.value for role in ControlPlaneServiceRole],
        )
        rendered = repr(descriptor).lower()
        self.assertNotIn("cpi", rendered)
        self.assertNotIn("hello", rendered)
        self.assertNotIn("fastapi", rendered)
        self.assertNotIn("dockerfile", rendered)

    def test_service_binding_rejects_process_packaging_terms(self) -> None:
        with self.assertRaises(InvalidDeploymentProgramBoundary):
            ApplicationServiceBinding(
                role=ControlPlaneServiceRole.EXECUTION,
                service_name="fastapi-process",
            )

        with self.assertRaises(InvalidDeploymentProgramBoundary):
            ApplicationServiceBinding(
                role=ControlPlaneServiceRole.EXECUTION,
                service_name="execution-service",
                parameters=("dockerfile",),
            )

    def test_stage_pipeline_is_closed_ordered_public_contract_data(self) -> None:
        pipeline = canonical_deployment_stage_pipeline()

        self.assertEqual(
            [stage.stage for stage in pipeline.stages],
            [
                DeploymentProgramStage.PLAN,
                DeploymentProgramStage.APPROVE,
                DeploymentProgramStage.ADMIT,
                DeploymentProgramStage.CLAIM,
                DeploymentProgramStage.EXECUTE,
                DeploymentProgramStage.ADVANCE,
            ],
        )
        self.assertEqual(
            [stage.service_role for stage in pipeline.stages],
            [
                ControlPlaneServiceRole.PLANNING,
                ControlPlaneServiceRole.APPROVAL,
                ControlPlaneServiceRole.ADMISSION,
                ControlPlaneServiceRole.LIFECYCLE,
                ControlPlaneServiceRole.EXECUTION,
                ControlPlaneServiceRole.LIFECYCLE,
            ],
        )
        self.assertTrue(
            all(stage.creates_durable_handoff for stage in pipeline.stages)
        )

        descriptor = pipeline.descriptor()
        self.assertEqual(DeploymentStagePipeline.from_descriptor(descriptor), pipeline)
        self.assertNotIn("fastapi", repr(descriptor).lower())
        self.assertNotIn("postgres", repr(descriptor).lower())
        self.assertNotIn("docker", repr(descriptor).lower())

    def test_stage_pipeline_rejects_reordered_or_role_mismatched_stages(self) -> None:
        pipeline = canonical_deployment_stage_pipeline()

        with self.assertRaises(InvalidDeploymentProgramBoundary):
            DeploymentStagePipeline(tuple(reversed(pipeline.stages)))

        execute = pipeline.stages[4]
        with self.assertRaises(InvalidDeploymentProgramBoundary):
            type(execute)(
                stage=DeploymentProgramStage.EXECUTE,
                service_role=ControlPlaneServiceRole.LIFECYCLE,
                requires_prior_stage=DeploymentProgramStage.CLAIM,
                creates_durable_handoff=True,
            )

    def test_operations_modules_do_not_import_process_or_product_packages(self) -> None:
        forbidden = {
            "control_plane_kit",
            "docker",
            "fastapi",
            "httpx",
            "mcp",
            "psycopg",
            "uvicorn",
        }
        findings: list[str] = []
        for path in sorted((SRC_ROOT / "operations").rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            imports: set[str] = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imports.update(alias.name.split(".", 1)[0] for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    imports.add(node.module.split(".", 1)[0])
            if imports & forbidden:
                findings.append(f"{path.name}: {sorted(imports & forbidden)}")

        self.assertEqual(findings, [])


if __name__ == "__main__":
    unittest.main()
