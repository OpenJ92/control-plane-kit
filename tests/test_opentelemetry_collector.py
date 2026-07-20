from __future__ import annotations

from dataclasses import replace
import unittest

import yaml

from control_plane_kit import (
    DeploymentRecipe,
    DockerRuntime,
    GraphDescriptorCodec,
    PackageServerProduct,
    Protocol,
    SecretReference,
    compile_recipe,
    diff_graphs,
    validate_graph,
)
from control_plane_kit.servers import (
    AttributeRedactionProcessor,
    BatchProcessor,
    DebugExporter,
    ExporterHeader,
    MemoryLimiterProcessor,
    OpenTelemetryCollectorConfiguration,
    OtlpHttpExporter,
    OtlpReceiver,
    ProbabilisticSamplingProcessor,
    TelemetryPipeline,
    TelemetrySignal,
    default_collector_configuration,
    opentelemetry_collector_block,
    package_server_contract,
    render_collector_configuration,
)


class OpenTelemetryCollectorTests(unittest.TestCase):
    def test_default_configuration_is_deterministic_closed_yaml(self) -> None:
        configuration = default_collector_configuration()

        first = render_collector_configuration(configuration)
        second = render_collector_configuration(configuration)
        parsed = yaml.safe_load(first.content)

        self.assertEqual(first, second)
        self.assertEqual(parsed["extensions"]["health_check"]["endpoint"], "0.0.0.0:13133")
        self.assertEqual(set(parsed["receivers"]["otlp"]["protocols"]), {"grpc", "http"})
        self.assertEqual(set(parsed["service"]["pipelines"]), {"traces", "metrics", "logs"})
        self.assertEqual(
            parsed["processors"]["attributes/redaction"]["actions"],
            [
                {"key": "http.request.header.authorization", "action": "delete"},
                {"key": "http.request.header.cookie", "action": "delete"},
            ],
        )

    def test_block_advertises_exact_otlp_and_health_sockets(self) -> None:
        block = opentelemetry_collector_block()

        self.assertIs(block.spec.product, PackageServerProduct.OPENTELEMETRY_COLLECTOR)
        self.assertEqual(block.sockets.requirement_names(), ())
        self.assertEqual(
            block.sockets.provider_names(),
            ("health", "otlp-grpc", "otlp-http"),
        )
        self.assertIs(block.sockets.provider("health").protocol, Protocol.HTTP)
        self.assertIs(block.sockets.provider("otlp-grpc").protocol, Protocol.OTLP_GRPC)
        self.assertIs(block.sockets.provider("otlp-http").protocol, Protocol.OTLP_HTTP)
        self.assertEqual(block.spec.verification.checks[0].provider_socket, "health")

    def test_remote_exporter_uses_socket_and_opaque_secret_delivery(self) -> None:
        exporter = OtlpHttpExporter(
            "archive",
            "archive",
            "CPK_OTEL_ARCHIVE_ENDPOINT",
            (
                ExporterHeader(
                    "Authorization",
                    "CPK_OTEL_ARCHIVE_AUTHORIZATION",
                    SecretReference("secret://otel/archive-authorization"),
                ),
            ),
        )
        configuration = replace(
            default_collector_configuration(),
            exporters=(DebugExporter(), exporter),
            pipelines=tuple(
                replace(value, exporters=("debug", "otlphttp/archive"))
                for value in default_collector_configuration().pipelines
            ),
        )

        block = opentelemetry_collector_block(configuration=configuration)
        artifact = block.implementation.configuration_artifacts[0]

        self.assertIs(block.sockets.requirement("archive").protocol, Protocol.OTLP_HTTP)
        self.assertEqual(
            block.sockets.requirement("archive").env_bindings,
            ("CPK_OTEL_ARCHIVE_ENDPOINT",),
        )
        self.assertEqual(
            block.implementation.secret_deliveries[0].reference.reference_id,
            "secret://otel/archive-authorization",
        )
        self.assertNotIn("secret://otel/archive-authorization", artifact.content)
        self.assertIn("${env:CPK_OTEL_ARCHIVE_AUTHORIZATION}", artifact.content)
        self.assertIn("${env:CPK_OTEL_ARCHIVE_ENDPOINT}", artifact.content)

    def test_invalid_pipeline_references_fail_at_pure_boundary(self) -> None:
        configuration = default_collector_configuration()

        with self.assertRaisesRegex(ValueError, "unknown exporter"):
            replace(
                configuration,
                pipelines=(
                    replace(configuration.pipelines[0], exporters=("missing",)),
                ),
            )

    def test_sampling_is_rejected_outside_trace_pipeline(self) -> None:
        sampler = ProbabilisticSamplingProcessor(10)
        processors = (*default_collector_configuration().processors, sampler)

        with self.assertRaisesRegex(ValueError, "only to traces"):
            OpenTelemetryCollectorConfiguration(
                receivers=(OtlpReceiver(),),
                processors=processors,
                exporters=(DebugExporter(),),
                pipelines=(
                    TelemetryPipeline(
                        TelemetrySignal.METRICS,
                        ("otlp",),
                        tuple(value.component_id for value in processors),
                        ("debug",),
                    ),
                ),
            )

    def test_provider_socket_names_and_ports_are_globally_unique(self) -> None:
        configuration = default_collector_configuration()

        with self.assertRaisesRegex(ValueError, "provider socket names"):
            replace(
                configuration,
                health=replace(configuration.health, socket_name="otlp-http"),
            )
        with self.assertRaisesRegex(ValueError, "provider ports"):
            replace(
                configuration,
                health=replace(configuration.health, port=4318),
            )

    def test_exporter_requirement_and_environment_names_are_globally_unique(self) -> None:
        first = OtlpHttpExporter("first", "archive", "OTEL_ENDPOINT_FIRST")
        repeated_socket = OtlpHttpExporter("second", "archive", "OTEL_ENDPOINT_SECOND")
        repeated_environment = OtlpHttpExporter(
            "second",
            "secondary",
            "OTEL_ENDPOINT_FIRST",
        )

        with self.assertRaisesRegex(ValueError, "requirement sockets"):
            replace(
                default_collector_configuration(),
                exporters=(DebugExporter(), first, repeated_socket),
            )
        with self.assertRaisesRegex(ValueError, "environment names"):
            replace(
                default_collector_configuration(),
                exporters=(DebugExporter(), first, repeated_environment),
            )

    def test_exporter_headers_are_bounded(self) -> None:
        headers = tuple(
            ExporterHeader(
                f"X-Header-{index}",
                f"OTEL_HEADER_{index}",
                SecretReference(f"secret://otel/header-{index}"),
            )
            for index in range(33)
        )

        with self.assertRaisesRegex(ValueError, "headers must be bounded"):
            OtlpHttpExporter("archive", "archive", "OTEL_ENDPOINT", headers)

    def test_graph_codec_and_diff_retain_exact_artifact_identity(self) -> None:
        current = _graph(default_collector_configuration())
        changed_configuration = OpenTelemetryCollectorConfiguration(
            receivers=(OtlpReceiver(),),
            processors=(
                MemoryLimiterProcessor(limit_mib=256),
                AttributeRedactionProcessor(("http.request.header.authorization",)),
                BatchProcessor(),
            ),
            exporters=(DebugExporter(),),
            pipelines=tuple(
                TelemetryPipeline(
                    signal,
                    ("otlp",),
                    ("memory_limiter", "attributes/redaction", "batch"),
                    ("debug",),
                )
                for signal in TelemetrySignal
            ),
        )
        desired = _graph(changed_configuration)
        codec = GraphDescriptorCodec()

        descriptor = codec.encode(current)
        difference = diff_graphs(validate_graph(current), validate_graph(desired))

        self.assertEqual(codec.encode(codec.decode(descriptor)), descriptor)
        self.assertNotEqual(
            current.node("collector").configuration_artifacts,
            desired.node("collector").configuration_artifacts,
        )
        self.assertTrue(difference.changes)

    def test_catalogue_marks_official_integration_operational(self) -> None:
        contract = package_server_contract(PackageServerProduct.OPENTELEMETRY_COLLECTOR)

        self.assertEqual(contract.block.spec.health_path, "/")
        self.assertEqual(contract.capabilities[0].path, "/")
        self.assertEqual(contract.descriptor()["product"], "opentelemetry-collector")


def _graph(configuration: OpenTelemetryCollectorConfiguration):
    return compile_recipe(
        DeploymentRecipe(
            "collector",
            DockerRuntime(
                runtime_id="docker",
                network_name="collector-network",
                children=(
                    opentelemetry_collector_block(
                        "collector",
                        configuration=configuration,
                    ),
                ),
            ),
        )
    )


if __name__ == "__main__":
    unittest.main()
