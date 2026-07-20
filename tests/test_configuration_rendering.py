from __future__ import annotations

from dataclasses import dataclass
import unittest

from control_plane_kit import (
    ConfigurationArtifactError,
    ConfigurationMediaType,
)
from control_plane_kit.interpreters.configuration_rendering import (
    ConfigurationRenderingError,
    ConfigurationTemplate,
    ConfigurationTemplateRenderError,
    ConfigurationTemplateSyntaxError,
)


@dataclass(frozen=True)
class ProxyConfiguration:
    workers: int
    upstreams: tuple[str, ...]

    def configuration_values(self):
        return {
            "upstreams": self.upstreams,
            "workers": self.workers,
        }


class ConfigurationRenderingTests(unittest.TestCase):
    def test_typed_parameters_render_one_deterministic_json_artifact(self) -> None:
        template = _template(
            '{"upstreams":{{ upstreams | json }},"workers":{{ workers | json }}}\n'
        )
        parameters = ProxyConfiguration(2, ("http://api-a:8080", "http://api-b:8080"))

        first = template.render(parameters)
        second = template.render(parameters)

        self.assertEqual(first, second)
        self.assertEqual(
            first.content,
            '{"upstreams":["http://api-a:8080","http://api-b:8080"],"workers":2}\n',
        )
        self.assertEqual(first.media_type, ConfigurationMediaType.JSON)
        self.assertEqual(first.target_path, "/etc/proxy/config.json")
        self.assertNotEqual(first.source_digest, first.content_digest)

    def test_template_definition_changes_artifact_identity_even_when_output_does_not(self) -> None:
        first = _template(
            '{# revision-a #}{"workers":{{ workers | json }}}\n'
        ).render(ProxyConfiguration(2, ()))
        second = _template(
            '{# revision-b #}{"workers":{{ workers | json }}}\n'
        ).render(ProxyConfiguration(2, ()))

        self.assertEqual(first.content, second.content)
        self.assertEqual(first.content_digest, second.content_digest)
        self.assertNotEqual(first.source_digest, second.source_digest)
        self.assertNotEqual(first, second)

    def test_undefined_and_malformed_templates_fail_without_source_or_values(self) -> None:
        sensitive = "do-not-retain"
        with self.assertRaises(ConfigurationTemplateSyntaxError) as malformed:
            _template("{% if broken %}{{")
        self.assertNotIn("broken", str(malformed.exception))

        template = _template('{"missing":{{ absent | json }}}\n')
        with self.assertRaises(ConfigurationTemplateRenderError) as undefined:
            template.render(ProxyConfiguration(2, (sensitive,)))
        self.assertNotIn(sensitive, str(undefined.exception))
        self.assertNotIn("absent", str(undefined.exception))

    def test_context_is_closed_bounded_and_secret_free_before_render(self) -> None:
        class Parameters:
            def __init__(self, values):
                self.values = values

            def configuration_values(self):
                return self.values

        template = _template('{"workers":{{ workers | json }}}\n')
        invalid = (
            {"api_token": "reference-only"},
            {"workers": [1, 2]},
            {"workers": object()},
            {"workers": float("inf")},
            {"workers": "postgresql://user:password@database/app"},
        )

        for values in invalid:
            with self.subTest(values=type(next(iter(values.values()))).__name__):
                with self.assertRaises(ConfigurationRenderingError):
                    template.render(Parameters(values))

    def test_rendered_output_is_bounded_and_format_validated(self) -> None:
        class Parameters:
            def configuration_values(self):
                return {"value": "x" * 140_000}

        oversized = _template("{{ value }}{{ value }}")
        with self.assertRaises(ConfigurationTemplateRenderError):
            oversized.render(Parameters())

        malformed_json = _template("{not-json}")
        with self.assertRaisesRegex(ConfigurationArtifactError, "JSON"):
            malformed_json.render(ProxyConfiguration(2, ()))


def _template(source: str) -> ConfigurationTemplate:
    return ConfigurationTemplate(
        "proxy-config",
        "proxy-config",
        "/etc/proxy/config.json",
        ConfigurationMediaType.JSON,
        source,
    )


if __name__ == "__main__":
    unittest.main()
