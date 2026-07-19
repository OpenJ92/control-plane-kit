"""Typed configuration and official-image adapter for OpenTelemetry Collector."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from importlib.resources import files
import re
from typing import Mapping, TypeAlias

from control_plane_kit.algebra import (
    ApplicationBlock,
    BlockSockets,
    PackageServerProduct,
    PackageServerSpec,
    ProductMaturity,
    ProviderSocket,
    RequirementSocket,
)
from control_plane_kit.capabilities import CapabilityName
from control_plane_kit.configuration import (
    ConfigurationArtifact,
    ConfigurationFileMode,
    ConfigurationMediaType,
)
from control_plane_kit.configuration_rendering import ConfigurationTemplate
from control_plane_kit.implementations import DockerImageImplementation
from control_plane_kit.secrets import SecretEnvironmentDelivery, SecretReference
from control_plane_kit.types import Protocol
from control_plane_kit.verification import HttpCheck, VerificationContract


COLLECTOR_IMAGE = "otel/opentelemetry-collector-contrib:0.156.0"
COLLECTOR_CONFIG_PATH = "/etc/cpk/opentelemetry-collector.yaml"
_IDENTITY = re.compile(r"[a-z][a-z0-9_-]{0,62}\Z")
_COMPONENT_REFERENCE = re.compile(
    r"[a-z][a-z0-9_]*(?:/[a-z][a-z0-9_-]{0,62})?\Z"
)
_ENVIRONMENT = re.compile(r"[A-Z][A-Z0-9_]{0,127}\Z")
_HEADER = re.compile(r"[A-Za-z][A-Za-z0-9-]{0,126}\Z")
_ATTRIBUTE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,254}\Z")


def _identity(value: str, name: str) -> None:
    if not isinstance(value, str) or not _IDENTITY.fullmatch(value):
        raise ValueError(f"{name} identity is invalid")


def _bounded_integer(value: int, name: str, minimum: int, maximum: int) -> None:
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")


class TelemetrySignal(StrEnum):
    TRACES = "traces"
    METRICS = "metrics"
    LOGS = "logs"


class DebugVerbosity(StrEnum):
    BASIC = "basic"
    NORMAL = "normal"
    DETAILED = "detailed"


@dataclass(frozen=True)
class OtlpReceiverProtocol:
    socket_name: str
    protocol: Protocol
    port: int

    def __post_init__(self) -> None:
        _identity(self.socket_name, "OTLP receiver socket")
        if self.protocol not in (Protocol.OTLP_HTTP, Protocol.OTLP_GRPC):
            raise ValueError("OTLP receiver requires an exact OTLP protocol")
        _bounded_integer(self.port, "OTLP receiver port", 1, 65_535)


@dataclass(frozen=True)
class OtlpReceiver:
    protocols: tuple[OtlpReceiverProtocol, ...] = (
        OtlpReceiverProtocol("otlp-grpc", Protocol.OTLP_GRPC, 4317),
        OtlpReceiverProtocol("otlp-http", Protocol.OTLP_HTTP, 4318),
    )
    component_id: str = field(default="otlp", init=False)

    def __post_init__(self) -> None:
        if not self.protocols or any(
            not isinstance(value, OtlpReceiverProtocol) for value in self.protocols
        ):
            raise TypeError("OTLP receiver protocols must be typed and nonempty")
        if len({value.protocol for value in self.protocols}) != len(self.protocols):
            raise ValueError("OTLP receiver repeats a protocol")
        if len({value.socket_name for value in self.protocols}) != len(self.protocols):
            raise ValueError("OTLP receiver repeats a socket")
        if len({value.port for value in self.protocols}) != len(self.protocols):
            raise ValueError("OTLP receiver ports must be unique")


@dataclass(frozen=True)
class MemoryLimiterProcessor:
    check_interval_seconds: int = 1
    limit_mib: int = 128
    spike_limit_mib: int = 32
    component_id: str = field(default="memory_limiter", init=False)

    def __post_init__(self) -> None:
        _bounded_integer(self.check_interval_seconds, "memory check interval", 1, 60)
        _bounded_integer(self.limit_mib, "memory limit", 16, 65_536)
        _bounded_integer(self.spike_limit_mib, "memory spike limit", 1, self.limit_mib)


@dataclass(frozen=True)
class BatchProcessor:
    timeout_seconds: int = 1
    send_batch_size: int = 512
    send_batch_max_size: int = 1_024
    component_id: str = field(default="batch", init=False)

    def __post_init__(self) -> None:
        _bounded_integer(self.timeout_seconds, "batch timeout", 1, 60)
        _bounded_integer(self.send_batch_size, "batch size", 1, 65_536)
        _bounded_integer(
            self.send_batch_max_size,
            "maximum batch size",
            self.send_batch_size,
            131_072,
        )


@dataclass(frozen=True)
class AttributeRedactionProcessor:
    attribute_keys: tuple[str, ...]
    component_id: str = field(default="attributes/redaction", init=False)

    def __post_init__(self) -> None:
        if not self.attribute_keys or len(self.attribute_keys) > 100:
            raise ValueError("redaction keys must be nonempty and bounded")
        if any(not isinstance(value, str) or not _ATTRIBUTE.fullmatch(value) for value in self.attribute_keys):
            raise ValueError("redaction attribute key is invalid")
        if len(set(self.attribute_keys)) != len(self.attribute_keys):
            raise ValueError("redaction attribute keys must be unique")


@dataclass(frozen=True)
class ProbabilisticSamplingProcessor:
    sampling_percentage: float
    component_id: str = field(default="probabilistic_sampler", init=False)

    def __post_init__(self) -> None:
        if (
            not isinstance(self.sampling_percentage, (int, float))
            or isinstance(self.sampling_percentage, bool)
            or not 0 <= self.sampling_percentage <= 100
        ):
            raise ValueError("sampling percentage must be between zero and 100")


CollectorProcessor: TypeAlias = (
    MemoryLimiterProcessor
    | BatchProcessor
    | AttributeRedactionProcessor
    | ProbabilisticSamplingProcessor
)


@dataclass(frozen=True)
class DebugExporter:
    verbosity: DebugVerbosity = DebugVerbosity.NORMAL
    component_id: str = field(default="debug", init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.verbosity, DebugVerbosity):
            raise TypeError("debug exporter verbosity must be typed")


@dataclass(frozen=True)
class ExporterHeader:
    name: str
    environment_name: str
    secret_reference: SecretReference

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not _HEADER.fullmatch(self.name):
            raise ValueError("exporter header name is invalid")
        if not isinstance(self.environment_name, str) or not _ENVIRONMENT.fullmatch(
            self.environment_name
        ):
            raise ValueError("exporter header environment name is invalid")
        if not isinstance(self.secret_reference, SecretReference):
            raise TypeError("exporter header requires an opaque SecretReference")


@dataclass(frozen=True)
class OtlpHttpExporter:
    instance_name: str
    requirement_socket: str
    endpoint_environment_name: str
    headers: tuple[ExporterHeader, ...] = ()
    queue_size: int = 1_000
    retry_max_elapsed_seconds: int = 300
    component_id: str = field(init=False)

    def __post_init__(self) -> None:
        _identity(self.instance_name, "OTLP HTTP exporter")
        object.__setattr__(self, "component_id", f"otlphttp/{self.instance_name}")
        _identity(self.requirement_socket, "OTLP HTTP exporter requirement")
        if not _ENVIRONMENT.fullmatch(self.endpoint_environment_name):
            raise ValueError("OTLP HTTP exporter endpoint environment name is invalid")
        if any(not isinstance(value, ExporterHeader) for value in self.headers):
            raise TypeError("OTLP HTTP exporter headers must be typed")
        if len(self.headers) > 32:
            raise ValueError("OTLP HTTP exporter headers must be bounded")
        if len({value.name.lower() for value in self.headers}) != len(self.headers):
            raise ValueError("OTLP HTTP exporter header names must be unique")
        if len({value.environment_name for value in self.headers}) != len(self.headers):
            raise ValueError("OTLP HTTP exporter header environments must be unique")
        _bounded_integer(self.queue_size, "exporter queue size", 1, 100_000)
        _bounded_integer(
            self.retry_max_elapsed_seconds,
            "exporter retry elapsed time",
            1,
            3_600,
        )


CollectorExporter: TypeAlias = DebugExporter | OtlpHttpExporter


@dataclass(frozen=True)
class HealthCheckExtension:
    socket_name: str = "health"
    port: int = 13_133
    component_id: str = field(default="health_check", init=False)

    def __post_init__(self) -> None:
        _identity(self.socket_name, "health socket")
        _bounded_integer(self.port, "health port", 1, 65_535)


@dataclass(frozen=True)
class TelemetryPipeline:
    signal: TelemetrySignal
    receivers: tuple[str, ...]
    processors: tuple[str, ...]
    exporters: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.signal, TelemetrySignal):
            raise TypeError("telemetry pipeline signal must be typed")
        for label, values in (
            ("receivers", self.receivers),
            ("processors", self.processors),
            ("exporters", self.exporters),
        ):
            if not values or len(values) > 32 or any(
                not isinstance(value, str) or not _COMPONENT_REFERENCE.fullmatch(value)
                for value in values
            ):
                raise ValueError(f"telemetry pipeline {label} must be typed identities")
            if len(set(values)) != len(values):
                raise ValueError(f"telemetry pipeline {label} must be unique")


@dataclass(frozen=True)
class OpenTelemetryCollectorConfiguration:
    receivers: tuple[OtlpReceiver, ...]
    processors: tuple[CollectorProcessor, ...]
    exporters: tuple[CollectorExporter, ...]
    pipelines: tuple[TelemetryPipeline, ...]
    health: HealthCheckExtension = HealthCheckExtension()

    def __post_init__(self) -> None:
        _typed_nonempty(self.receivers, OtlpReceiver, "collector receivers")
        _typed_nonempty(
            self.processors,
            (
                MemoryLimiterProcessor,
                BatchProcessor,
                AttributeRedactionProcessor,
                ProbabilisticSamplingProcessor,
            ),
            "collector processors",
        )
        _typed_nonempty(
            self.exporters,
            (DebugExporter, OtlpHttpExporter),
            "collector exporters",
        )
        _typed_nonempty(self.pipelines, TelemetryPipeline, "collector pipelines")
        if not isinstance(self.health, HealthCheckExtension):
            raise TypeError("collector health extension must be typed")
        receiver_ids = _unique_component_ids(self.receivers, "collector receivers")
        processor_ids = _unique_component_ids(self.processors, "collector processors")
        exporter_ids = _unique_component_ids(self.exporters, "collector exporters")
        receiver_protocols = tuple(
            protocol
            for receiver in self.receivers
            for protocol in receiver.protocols
        )
        _unique_values(
            (self.health.socket_name, *(value.socket_name for value in receiver_protocols)),
            "collector provider socket names",
        )
        _unique_values(
            (self.health.port, *(value.port for value in receiver_protocols)),
            "collector provider ports",
        )
        remote_exporters = tuple(
            value for value in self.exporters if isinstance(value, OtlpHttpExporter)
        )
        _unique_values(
            tuple(value.requirement_socket for value in remote_exporters),
            "collector exporter requirement sockets",
        )
        _unique_values(
            tuple(
                environment_name
                for exporter in remote_exporters
                for environment_name in (
                    exporter.endpoint_environment_name,
                    *(header.environment_name for header in exporter.headers),
                )
            ),
            "collector exporter environment names",
        )
        if len({value.signal for value in self.pipelines}) != len(self.pipelines):
            raise ValueError("collector signal pipelines must be unique")
        for pipeline in self.pipelines:
            if not set(pipeline.receivers).issubset(receiver_ids):
                raise ValueError("collector pipeline references an unknown receiver")
            if not set(pipeline.processors).issubset(processor_ids):
                raise ValueError("collector pipeline references an unknown processor")
            if not set(pipeline.exporters).issubset(exporter_ids):
                raise ValueError("collector pipeline references an unknown exporter")
            sampling = {
                value.component_id
                for value in self.processors
                if isinstance(value, ProbabilisticSamplingProcessor)
            }
            if pipeline.signal is not TelemetrySignal.TRACES and sampling.intersection(
                pipeline.processors
            ):
                raise ValueError("probabilistic sampling belongs only to traces")

    def configuration_values(self) -> Mapping[str, object]:
        return {
            "receivers": tuple(_receiver_value(value) for value in self.receivers),
            "processors": tuple(_processor_value(value) for value in self.processors),
            "exporters": tuple(_exporter_value(value) for value in self.exporters),
            "pipelines": tuple(_pipeline_value(value) for value in self.pipelines),
            "health": {
                "component_id": self.health.component_id,
                "endpoint": f"0.0.0.0:{self.health.port}",
            },
        }


def default_collector_configuration() -> OpenTelemetryCollectorConfiguration:
    processors: tuple[CollectorProcessor, ...] = (
        MemoryLimiterProcessor(),
        AttributeRedactionProcessor(
            ("http.request.header.authorization", "http.request.header.cookie")
        ),
        BatchProcessor(),
    )
    return OpenTelemetryCollectorConfiguration(
        receivers=(OtlpReceiver(),),
        processors=processors,
        exporters=(DebugExporter(),),
        pipelines=tuple(
            TelemetryPipeline(
                signal,
                ("otlp",),
                tuple(value.component_id for value in processors),
                ("debug",),
            )
            for signal in TelemetrySignal
        ),
    )


def render_collector_configuration(
    configuration: OpenTelemetryCollectorConfiguration,
) -> ConfigurationArtifact:
    if not isinstance(configuration, OpenTelemetryCollectorConfiguration):
        raise TypeError("collector renderer requires typed configuration")
    source = files("control_plane_kit").joinpath(
        "product_templates/opentelemetry_collector.yaml.j2"
    ).read_text(encoding="utf-8")
    return ConfigurationTemplate(
        "opentelemetry-collector-config",
        "opentelemetry-collector-config",
        COLLECTOR_CONFIG_PATH,
        ConfigurationMediaType.YAML,
        source,
        ConfigurationFileMode.READ_ONLY,
    ).render(configuration)


def opentelemetry_collector_block(
    block_id: str = "opentelemetry-collector",
    *,
    display_name: str = "OpenTelemetry Collector",
    image: str = COLLECTOR_IMAGE,
    configuration: OpenTelemetryCollectorConfiguration | None = None,
) -> ApplicationBlock:
    config = default_collector_configuration() if configuration is None else configuration
    if not isinstance(config, OpenTelemetryCollectorConfiguration):
        raise TypeError("collector block requires typed configuration")
    requirements = tuple(
        RequirementSocket(
            exporter.requirement_socket,
            Protocol.OTLP_HTTP,
            (exporter.endpoint_environment_name,),
        )
        for exporter in config.exporters
        if isinstance(exporter, OtlpHttpExporter)
    )
    receiver_protocols = tuple(
        protocol
        for receiver in config.receivers
        for protocol in receiver.protocols
    )
    providers = (
        ProviderSocket(config.health.socket_name, Protocol.HTTP),
        *(ProviderSocket(value.socket_name, value.protocol) for value in receiver_protocols),
    )
    ports = {
        config.health.socket_name: config.health.port,
        **{value.socket_name: value.port for value in receiver_protocols},
    }
    secret_deliveries = tuple(
        SecretEnvironmentDelivery(header.environment_name, header.secret_reference)
        for exporter in config.exporters
        if isinstance(exporter, OtlpHttpExporter)
        for header in exporter.headers
    )
    return ApplicationBlock(
        PackageServerSpec(
            role_id=block_id,
            product=PackageServerProduct.OPENTELEMETRY_COLLECTOR,
            maturity=ProductMaturity.OPERATIONAL,
            display_name=display_name,
            health_path="/",
            capabilities=(CapabilityName.HEALTH_CHECKABLE,),
            verification=VerificationContract(
                (
                    HttpCheck(
                        check_id="collector-health",
                        provider_socket=config.health.socket_name,
                        path="/",
                    ),
                )
            ),
        ),
        DockerImageImplementation(
            image=image,
            command=(f"--config={COLLECTOR_CONFIG_PATH}",),
            ports=ports,
            configuration_artifacts=(render_collector_configuration(config),),
            secret_deliveries=secret_deliveries,
        ),
        BlockSockets(requirements=requirements, providers=providers),
    )


def _receiver_value(value: OtlpReceiver) -> Mapping[str, object]:
    return {
        "component_id": value.component_id,
        "protocols": tuple(
            {
                "kind": "http" if item.protocol is Protocol.OTLP_HTTP else "grpc",
                "endpoint": f"0.0.0.0:{item.port}",
            }
            for item in value.protocols
        ),
    }


def _processor_value(value: CollectorProcessor) -> Mapping[str, object]:
    match value:
        case MemoryLimiterProcessor():
            return {
                "kind": "memory-limiter",
                "component_id": value.component_id,
                "check_interval": f"{value.check_interval_seconds}s",
                "limit_mib": value.limit_mib,
                "spike_limit_mib": value.spike_limit_mib,
            }
        case BatchProcessor():
            return {
                "kind": "batch",
                "component_id": value.component_id,
                "timeout": f"{value.timeout_seconds}s",
                "send_batch_size": value.send_batch_size,
                "send_batch_max_size": value.send_batch_max_size,
            }
        case AttributeRedactionProcessor():
            return {
                "kind": "redaction",
                "component_id": value.component_id,
                "attribute_keys": tuple(sorted(value.attribute_keys)),
            }
        case ProbabilisticSamplingProcessor():
            return {
                "kind": "probabilistic-sampling",
                "component_id": value.component_id,
                "sampling_percentage": float(value.sampling_percentage),
            }


def _exporter_value(value: CollectorExporter) -> Mapping[str, object]:
    match value:
        case DebugExporter():
            return {
                "kind": "debug",
                "component_id": value.component_id,
                "verbosity": value.verbosity.value,
            }
        case OtlpHttpExporter():
            return {
                "kind": "otlp-http",
                "component_id": value.component_id,
                "endpoint_environment_name": value.endpoint_environment_name,
                "headers": tuple(
                    {
                        "name": header.name,
                        "environment_name": header.environment_name,
                    }
                    for header in value.headers
                ),
                "queue_size": value.queue_size,
                "retry_max_elapsed": f"{value.retry_max_elapsed_seconds}s",
            }


def _pipeline_value(value: TelemetryPipeline) -> Mapping[str, object]:
    return {
        "signal": value.signal.value,
        "receivers": value.receivers,
        "processors": value.processors,
        "exporters": value.exporters,
    }


def _typed_nonempty(values: tuple[object, ...], expected, name: str) -> None:
    if not isinstance(values, tuple) or not values or len(values) > 32 or any(
        not isinstance(value, expected) for value in values
    ):
        raise TypeError(f"{name} must be a bounded typed tuple")


def _unique_component_ids(values: tuple[object, ...], name: str) -> set[str]:
    identities = tuple(value.component_id for value in values)
    _unique_values(identities, name)
    return set(identities)


def _unique_values(values: tuple[object, ...], name: str) -> None:
    if len(set(values)) != len(values):
        raise ValueError(f"{name} must be unique")
