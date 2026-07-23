"""Local Docker realization adapter for registered OCI product activities."""

from __future__ import annotations

from dataclasses import dataclass
import os
import re
import subprocess
from typing import Mapping, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import HTTPRedirectHandler, Request, build_opener

from control_plane_kit_core.lifecycle import ResourceOwnership
from control_plane_kit_core.operations.execution import EffectResultKind
from control_plane_kit_core.operations.lifecycle import FailureCategory
from control_plane_kit_core.planning import (
    RemoveNodeResource,
    RemoveRuntimeResource,
    ReconcileNode,
    StartNode,
    StartRuntime,
    StopNode,
    StopRuntime,
    WaitForHealthy,
)
from control_plane_kit_core.products import (
    ProductDescriptorDigest,
    ProductIdentity,
    ProductReference,
    RetainedDataMount,
)
from control_plane_kit_core.probe_intents import ProbeKind, ProbeOutcome
from control_plane_kit_core.secrets import (
    SecretEnvironmentDelivery,
    SecretReferenceEnvironmentDelivery,
    SecretResolutionCode,
    SecretResolutionError,
    SecretResolver,
    require_resolved_secret,
)
from control_plane_kit_core.topology import DEFAULT_GRAPH_CODEC, DeploymentGraph
from control_plane_kit_core.types import RuntimeKind
from control_plane_kit_core.verification import (
    HttpCheck,
    PostgresQueryCheck,
    VerificationOutcome,
)
from control_plane_kit_operations.coordinator import (
    ActivityExecutionOutcome,
    ActivityRealizationContext,
)
from control_plane_kit_operations.records import (
    BoundedEvidence,
    FailureEvidence,
    ObservationRecord,
    ObservationStatus,
)
from control_plane_kit_operations.workflows import InvalidOperationCommand


_IDENTITY = re.compile(r"[^a-zA-Z0-9_.-]+")
_LABEL_OWNER = "control-plane-kit.owner"
_LABEL_OWNER_VALUE = "operations"
_LABEL_WORKSPACE = "control-plane-kit.workspace-id"
_LABEL_PLAN = "control-plane-kit.plan-id"
_LABEL_GRAPH = "control-plane-kit.graph-id"
_LABEL_RUNTIME = "control-plane-kit.runtime-id"
_LABEL_NODE = "control-plane-kit.node-id"
_LABEL_PRODUCT = "control-plane-kit.product-identity"
_LABEL_DESCRIPTOR = "control-plane-kit.product-descriptor-sha256"
_LABEL_DATA_RESOURCE = "control-plane-kit.data-resource-id"


class DockerRealizationClient(Protocol):
    """Small local Docker capability surface used by operations realization."""

    def inspect_network(self, name: str) -> "DockerResourceInspection | None": ...

    def create_network(self, name: str, *, labels: dict[str, str]) -> None: ...

    def inspect_container(self, name: str) -> "DockerResourceInspection | None": ...

    def inspect_volume(self, name: str) -> "DockerResourceInspection | None": ...

    def create_volume(self, name: str, *, labels: dict[str, str]) -> None: ...

    def pull_image(self, image: str) -> None: ...

    def run_container(
        self,
        *,
        name: str,
        image: str,
        network: str,
        environment: dict[str, str],
        labels: dict[str, str],
        network_aliases: tuple[str, ...],
        mounts: dict[str, str],
    ) -> None: ...

    def start_container(self, name: str) -> None: ...

    def stop_container(self, name: str) -> None: ...

    def remove_container(self, name: str) -> None: ...

    def remove_network(self, name: str) -> None: ...


class DockerHealthCheckClient(Protocol):
    """Runtime-private health check capability for Docker-local endpoints."""

    def check_http(self, base_url: str, check: HttpCheck) -> "DockerHealthCheckResult": ...

    def check_postgres(
        self,
        base_url: str,
        check: PostgresQueryCheck,
    ) -> "DockerHealthCheckResult": ...


@dataclass(frozen=True)
class DockerResourceInspection:
    """Small normalized Docker resource inspection used for ownership checks."""

    name: str
    running: bool
    image: str | None
    labels: Mapping[str, str]


@dataclass(frozen=True)
class DockerHealthCheckResult:
    """Bounded result of one Docker-local semantic health check."""

    outcome: VerificationOutcome | None
    evidence: BoundedEvidence
    unsupported_reason: str | None = None

    @classmethod
    def passed(cls, evidence: Mapping[str, object]) -> "DockerHealthCheckResult":
        return cls(VerificationOutcome.PASSED, BoundedEvidence.from_mapping(evidence))

    @classmethod
    def failed(
        cls,
        outcome: VerificationOutcome,
        evidence: Mapping[str, object],
    ) -> "DockerHealthCheckResult":
        if outcome is VerificationOutcome.PASSED:
            raise ValueError("failed health check result cannot be passed")
        return cls(outcome, BoundedEvidence.from_mapping(evidence))

    @classmethod
    def unsupported(cls, reason: str) -> "DockerHealthCheckResult":
        return cls(None, BoundedEvidence(), reason)

    def __post_init__(self) -> None:
        if self.outcome is not None and not isinstance(self.outcome, VerificationOutcome):
            raise TypeError("health check outcome must be VerificationOutcome")
        if not isinstance(self.evidence, BoundedEvidence):
            raise TypeError("health check evidence must be BoundedEvidence")
        if self.unsupported_reason is not None and not isinstance(
            self.unsupported_reason,
            str,
        ):
            raise TypeError("unsupported reason must be text")


class _NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None


@dataclass(frozen=True)
class StdlibDockerHealthCheckClient:
    """Stdlib implementation for HTTP checks; other protocols fail closed."""

    def check_http(self, base_url: str, check: HttpCheck) -> DockerHealthCheckResult:
        url = urljoin(base_url.rstrip("/") + "/", check.path.lstrip("/"))
        opener = build_opener(_NoRedirects)
        attempts = 0
        last: DockerHealthCheckResult | None = None
        for _ in range(check.policy.maximum_attempts):
            attempts += 1
            try:
                with opener.open(
                    Request(url, method="GET"),
                    timeout=float(check.policy.timeout_seconds),
                ) as response:
                    body = response.read(check.policy.maximum_evidence_bytes + 1)
                    evidence = {
                        "kind": "http",
                        "url": url,
                        "status_code": int(response.status),
                        "response_bytes": min(
                            len(body),
                            check.policy.maximum_evidence_bytes,
                        ),
                        "attempts": attempts,
                    }
                    if int(response.status) in check.expected_statuses:
                        return DockerHealthCheckResult.passed(evidence)
                    last = DockerHealthCheckResult.failed(
                        VerificationOutcome.FAILED,
                        evidence,
                    )
            except HTTPError as error:
                last = DockerHealthCheckResult.failed(
                    VerificationOutcome.FAILED,
                    {
                        "kind": "http",
                        "url": url,
                        "status_code": int(error.code),
                        "response_bytes": 0,
                        "attempts": attempts,
                    },
                )
            except TimeoutError:
                last = DockerHealthCheckResult.failed(
                    VerificationOutcome.TIMED_OUT,
                    {"kind": "http", "url": url, "attempts": attempts},
                )
            except (OSError, URLError, ValueError):
                last = DockerHealthCheckResult.failed(
                    VerificationOutcome.MALFORMED,
                    {"kind": "http", "url": url, "attempts": attempts},
                )
        assert last is not None
        return last

    def check_postgres(
        self,
        base_url: str,
        check: PostgresQueryCheck,
    ) -> DockerHealthCheckResult:
        del base_url, check
        return DockerHealthCheckResult.unsupported(
            "postgres health verification requires an injected Postgres check client"
        )


@dataclass(frozen=True)
class DockerCliRealizationClient:
    """Docker CLI backed implementation of the local realization client."""

    docker: str = "docker"
    timeout_seconds: int = 30

    def inspect_network(self, name: str) -> DockerResourceInspection | None:
        if not self._exists("network", name):
            return None
        labels = self._labels("network", name)
        return DockerResourceInspection(name=name, running=False, image=None, labels=labels)

    def create_network(self, name: str, *, labels: dict[str, str]) -> None:
        args = ["network", "create"]
        for key, value in sorted(labels.items()):
            args.extend(("--label", f"{key}={value}"))
        args.append(name)
        self._run(args)

    def inspect_container(self, name: str) -> DockerResourceInspection | None:
        if not self._exists("container", name):
            return None
        labels = self._labels("container", name)
        image = self._capture(
            ["container", "inspect", "--format", "{{.Config.Image}}", name]
        ).strip()
        running = (
            self._capture(
                ["container", "inspect", "--format", "{{.State.Running}}", name]
            ).strip()
            == "true"
        )
        return DockerResourceInspection(
            name=name,
            running=running,
            image=image,
            labels=labels,
        )

    def inspect_volume(self, name: str) -> DockerResourceInspection | None:
        if not self._exists("volume", name):
            return None
        labels = self._labels("volume", name)
        return DockerResourceInspection(name=name, running=False, image=None, labels=labels)

    def create_volume(self, name: str, *, labels: dict[str, str]) -> None:
        args = ["volume", "create"]
        for key, value in sorted(labels.items()):
            args.extend(("--label", f"{key}={value}"))
        args.append(name)
        self._run(args)

    def pull_image(self, image: str) -> None:
        self._run(["pull", image])

    def run_container(
        self,
        *,
        name: str,
        image: str,
        network: str,
        environment: dict[str, str],
        labels: dict[str, str],
        network_aliases: tuple[str, ...],
        mounts: dict[str, str],
    ) -> None:
        args = ["run", "-d", "--name", name, "--network", network]
        for alias in network_aliases:
            args.extend(("--network-alias", alias))
        for key in sorted(environment):
            args.extend(("-e", key))
        for key, value in sorted(labels.items()):
            args.extend(("--label", f"{key}={value}"))
        for volume_name, target_path in sorted(mounts.items()):
            args.extend(("--mount", f"type=volume,source={volume_name},target={target_path}"))
        args.append(image)
        process_environment = None
        if environment:
            process_environment = {**os.environ, **environment}
        self._run(args, environment=process_environment)

    def start_container(self, name: str) -> None:
        self._run(["start", name])

    def stop_container(self, name: str) -> None:
        self._run(["stop", name])

    def remove_container(self, name: str) -> None:
        self._run(["rm", name])

    def remove_network(self, name: str) -> None:
        self._run(["network", "rm", name])

    def _exists(self, kind: str, name: str) -> bool:
        result = subprocess.run(
            [self.docker, kind, "inspect", name],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=self.timeout_seconds,
        )
        return result.returncode == 0

    def _labels(self, kind: str, name: str) -> dict[str, str]:
        output = self._capture(
            [kind, "inspect", "--format", "{{range $k,$v := .Config.Labels}}{{$k}}={{$v}}\n{{end}}", name]
            if kind == "container"
            else [kind, "inspect", "--format", "{{range $k,$v := .Labels}}{{$k}}={{$v}}\n{{end}}", name]
        )
        labels: dict[str, str] = {}
        for line in output.splitlines():
            key, _, value = line.partition("=")
            if key:
                labels[key] = value
        return labels

    def _capture(self, args: list[str]) -> str:
        return subprocess.run(
            [self.docker, *args],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=self.timeout_seconds,
        ).stdout

    def _run(
        self,
        args: list[str],
        *,
        environment: Mapping[str, str] | None = None,
    ) -> None:
        subprocess.run(
            [self.docker, *args],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            timeout=self.timeout_seconds,
            env=None if environment is None else {**environment},
        )


@dataclass(frozen=True)
class DockerProductRealizationAdapter:
    """Execute local Docker activities from pinned realization context."""

    project_name: str = "control-plane-kit"
    client: DockerRealizationClient = DockerCliRealizationClient()
    secret_resolver: SecretResolver | None = None
    health: DockerHealthCheckClient = StdlibDockerHealthCheckClient()

    def execute(self, context: ActivityRealizationContext) -> ActivityExecutionOutcome:
        if not isinstance(context, ActivityRealizationContext):
            raise InvalidOperationCommand(
                "docker realization requires ActivityRealizationContext"
            )
        try:
            return self._execute(context)
        except _UnsupportedDockerRealization as error:
            return ActivityExecutionOutcome.unsupported(
                FailureEvidence(
                    FailureCategory.OPERATOR_REVIEW,
                    "docker.product-runtime-unsupported",
                    str(error),
                    error.details,
                )
            )
        except _DockerOwnershipConflict as error:
            return ActivityExecutionOutcome.failed(
                FailureEvidence(
                    FailureCategory.TERMINAL,
                    "docker.ownership-conflict",
                    str(error),
                    error.details,
                )
            )
        except SecretResolutionError as error:
            return ActivityExecutionOutcome.failed(
                FailureEvidence(
                    FailureCategory.TERMINAL,
                    _secret_failure_code(error.code),
                    "Docker secret resolution failed before resource mutation",
                )
            )
        except subprocess.TimeoutExpired:
            return ActivityExecutionOutcome.uncertain(
                FailureEvidence(
                    FailureCategory.UNCERTAIN,
                    "docker.timeout",
                    "Docker command timed out before result was known",
                )
            )
        except subprocess.CalledProcessError:
            return ActivityExecutionOutcome.failed(
                FailureEvidence(
                    FailureCategory.TERMINAL,
                    "docker.command-failed",
                    "Docker command failed",
                )
            )

    def _execute(self, context: ActivityRealizationContext) -> ActivityExecutionOutcome:
        match context.activity.operation:
            case StartRuntime() as operation:
                return self._start_runtime(context, _desired_graph(context), operation)
            case StartNode() as operation:
                return self._start_node(context, _desired_graph(context), operation)
            case WaitForHealthy() as operation:
                return self._wait_for_healthy(context, _desired_graph(context), operation)
            case ReconcileNode() as operation:
                return self._reconcile_node(context, _desired_graph(context), operation)
            case StopNode() as operation:
                return self._stop_node(context, _base_graph(context), operation)
            case RemoveNodeResource() as operation:
                return self._remove_node_resource(context, _base_graph(context), operation)
            case StopRuntime() as operation:
                return self._stop_runtime(context, _base_graph(context), operation)
            case RemoveRuntimeResource() as operation:
                return self._remove_runtime_resource(context, _base_graph(context), operation)
            case _:
                return ActivityExecutionOutcome.unsupported(
                    FailureEvidence(
                        FailureCategory.OPERATOR_REVIEW,
                        "docker.operation-unsupported",
                        "Docker realization does not support this activity operation yet",
                        BoundedEvidence.from_mapping(
                            {
                                "activity_id": context.activity.activity_id.value,
                                "operation": type(context.activity.operation).__name__,
                            }
                        ),
                    )
                )

    def _start_runtime(
        self,
        context: ActivityRealizationContext,
        graph: DeploymentGraph,
        operation: StartRuntime,
    ) -> ActivityExecutionOutcome:
        runtime = graph.runtimes[operation.target.runtime_id]
        if runtime.kind is not RuntimeKind.DOCKER:
            raise _UnsupportedDockerRealization(
                f"runtime {runtime.runtime_id!r} is not Docker",
                runtime_id=runtime.runtime_id,
            )
        network_name = _network_name(context, runtime.runtime_id, runtime.metadata)
        labels = _base_labels(context, runtime.runtime_id)
        inspected = self.client.inspect_network(network_name)
        _require_owned(inspected, labels, resource_name=network_name)
        if inspected is None:
            self.client.create_network(network_name, labels=labels)
        return ActivityExecutionOutcome.succeeded(
            BoundedEvidence.from_mapping(
                {
                    "docker": {
                        "action": "ensure-network",
                        "network": network_name,
                        "runtime_id": runtime.runtime_id,
                    }
                }
            )
        )

    def _start_node(
        self,
        context: ActivityRealizationContext,
        graph: DeploymentGraph,
        operation: StartNode,
    ) -> ActivityExecutionOutcome:
        node = graph.node(operation.target.node_id)
        if node.kind != "oci-container":
            raise _UnsupportedDockerRealization(
                f"node {node.node_id!r} is not an OCI container product",
                node_id=node.node_id,
            )
        if node.lifecycle.ownership is not ResourceOwnership.OWNED:
            raise _UnsupportedDockerRealization(
                f"node {node.node_id!r} is not owned by operations",
                node_id=node.node_id,
            )
        if node.configuration_artifacts:
            raise _UnsupportedDockerRealization(
                "configuration artifact materialization is not in #872 scope",
                node_id=node.node_id,
            )
        runtime = graph.runtimes[node.runtime_id]
        if runtime.kind is not RuntimeKind.DOCKER:
            raise _UnsupportedDockerRealization(
                f"node {node.node_id!r} is not assigned to Docker runtime",
                node_id=node.node_id,
            )
        product = _registered_product_for_node(context, node.metadata)
        image = product.descriptor_document.product.image.execution_reference
        _require_digest_image(image)
        metadata_image = node.metadata.get("oci_image")
        if metadata_image != image:
            raise _DockerOwnershipConflict(
                "node image material does not match registered product",
                node_id=node.node_id,
            )
        environment = {
            **node.non_secret_environment(),
            **self._secret_environment(node.secret_deliveries),
        }
        network_name = _network_name(context, runtime.runtime_id, runtime.metadata)
        labels = {
            **_base_labels(context, runtime.runtime_id),
            _LABEL_NODE: node.node_id,
            _LABEL_PRODUCT: product.reference.identity.key,
            _LABEL_DESCRIPTOR: product.reference.descriptor_sha256.value,
        }
        container_name = _container_name(
            self.project_name,
            context.request.identity.workspace_id,
            runtime.runtime_id,
            node.node_id,
        )
        inspected = self.client.inspect_container(container_name)
        _require_owned(inspected, labels, resource_name=container_name)
        if inspected is not None and inspected.running:
            return ActivityExecutionOutcome.succeeded(
                _container_evidence("start-container", container_name, image, reused=True),
                observations=(
                    _process_started_observation(context, node.node_id, container_name),
                ),
            )
        if inspected is not None:
            self.client.start_container(container_name)
            return ActivityExecutionOutcome.succeeded(
                _container_evidence("start-container", container_name, image, reused=True),
                observations=(
                    _process_started_observation(context, node.node_id, container_name),
                ),
            )
        mounts = self._retained_mounts(context, runtime.runtime_id, node, product)
        self.client.pull_image(image)
        self.client.run_container(
            name=container_name,
            image=image,
            network=network_name,
            environment=environment,
            labels=labels,
            network_aliases=_network_aliases(node),
            mounts=mounts,
        )
        return ActivityExecutionOutcome.succeeded(
            _container_evidence("start-container", container_name, image, reused=False),
            observations=(
                _process_started_observation(context, node.node_id, container_name),
            ),
        )

    def _wait_for_healthy(
        self,
        context: ActivityRealizationContext,
        graph: DeploymentGraph,
        operation: WaitForHealthy,
    ) -> ActivityExecutionOutcome:
        node = graph.node(operation.target.node_id)
        checks = node.block_spec.verification.checks
        if not checks:
            raise _UnsupportedDockerRealization(
                "node has no verification contract for health readiness",
                node_id=node.node_id,
            )
        results: list[tuple[str, DockerHealthCheckResult]] = []
        for check in checks:
            try:
                endpoint = node.endpoint(check.provider_socket)
            except KeyError as error:
                raise _UnsupportedDockerRealization(
                    "verification references a missing node endpoint",
                    node_id=node.node_id,
                    provider_socket=check.provider_socket,
                ) from error
            match check:
                case HttpCheck():
                    result = self.health.check_http(endpoint.url, check)
                case PostgresQueryCheck():
                    result = self.health.check_postgres(endpoint.url, check)
                case _:
                    result = DockerHealthCheckResult.unsupported(
                        f"verification check {type(check).__name__} is not supported by Docker ACTIVITY"
                    )
            if result.unsupported_reason is not None:
                raise _UnsupportedDockerRealization(
                    result.unsupported_reason,
                    node_id=node.node_id,
                    check_id=check.check_id,
                )
            results.append((check.check_id, result))
        failures = tuple(
            check_id
            for check_id, result in results
            if result.outcome is not VerificationOutcome.PASSED
        )
        if failures:
            return ActivityExecutionOutcome.failed(
                FailureEvidence(
                    FailureCategory.TERMINAL,
                    "docker.health-check-failed",
                    "one or more Docker-local health checks failed",
                    BoundedEvidence.from_mapping({"check_ids": list(failures)}),
                )
            )
        return ActivityExecutionOutcome.succeeded(
            BoundedEvidence.from_mapping(
                {
                    "docker": {
                        "action": "wait-healthy",
                        "node_id": node.node_id,
                        "checks": [check_id for check_id, _ in results],
                    }
                }
            ),
            observations=(
                _healthy_observation(
                    context,
                    node.node_id,
                    tuple(check_id for check_id, _ in results),
                ),
            ),
        )

    def _reconcile_node(
        self,
        context: ActivityRealizationContext,
        graph: DeploymentGraph,
        operation: ReconcileNode,
    ) -> ActivityExecutionOutcome:
        node = graph.node(operation.target.node_id)
        runtime = graph.runtimes[node.runtime_id]
        container_name = _container_name(
            self.project_name,
            context.request.identity.workspace_id,
            runtime.runtime_id,
            node.node_id,
        )
        inspected = self.client.inspect_container(container_name)
        if inspected is not None:
            labels = _container_labels(context, runtime.runtime_id, node, graph, self.project_name)
            _require_owned(inspected, labels, resource_name=container_name)
            if inspected.running:
                self.client.stop_container(container_name)
            self.client.remove_container(container_name)
        started = self._start_node(context, graph, StartNode(operation.target))
        if started.kind is not EffectResultKind.SUCCEEDED:
            return started
        return ActivityExecutionOutcome.succeeded(
            BoundedEvidence.from_mapping(
                {
                    "docker": {
                        "action": "reconcile-container",
                        "container": container_name,
                        "node_id": node.node_id,
                    }
                }
            ),
            observations=started.observations,
        )

    def _stop_node(
        self,
        context: ActivityRealizationContext,
        graph: DeploymentGraph,
        operation: StopNode,
    ) -> ActivityExecutionOutcome:
        node = graph.node(operation.target.node_id)
        runtime = graph.runtimes[node.runtime_id]
        labels = _container_labels(context, runtime.runtime_id, node, graph, self.project_name)
        container_name = _container_name(
            self.project_name,
            context.request.identity.workspace_id,
            runtime.runtime_id,
            node.node_id,
        )
        inspected = self.client.inspect_container(container_name)
        _require_owned(inspected, labels, resource_name=container_name)
        if inspected is not None and inspected.running:
            self.client.stop_container(container_name)
        return ActivityExecutionOutcome.succeeded(
            BoundedEvidence.from_mapping(
                {
                    "docker": {
                        "action": "stop-container",
                        "container": container_name,
                        "present": inspected is not None,
                    }
                }
            )
        )

    def _remove_node_resource(
        self,
        context: ActivityRealizationContext,
        graph: DeploymentGraph,
        operation: RemoveNodeResource,
    ) -> ActivityExecutionOutcome:
        node = graph.node(operation.target.node_id)
        runtime = graph.runtimes[node.runtime_id]
        labels = _container_labels(context, runtime.runtime_id, node, graph, self.project_name)
        container_name = _container_name(
            self.project_name,
            context.request.identity.workspace_id,
            runtime.runtime_id,
            node.node_id,
        )
        inspected = self.client.inspect_container(container_name)
        _require_owned(inspected, labels, resource_name=container_name)
        if inspected is not None:
            self.client.remove_container(container_name)
        return ActivityExecutionOutcome.succeeded(
            BoundedEvidence.from_mapping(
                {
                    "docker": {
                        "action": "remove-container",
                        "container": container_name,
                        "present": inspected is not None,
                    }
                }
            )
        )

    def _stop_runtime(
        self,
        context: ActivityRealizationContext,
        graph: DeploymentGraph,
        operation: StopRuntime,
    ) -> ActivityExecutionOutcome:
        runtime = graph.runtimes[operation.target.runtime_id]
        network_name = _network_name(context, runtime.runtime_id, runtime.metadata)
        inspected = self.client.inspect_network(network_name)
        _require_owned(
            inspected,
            _base_labels(context, runtime.runtime_id),
            resource_name=network_name,
        )
        return ActivityExecutionOutcome.succeeded(
            BoundedEvidence.from_mapping(
                {
                    "docker": {
                        "action": "stop-runtime",
                        "network": network_name,
                        "present": inspected is not None,
                    }
                }
            )
        )

    def _remove_runtime_resource(
        self,
        context: ActivityRealizationContext,
        graph: DeploymentGraph,
        operation: RemoveRuntimeResource,
    ) -> ActivityExecutionOutcome:
        runtime = graph.runtimes[operation.target.runtime_id]
        network_name = _network_name(context, runtime.runtime_id, runtime.metadata)
        inspected = self.client.inspect_network(network_name)
        _require_owned(
            inspected,
            _base_labels(context, runtime.runtime_id),
            resource_name=network_name,
        )
        if inspected is not None:
            self.client.remove_network(network_name)
        return ActivityExecutionOutcome.succeeded(
            BoundedEvidence.from_mapping(
                {
                    "docker": {
                        "action": "remove-runtime-resource",
                        "network": network_name,
                        "present": inspected is not None,
                    }
                }
            )
        )

    def _secret_environment(self, deliveries: tuple[object, ...]) -> dict[str, str]:
        environment: dict[str, str] = {}
        for delivery in deliveries:
            match delivery:
                case SecretEnvironmentDelivery(
                    environment_name=name,
                    reference=reference,
                ):
                    if self.secret_resolver is None:
                        raise _UnsupportedDockerRealization(
                            "secret environment delivery requires a runtime resolver"
                        )
                    environment[name] = require_resolved_secret(
                        self.secret_resolver,
                        reference,
                    ).reveal()
                case SecretReferenceEnvironmentDelivery(
                    environment_name=name,
                    reference=reference,
                ):
                    environment[name] = reference.reference_id
                case _:
                    raise _UnsupportedDockerRealization(
                        "secret file delivery materialization is not in ACTIVITY scope"
                    )
        return environment

    def _retained_mounts(
        self,
        context: ActivityRealizationContext,
        runtime_id: str,
        node,
        product,
    ) -> dict[str, str]:
        if not node.lifecycle.data:
            return {}
        mounts_by_resource = {
            value.resource_id: value
            for value in product.descriptor_document.product.runtime_contract.retained_data_mounts
        }
        mount_targets: dict[str, str] = {}
        for resource in node.lifecycle.data:
            retained_mount = mounts_by_resource.get(resource.resource_id)
            if retained_mount is None:
                raise _UnsupportedDockerRealization(
                    "retained data resource lacks product mount material",
                    node_id=node.node_id,
                    resource_id=resource.resource_id,
                )
            volume_name = _retained_volume_name(
                self.project_name,
                context.request.identity.workspace_id,
                runtime_id,
                node.node_id,
                retained_mount,
            )
            labels = {
                **_base_labels(context, runtime_id),
                _LABEL_NODE: node.node_id,
                _LABEL_PRODUCT: product.reference.identity.key,
                _LABEL_DESCRIPTOR: product.reference.descriptor_sha256.value,
                _LABEL_DATA_RESOURCE: resource.resource_id,
            }
            inspected = self.client.inspect_volume(volume_name)
            _require_owned(inspected, labels, resource_name=volume_name)
            if inspected is None:
                self.client.create_volume(volume_name, labels=labels)
            mount_targets[volume_name] = retained_mount.target_path
        return mount_targets


class _UnsupportedDockerRealization(ValueError):
    def __init__(self, message: str, **details: str) -> None:
        super().__init__(message)
        self.details = BoundedEvidence.from_mapping(details)


class _DockerOwnershipConflict(ValueError):
    def __init__(self, message: str, **details: str) -> None:
        super().__init__(message)
        self.details = BoundedEvidence.from_mapping(details)


def _desired_graph(context: ActivityRealizationContext) -> DeploymentGraph:
    return DEFAULT_GRAPH_CODEC.decode(context.desired_graph.graph_descriptor)


def _base_graph(context: ActivityRealizationContext) -> DeploymentGraph:
    return DEFAULT_GRAPH_CODEC.decode(context.base_graph.graph_descriptor)


def _registered_product_for_node(
    context: ActivityRealizationContext,
    metadata: Mapping[str, object],
):
    identity = _product_identity(metadata.get("product_identity"))
    digest = _descriptor_digest(metadata.get("product_descriptor_digest"))
    reference = ProductReference(identity, digest)
    for product in context.registered_products:
        if product.reference == reference:
            return product
    raise _DockerOwnershipConflict(
        "node product reference is not registered for this workspace",
        product_identity=identity.key,
    )


def _product_identity(value: object) -> ProductIdentity:
    if not isinstance(value, str):
        raise _DockerOwnershipConflict("node product identity metadata is malformed")
    parts = value.split("/")
    if len(parts) != 3:
        raise _DockerOwnershipConflict("node product identity metadata is malformed")
    try:
        revision = int(parts[2])
    except ValueError as error:
        raise _DockerOwnershipConflict(
            "node product identity metadata is malformed"
        ) from error
    return ProductIdentity(parts[0], parts[1], revision)


def _descriptor_digest(value: object) -> ProductDescriptorDigest:
    if not isinstance(value, str):
        raise _DockerOwnershipConflict("node product descriptor metadata is malformed")
    return ProductDescriptorDigest(value)


def _require_digest_image(image: str) -> None:
    if "@sha256:" not in image:
        raise _UnsupportedDockerRealization(
            "Docker realization requires a digest-pinned OCI image"
        )


def _network_name(
    context: ActivityRealizationContext,
    runtime_id: str,
    metadata: Mapping[str, str],
) -> str:
    value = metadata.get("network_name")
    if isinstance(value, str) and value:
        return value
    return _resource_name(
        "network",
        context.request.identity.workspace_id,
        runtime_id,
        context.plan_record.plan_id,
    )


def _container_name(
    project_name: str,
    workspace_id: str,
    runtime_id: str,
    node_id: str,
) -> str:
    return _clean(f"{project_name}-{workspace_id}-{runtime_id}-{node_id}")


def _retained_volume_name(
    project_name: str,
    workspace_id: str,
    runtime_id: str,
    node_id: str,
    mount: RetainedDataMount,
) -> str:
    return _clean(
        f"{project_name}-{workspace_id}-{runtime_id}-{node_id}-{mount.resource_id}"
    )


def _resource_name(*parts: str) -> str:
    return _clean("-".join(parts))


def _clean(value: str) -> str:
    return _IDENTITY.sub("-", value).strip("-").lower()


def _base_labels(
    context: ActivityRealizationContext,
    runtime_id: str,
) -> dict[str, str]:
    return {
        _LABEL_OWNER: _LABEL_OWNER_VALUE,
        _LABEL_WORKSPACE: context.request.identity.workspace_id,
        _LABEL_PLAN: context.plan_record.plan_id,
        _LABEL_GRAPH: context.plan_record.desired_graph_id,
        _LABEL_RUNTIME: runtime_id,
    }


def _require_owned(
    inspected: DockerResourceInspection | None,
    expected: Mapping[str, str],
    *,
    resource_name: str,
) -> None:
    if inspected is None:
        return
    for key, value in expected.items():
        if inspected.labels.get(key) != value:
            raise _DockerOwnershipConflict(
                f"Docker resource {resource_name!r} is not owned compatible",
                resource_name=resource_name,
                label=key,
            )


def _container_labels(
    context: ActivityRealizationContext,
    runtime_id: str,
    node,
    graph: DeploymentGraph,
    project_name: str,
) -> dict[str, str]:
    del graph, project_name
    product = _registered_product_for_node(context, node.metadata)
    return {
        **_base_labels(context, runtime_id),
        _LABEL_NODE: node.node_id,
        _LABEL_PRODUCT: product.reference.identity.key,
        _LABEL_DESCRIPTOR: product.reference.descriptor_sha256.value,
    }


def _network_aliases(node) -> tuple[str, ...]:
    return tuple(
        _clean(f"{node.node_id}-{socket.name}")
        for socket in node.sockets.providers
    )


def _container_evidence(
    action: str,
    container_name: str,
    image: str,
    *,
    reused: bool,
) -> BoundedEvidence:
    return BoundedEvidence.from_mapping(
        {
            "docker": {
                "action": action,
                "container": container_name,
                "image": image,
                "reused": reused,
            }
        }
    )


def _process_started_observation(
    context: ActivityRealizationContext,
    node_id: str,
    container_name: str,
) -> ObservationRecord:
    return ObservationRecord(
        observation_id=f"{context.intent_event.event_id}:process-started",
        workspace_id=context.request.identity.workspace_id,
        subject_id=node_id,
        status=ObservationStatus.PROCESS_STARTED,
        observed_at=context.intent_event.occurred_at,
        evidence=BoundedEvidence.from_mapping(
            {
                "docker": {
                    "container": container_name,
                    "action": "start-container",
                }
            }
        ),
        graph_id=context.plan_record.desired_graph_id,
        probe_kind=ProbeKind.PROCESS,
        probe_outcome=ProbeOutcome.PROCESS_RUNNING,
    )


def _healthy_observation(
    context: ActivityRealizationContext,
    node_id: str,
    check_ids: tuple[str, ...],
) -> ObservationRecord:
    return ObservationRecord(
        observation_id=f"{context.intent_event.event_id}:healthy",
        workspace_id=context.request.identity.workspace_id,
        subject_id=node_id,
        status=ObservationStatus.HEALTHY,
        observed_at=context.intent_event.occurred_at,
        evidence=BoundedEvidence.from_mapping(
            {
                "docker": {
                    "action": "wait-healthy",
                    "check_ids": list(check_ids),
                }
            }
        ),
        graph_id=context.plan_record.desired_graph_id,
        probe_kind=ProbeKind.APPLICATION_HEALTH,
        probe_outcome=ProbeOutcome.HEALTHY,
    )


def _secret_failure_code(code: SecretResolutionCode) -> str:
    return f"docker.secret-{code.value}"
