from __future__ import annotations

import json
import unittest

from control_plane_kit import (
    ApplicationBlock,
    BlockSockets,
    BlockSpec,
    DEFAULT_GRAPH_CODEC,
    DeploymentGraph,
    DeploymentRecipe,
    DockerImageImplementation,
    DockerRuntime,
    EnvironmentMaterialSource,
    PinnedGraphSet,
    PublicStaticEnvironmentBinding,
    ReconcileNode,
    SecretEnvironmentDelivery,
    SecretFileDelivery,
    SecretFileMaterial,
    SecretFilePathBinding,
    SecretReference,
    SecretReferenceEnvironmentDelivery,
    SecretReferenceMaterialValue,
    StartNode,
    StructuralField,
    compile_activity_plan,
    compile_recipe,
    diff_graphs,
    effect_request_for_activity,
    materialize_effect_request,
    validate_graph,
)
from control_plane_kit.projections.operator_graph import project_operator_graph
from control_plane_kit.core.topology import FieldSubject, ModifiedChange


class SecretDeliveryTopologyTests(unittest.TestCase):
    def test_graph_codec_preserves_closed_deliveries_without_values(self) -> None:
        graph = compile_recipe(_recipe("database-a"))
        descriptor = graph.descriptor()

        reconstructed = DEFAULT_GRAPH_CODEC.decode(descriptor)

        self.assertEqual(
            reconstructed.node("service").secret_deliveries,
            graph.node("service").secret_deliveries,
        )
        self.assertIn(
            "environment-reference",
            {
                value["kind"]
                for value in descriptor["nodes"]["service"]["secret_deliveries"]
            },
        )
        rendered = json.dumps(descriptor, sort_keys=True)
        self.assertIn("secret://local/workspace-a/database-a", rendered)
        self.assertNotIn("resolved-password", rendered)
        operator_node = next(
            value
            for value in project_operator_graph(graph).descriptor()["nodes"]
            if value["node_id"] == "service"
        )
        reference_identity = next(
            value
            for value in operator_node["environment_bindings"]
            if value["kind"] == "secret-reference-identity"
        )
        self.assertEqual(reference_identity["reference"], "<redacted>")

    def test_delivery_change_is_explicit_diff_and_reconcile(self) -> None:
        current = validate_graph(compile_recipe(_recipe("database-a")))
        desired = validate_graph(compile_recipe(_recipe("database-b")))

        diff = diff_graphs(current, desired)
        change = next(
            value
            for value in diff.changes
            if isinstance(value, ModifiedChange)
            and isinstance(value.subject, FieldSubject)
            and value.subject.field is StructuralField.SECRET_DELIVERIES
        )
        plan = compile_activity_plan(diff)

        self.assertNotEqual(change.before.descriptor(), change.after.descriptor())
        self.assertEqual(len(plan.activities), 1)
        self.assertIsInstance(plan.activities[0].operation, ReconcileNode)

    def test_exact_deliveries_reach_pinned_start_material(self) -> None:
        current = DeploymentGraph("secret-delivery")
        desired = compile_recipe(_recipe("database-a"))
        plan = compile_activity_plan(
            diff_graphs(validate_graph(current), validate_graph(desired))
        )
        activity = next(
            value for value in plan.activities if isinstance(value.operation, StartNode)
        )
        request = effect_request_for_activity(
            activity,
            run_id="run",
            attempt=1,
            idempotency_key="run:start-service:1",
        )

        materialized = materialize_effect_request(
            request,
            activity,
            PinnedGraphSet("workspace", "plan", "current", "desired"),
            base_graph_id="current",
            base_graph=current,
            desired_graph_id="desired",
            desired_graph=desired,
        )

        implementation = materialized.material.implementation
        environment = next(
            value for value in implementation.environment if value.name == "DATABASE_URL"
        )
        self.assertIsInstance(environment.value, SecretReferenceMaterialValue)
        self.assertEqual(
            environment.value.reference_id,
            "secret://local/workspace-a/database-a",
        )
        reference_identity = next(
            value
            for value in implementation.environment
            if value.name == "DATABASE_REFERENCE"
        )
        self.assertEqual(
            reference_identity.value.value,
            "secret://local/workspace-a/database-a",
        )
        self.assertIs(
            reference_identity.source,
            EnvironmentMaterialSource.SECRET_REFERENCE_IDENTITY,
        )
        self.assertEqual(
            reference_identity.source_id,
            "secret://local/workspace-a/database-a",
        )
        self.assertEqual(
            implementation.secret_files,
            (
                SecretFileMaterial(
                    "secret://local/workspace-a/database-a",
                    "/run/secrets/database-password",
                    implementation.secret_files[0].file_mode,
                    SecretFilePathBinding("POSTGRES_PASSWORD_FILE"),
                ),
            ),
        )
        path_binding = next(
            value
            for value in implementation.environment
            if value.name == "POSTGRES_PASSWORD_FILE"
        )
        self.assertEqual(
            path_binding.value.value,
            "/run/secrets/database-password",
        )
        self.assertNotIn("resolved-password", materialized.canonical_json())

    def test_literal_and_secret_environment_names_cannot_overlap(self) -> None:
        with self.assertRaisesRegex(ValueError, "overlap"):
            DockerImageImplementation(
                "service:latest",
                environment=(
                    PublicStaticEnvironmentBinding("DATABASE_URL", "literal"),
                ),
                secret_deliveries=(
                    SecretEnvironmentDelivery(
                        "DATABASE_URL",
                        SecretReference("secret://local/workspace-a/database"),
                    ),
                ),
            ).materialize("service", BlockSockets(), DockerRuntime())

        with self.assertRaisesRegex(ValueError, "SecretEnvironmentDelivery"):
            DockerImageImplementation(
                "postgres:16-alpine",
                environment=(
                    PublicStaticEnvironmentBinding(
                        "POSTGRES_PASSWORD_FILE", "/tmp/untyped"
                    ),
                ),
                secret_deliveries=(
                    SecretFileDelivery(
                        "/run/secrets/database-password",
                        SecretReference("secret://local/workspace-a/database"),
                        path_binding=SecretFilePathBinding(
                            "POSTGRES_PASSWORD_FILE"
                        ),
                    ),
                ),
            ).materialize("service", BlockSockets(), DockerRuntime())


def _recipe(reference_name: str) -> DeploymentRecipe:
    reference = SecretReference(f"secret://local/workspace-a/{reference_name}")
    service = ApplicationBlock(
        BlockSpec("service"),
        DockerImageImplementation(
            "service:latest",
            secret_deliveries=(
                SecretEnvironmentDelivery("DATABASE_URL", reference),
                SecretReferenceEnvironmentDelivery(
                    "DATABASE_REFERENCE",
                    reference,
                ),
                SecretFileDelivery(
                    "/run/secrets/database-password",
                    reference,
                    path_binding=SecretFilePathBinding("POSTGRES_PASSWORD_FILE"),
                ),
            ),
        ),
        BlockSockets(),
    )
    return DeploymentRecipe(
        "secret-delivery",
        DockerRuntime(children=(service,)),
    )


if __name__ == "__main__":
    unittest.main()
