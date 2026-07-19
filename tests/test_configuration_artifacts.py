from __future__ import annotations

import unittest

from control_plane_kit import (
    ApplicationBlock,
    BlockSockets,
    BlockSpec,
    ConfigurationArtifact,
    ConfigurationArtifactError,
    ConfigurationFileMode,
    ConfigurationMediaType,
    DEFAULT_GRAPH_CODEC,
    DeploymentGraph,
    DeploymentRecipe,
    DockerImageImplementation,
    DockerRuntime,
    PinnedGraphSet,
    ReconcileNode,
    StartNode,
    StructuralField,
    compile_activity_plan,
    compile_recipe,
    diff_graphs,
    effect_request_for_activity,
    materialize_effect_request,
    validate_graph,
)
from control_plane_kit.topology import FieldSubject, ModifiedChange


class ConfigurationArtifactTests(unittest.TestCase):
    def test_artifact_is_bounded_deterministic_and_digest_verified(self) -> None:
        artifact = _artifact("workers = 2\n")
        descriptor = artifact.descriptor()

        self.assertEqual(
            ConfigurationArtifact.from_descriptor(descriptor),
            artifact,
        )
        self.assertEqual(descriptor["file_mode"], "0444")
        self.assertEqual(len(descriptor["content_digest"]), 64)

        tampered = {**descriptor, "content": "workers = 3\n"}
        with self.assertRaisesRegex(ConfigurationArtifactError, "digest"):
            ConfigurationArtifact.from_descriptor(tampered)

    def test_unsafe_paths_secrets_and_malformed_json_fail_closed(self) -> None:
        for path in (
            "relative/config.conf",
            "/etc/../config.conf",
            "/run/secrets/token",
            "/var/run/docker.sock",
        ):
            with self.subTest(path=path), self.assertRaises(ConfigurationArtifactError):
                ConfigurationArtifact(
                    "service-config",
                    path,
                    ConfigurationMediaType.TEXT,
                    "workers = 2\n",
                )

        for content in (
            "password = hunter2\n",
            "upstream = postgresql://user:password@database:5432/app\n",
            "-----BEGIN PRIVATE KEY-----\nnot-a-real-key\n",
        ):
            with self.subTest(content=content), self.assertRaisesRegex(
                ConfigurationArtifactError,
                "secret-shaped",
            ):
                _artifact(content)

        with self.assertRaisesRegex(ConfigurationArtifactError, "malformed"):
            ConfigurationArtifact(
                "service-config",
                "/etc/service/config.json",
                ConfigurationMediaType.JSON,
                "{not-json}",
            )

    def test_graph_codec_preserves_exact_artifact_and_rejects_tampering(self) -> None:
        graph = compile_recipe(_recipe(_artifact("workers = 2\n")))
        descriptor = graph.descriptor()

        reconstructed = DEFAULT_GRAPH_CODEC.decode(descriptor)

        self.assertEqual(
            reconstructed.node("service").configuration_artifacts,
            graph.node("service").configuration_artifacts,
        )
        descriptor["nodes"]["service"]["configuration_artifacts"][0][
            "content_digest"
        ] = "0" * 64
        with self.assertRaisesRegex(ValueError, "digest"):
            DEFAULT_GRAPH_CODEC.decode(descriptor)

    def test_artifact_change_is_explicit_diff_and_reconcile_work(self) -> None:
        current = validate_graph(
            compile_recipe(_recipe(_artifact("workers = 2\n")))
        )
        desired = validate_graph(
            compile_recipe(_recipe(_artifact("workers = 3\n")))
        )

        diff = diff_graphs(current, desired)
        change = next(
            value
            for value in diff.changes
            if isinstance(value, ModifiedChange)
            and isinstance(value.subject, FieldSubject)
            and value.subject.field is StructuralField.CONFIGURATION_ARTIFACTS
        )
        plan = compile_activity_plan(diff)

        self.assertNotEqual(
            change.before.descriptor()[0]["content_digest"],
            change.after.descriptor()[0]["content_digest"],
        )
        self.assertEqual(len(plan.activities), 1)
        self.assertIsInstance(plan.activities[0].operation, ReconcileNode)

    def test_exact_artifact_reaches_pinned_start_material(self) -> None:
        artifact = _artifact("workers = 2\n")
        current = DeploymentGraph("configuration-artifact")
        desired = compile_recipe(_recipe(artifact))
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
            PinnedGraphSet("workspace", "plan", "base", "desired"),
            base_graph_id="base",
            base_graph=current,
            desired_graph_id="desired",
            desired_graph=desired,
        )

        self.assertEqual(
            materialized.material.implementation.configuration_artifacts,
            (artifact,),
        )

    def test_duplicate_artifact_identity_or_target_fails_at_authoring(self) -> None:
        first = _artifact("workers = 2\n")
        duplicate_target = ConfigurationArtifact(
            "other-config",
            first.target_path,
            ConfigurationMediaType.TEXT,
            "workers = 3\n",
            ConfigurationFileMode.READ_ONLY,
        )
        with self.assertRaisesRegex(ValueError, "target paths"):
            compile_recipe(_recipe(first, duplicate_target))


def _artifact(content: str) -> ConfigurationArtifact:
    return ConfigurationArtifact(
        "service-config",
        "/etc/service/config.conf",
        ConfigurationMediaType.TEXT,
        content,
        ConfigurationFileMode.READ_ONLY,
    )


def _recipe(*artifacts: ConfigurationArtifact) -> DeploymentRecipe:
    service = ApplicationBlock(
        BlockSpec("service"),
        DockerImageImplementation(
            "service:latest",
            configuration_artifacts=artifacts,
        ),
        BlockSockets(),
    )
    return DeploymentRecipe(
        "configuration-artifact",
        DockerRuntime(children=(service,)),
    )


if __name__ == "__main__":
    unittest.main()
