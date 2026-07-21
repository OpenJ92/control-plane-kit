from __future__ import annotations

from dataclasses import dataclass, replace
import unittest

from control_plane_kit_core.algebra import (
    ApplicationBlock,
    BlockSockets,
    BlockSpec,
    DeploymentTopology,
    DockerRuntime,
)
from control_plane_kit_core.configuration import (
    ConfigurationArtifact,
    ConfigurationArtifactError,
    ConfigurationFileMode,
    ConfigurationMediaType,
)
from control_plane_kit_core.configuration_rendering import (
    ConfigurationRenderingError,
    ConfigurationTemplate,
    ConfigurationTemplateRenderError,
    ConfigurationTemplateSyntaxError,
)
from control_plane_kit_core.topology import (
    FieldSubject,
    GraphDescriptorCodec,
    ModifiedChange,
    StructuralField,
    compile_topology,
    diff_graphs,
    validate_graph,
)

from tests.test_graph_codec import PureImplementation


@dataclass(frozen=True)
class ProxyConfiguration:
    workers: int
    upstreams: tuple[str, ...] = ()

    def configuration_values(self):
        return {
            "upstreams": self.upstreams,
            "workers": self.workers,
        }


class ConfigurationArtifactTests(unittest.TestCase):
    def test_artifact_is_bounded_deterministic_and_digest_verified(self) -> None:
        artifact = text_artifact("workers = 2\n")
        descriptor = artifact.descriptor()

        restored = ConfigurationArtifact.from_descriptor(descriptor)

        self.assertEqual(restored, artifact)
        self.assertEqual(descriptor["file_mode"], "0444")
        self.assertEqual(len(descriptor["content_digest"]), 64)
        self.assertEqual(descriptor["source_digest"], descriptor["content_digest"])

        with self.assertRaisesRegex(ConfigurationArtifactError, "digest"):
            ConfigurationArtifact.from_descriptor({**descriptor, "content": "workers = 3\n"})
        with self.assertRaisesRegex(ConfigurationArtifactError, "malformed"):
            ConfigurationArtifact.from_descriptor(
                {**descriptor, "source_digest": "not-a-digest"}
            )

    def test_paths_parser_shapes_and_secret_shaped_content_fail_closed(self) -> None:
        unsafe_paths = (
            "relative/config.conf",
            "/etc/../config.conf",
            "/etc/service/config.json,readonly",
            "/etc/service/config file.json",
            "/run/secrets/token",
            "/var/run/docker.sock",
        )
        for path in unsafe_paths:
            with self.subTest(path=path), self.assertRaises(ConfigurationArtifactError):
                ConfigurationArtifact(
                    "service-config",
                    path,
                    ConfigurationMediaType.TEXT,
                    "workers = 2\n",
                )

        secret_shaped_content = (
            "password = hunter2\n",
            "upstream = postgresql://user:password@database:5432/app\n",
            "-----BEGIN PRIVATE KEY-----\nnot-a-real-key\n",
            '{"api_token":"reference-only"}',
        )
        for content in secret_shaped_content:
            with self.subTest(content=content), self.assertRaisesRegex(
                ConfigurationArtifactError,
                "secret-shaped",
            ):
                ConfigurationArtifact(
                    "service-config",
                    "/etc/service/config.json",
                    ConfigurationMediaType.JSON if content.startswith("{") else ConfigurationMediaType.TEXT,
                    content,
                )

        malformed = (
            (ConfigurationMediaType.JSON, "{not-json}"),
            (ConfigurationMediaType.TOML, "workers = ["),
            (ConfigurationMediaType.YAML, "workers: ["),
        )
        for media_type, content in malformed:
            with self.subTest(media_type=media_type), self.assertRaisesRegex(
                ConfigurationArtifactError,
                "malformed",
            ):
                ConfigurationArtifact(
                    "service-config",
                    "/etc/service/config.conf",
                    media_type,
                    content,
                )

    def test_graph_codec_preserves_exact_artifact_and_rejects_tampering(self) -> None:
        graph = graph_with_artifacts(text_artifact("workers = 2\n"))
        descriptor = GraphDescriptorCodec().encode(graph)

        restored = GraphDescriptorCodec().decode(descriptor)

        self.assertEqual(
            restored.node("service").configuration_artifacts,
            graph.node("service").configuration_artifacts,
        )
        descriptor["nodes"]["service"]["configuration_artifacts"][0][
            "content_digest"
        ] = "0" * 64
        with self.assertRaisesRegex(ValueError, "digest"):
            GraphDescriptorCodec().decode(descriptor)

    def test_duplicate_artifact_identity_or_target_fails_at_authoring(self) -> None:
        first = text_artifact("workers = 2\n")
        duplicate_target = ConfigurationArtifact(
            "other-config",
            first.target_path,
            ConfigurationMediaType.TEXT,
            "workers = 3\n",
        )
        duplicate_identity = ConfigurationArtifact(
            first.artifact_id,
            "/etc/service/other.conf",
            ConfigurationMediaType.TEXT,
            "workers = 4\n",
        )

        for duplicate in (duplicate_target, duplicate_identity):
            with self.subTest(duplicate=duplicate.target_path), self.assertRaisesRegex(
                ValueError,
                "configuration artifact",
            ):
                graph_with_artifacts(first, duplicate)

    def test_artifact_and_template_revision_changes_are_explicit_diff_fields(self) -> None:
        current = validate_graph(graph_with_artifacts(text_artifact("workers = 2\n")))
        desired = validate_graph(graph_with_artifacts(text_artifact("workers = 3\n")))

        fields = _modified_fields(diff_graphs(current, desired))

        self.assertIn(StructuralField.CONFIGURATION_ARTIFACTS, fields)

        parameters = ProxyConfiguration(workers=2)
        rendered_a = template("revision-a").render(parameters)
        rendered_b = template("revision-b").render(parameters)
        source_diff = diff_graphs(
            validate_graph(graph_with_artifacts(rendered_a)),
            validate_graph(graph_with_artifacts(rendered_b)),
        )
        change = next(
            item
            for item in source_diff.changes
            if isinstance(item, ModifiedChange)
            and isinstance(item.subject, FieldSubject)
            and item.subject.field is StructuralField.CONFIGURATION_ARTIFACTS
        )

        self.assertEqual(rendered_a.content, rendered_b.content)
        self.assertNotEqual(rendered_a.source_digest, rendered_b.source_digest)
        self.assertNotEqual(change.before.descriptor(), change.after.descriptor())


class ConfigurationRenderingTests(unittest.TestCase):
    def test_typed_parameters_render_one_deterministic_json_artifact(self) -> None:
        renderer = json_template(
            '{"upstreams":{{ upstreams | json }},"workers":{{ workers | json }}}\n'
        )
        parameters = ProxyConfiguration(2, ("http://api-a:8080", "http://api-b:8080"))

        first = renderer.render(parameters)
        second = renderer.render(parameters)

        self.assertEqual(first, second)
        self.assertEqual(
            first.content,
            '{"upstreams":["http://api-a:8080","http://api-b:8080"],"workers":2}\n',
        )
        self.assertEqual(first.media_type, ConfigurationMediaType.JSON)
        self.assertEqual(first.target_path, "/etc/proxy/config.json")
        self.assertNotEqual(first.source_digest, first.content_digest)

    def test_template_definition_changes_identity_even_when_output_does_not(self) -> None:
        first = json_template('{# revision-a #}{"workers":{{ workers | json }}}\n').render(
            ProxyConfiguration(2)
        )
        second = json_template(
            '{# revision-b #}{"workers":{{ workers | json }}}\n'
        ).render(ProxyConfiguration(2))

        self.assertEqual(first.content, second.content)
        self.assertEqual(first.content_digest, second.content_digest)
        self.assertNotEqual(first.source_digest, second.source_digest)
        self.assertNotEqual(first, second)

    def test_undefined_and_malformed_templates_fail_without_source_or_values(self) -> None:
        sensitive = "do-not-retain"
        with self.assertRaises(ConfigurationTemplateSyntaxError) as malformed:
            json_template("{% if broken %}{{")
        self.assertNotIn("broken", str(malformed.exception))

        renderer = json_template('{"missing":{{ absent | json }}}\n')
        with self.assertRaises(ConfigurationTemplateRenderError) as undefined:
            renderer.render(ProxyConfiguration(2, (sensitive,)))
        self.assertNotIn(sensitive, str(undefined.exception))
        self.assertNotIn("absent", str(undefined.exception))

    def test_context_is_closed_bounded_and_secret_free_before_render(self) -> None:
        class Parameters:
            def __init__(self, values):
                self.values = values

            def configuration_values(self):
                return self.values

        renderer = json_template('{"workers":{{ workers | json }}}\n')
        invalid = (
            {"api_token": "reference-only"},
            {"workers": [1, 2]},
            {"workers": object()},
            {"workers": float("inf")},
            {"workers": "postgresql://user:password@database/app"},
        )

        for values in invalid:
            with self.subTest(values=values), self.assertRaises(
                ConfigurationRenderingError,
            ):
                renderer.render(Parameters(values))

    def test_rendered_output_is_bounded_and_format_validated(self) -> None:
        class Parameters:
            def configuration_values(self):
                return {"value": "x" * 140_000}

        with self.assertRaises(ConfigurationTemplateRenderError):
            json_template("{{ value }}{{ value }}").render(Parameters())

        with self.assertRaisesRegex(ConfigurationArtifactError, "JSON"):
            json_template("{not-json}").render(ProxyConfiguration(2))


def text_artifact(content: str) -> ConfigurationArtifact:
    return ConfigurationArtifact(
        "service-config",
        "/etc/service/config.conf",
        ConfigurationMediaType.TEXT,
        content,
        ConfigurationFileMode.READ_ONLY,
    )


def template(revision: str) -> ConfigurationTemplate:
    return ConfigurationTemplate(
        "service-config",
        "service-config",
        "/etc/service/config.conf",
        ConfigurationMediaType.TEXT,
        f"{{# {revision} #}}workers = {{{{ workers }}}}\n",
    )


def json_template(source: str) -> ConfigurationTemplate:
    return ConfigurationTemplate(
        "proxy-config",
        "proxy-config",
        "/etc/proxy/config.json",
        ConfigurationMediaType.JSON,
        source,
    )


def graph_with_artifacts(*artifacts: ConfigurationArtifact):
    service = ApplicationBlock(
        BlockSpec("service"),
        PureImplementation("service", {}),
        BlockSockets(),
    )
    graph = compile_topology(
        DeploymentTopology("configuration-artifact", DockerRuntime(children=(service,)))
    )
    node = graph.node("service")
    return graph.update_node(
        replace(node, configuration_artifacts=artifacts)
    )


def _modified_fields(diff):
    return {
        change.subject.field
        for change in diff.changes
        if isinstance(change, ModifiedChange)
        and isinstance(change.subject, FieldSubject)
    }


if __name__ == "__main__":
    unittest.main()
