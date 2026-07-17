"""Docker runtime planning and execution values."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from enum import StrEnum
from dataclasses import dataclass, field, replace
from typing import Mapping, Protocol, TypeAlias

from control_plane_kit.effects import (
    EffectCapability,
    EffectFailed,
    EffectObservation,
    EffectResult,
    EffectSucceeded,
    EffectUnsupported,
    EnvironmentBindingMaterial,
    LiteralMaterialValue,
    MaterializedEffectRequest,
    NodeMaterial,
    ObservationKind,
    RuntimeMaterial,
    SecretReferenceMaterialValue,
)
from control_plane_kit.execution import (
    BoundedEvidence,
    FailureCategory,
    FailureEvidence,
    ObservationStatus,
)
from control_plane_kit.planning import StartNode, StartRuntime, StopNode

from control_plane_kit.topology.graph import DeploymentGraph, Node
from control_plane_kit.runtimes import CleanupPolicy, RuntimeActivity, RuntimeNodeState, RuntimePlan, RuntimeState
from control_plane_kit.types import RuntimeKind


class UnsupportedDockerRuntimeFeature(ValueError):
    """Raised when a graph cannot be realized by the Docker interpreter yet."""


class DockerClient(Protocol):
    """Small Docker capability surface used by the runtime executor."""

    def ensure_network(self, name: str, *, timeout_seconds: int = 30) -> None:
        """Create the network if needed."""

    def start_container(
        self,
        *,
        name: str,
        image: str,
        network: str,
        environment: Mapping[str, str],
        command: tuple[str, ...],
        timeout_seconds: int = 30,
    ) -> None:
        """Start a detached container."""

    def stop_container(self, name: str, *, timeout_seconds: int = 30) -> None:
        """Stop a container if it is running."""

    def remove_container(self, name: str, *, timeout_seconds: int = 30) -> None:
        """Remove a container if it exists."""

    def remove_network(self, name: str, *, timeout_seconds: int = 30) -> None:
        """Remove a network if it exists."""

    def inspect_network(self, name: str, *, timeout_seconds: int = 30) -> "DockerResourceInspection | None": ...

    def create_network(self, name: str, labels: Mapping[str, str], *, timeout_seconds: int = 30) -> None: ...

    def inspect_container(self, name: str, *, timeout_seconds: int = 30) -> "DockerResourceInspection | None": ...

    def run_container(self, *, name: str, image: str, network: str, environment: Mapping[str, str], command: tuple[str, ...], labels: Mapping[str, str], timeout_seconds: int = 30) -> None: ...

    def start_existing_container(self, resource_id: str, *, timeout_seconds: int = 30) -> None: ...

    def stop_owned_container(
        self,
        name: str,
        ownership: "DockerOwnership",
        *,
        timeout_seconds: int = 30,
    ) -> None: ...


@dataclass(frozen=True)
class DockerCliClient:
    """Docker client backed by the local `docker` CLI."""

    docker: str = "docker"

    def inspect_network(self, name: str, *, timeout_seconds: int = 30) -> "DockerResourceInspection | None":
        result = self._capture(
            "network",
            "inspect",
            "--format",
            "{{.Id}}\t{{.Name}}\t{{json .Labels}}",
            name,
            check=False,
            timeout_seconds=timeout_seconds,
        )
        if result.returncode != 0:
            return None
        parts = result.stdout.strip().split("\t", 2)
        if len(parts) != 3:
            raise UnsupportedDockerRuntimeFeature("Docker network inspection was malformed")
        return DockerResourceInspection(
            DockerResourceKind.NETWORK,
            parts[0],
            parts[1],
            False,
            None,
            _parse_labels(parts[2]),
        )

    def create_network(
        self,
        name: str,
        labels: Mapping[str, str],
        *,
        timeout_seconds: int = 30,
    ) -> None:
        args = ["network", "create"]
        for key, value in sorted(labels.items()):
            args.extend(("--label", f"{key}={value}"))
        args.append(name)
        self._run(*args, timeout_seconds=timeout_seconds)

    def inspect_container(self, name: str, *, timeout_seconds: int = 30) -> "DockerResourceInspection | None":
        result = self._capture(
            "container",
            "inspect",
            "--format",
            "{{.Id}}\t{{.Name}}\t{{.State.Running}}\t{{.Config.Image}}\t{{json .Config.Labels}}",
            name,
            check=False,
            timeout_seconds=timeout_seconds,
        )
        if result.returncode != 0:
            return None
        parts = result.stdout.strip().split("\t", 4)
        if len(parts) != 5 or parts[2] not in ("true", "false"):
            raise UnsupportedDockerRuntimeFeature("Docker container inspection was malformed")
        return DockerResourceInspection(
            DockerResourceKind.CONTAINER,
            parts[0],
            parts[1].removeprefix("/"),
            parts[2] == "true",
            parts[3],
            _parse_labels(parts[4]),
        )

    def run_container(
        self,
        *,
        name: str,
        image: str,
        network: str,
        environment: Mapping[str, str],
        command: tuple[str, ...],
        labels: Mapping[str, str],
        timeout_seconds: int = 30,
    ) -> None:
        args = ["run", "-d", "--name", name, "--network", network]
        process_environment = os.environ.copy()
        process_environment.update(environment)
        for key in sorted(environment):
            args.extend(("-e", key))
        for key, value in sorted(labels.items()):
            args.extend(("--label", f"{key}={value}"))
        args.append(image)
        args.extend(command)
        self._run(*args, timeout_seconds=timeout_seconds, environment=process_environment)

    def start_existing_container(self, resource_id: str, *, timeout_seconds: int = 30) -> None:
        self._run("start", resource_id, timeout_seconds=timeout_seconds)

    def stop_owned_container(
        self,
        name: str,
        ownership: "DockerOwnership",
        *,
        timeout_seconds: int = 30,
    ) -> None:
        inspected = self.inspect_container(name, timeout_seconds=timeout_seconds)
        disposition = classify_docker_resource(inspected, ownership)
        if disposition is DockerResourceDisposition.ABSENT:
            return
        if disposition is not DockerResourceDisposition.OWNED_COMPATIBLE:
            raise DockerOwnershipConflict(disposition)
        if inspected is not None and inspected.running:
            self._run("stop", inspected.resource_id, timeout_seconds=timeout_seconds)

    def ensure_network(self, name: str, *, timeout_seconds: int = 30) -> None:
        expected = DockerOwnership.legacy_network(name)
        inspected = self.inspect_network(name, timeout_seconds=timeout_seconds)
        disposition = classify_docker_resource(inspected, expected)
        if disposition is DockerResourceDisposition.ABSENT:
            self.create_network(name, expected.labels(), timeout_seconds=timeout_seconds)
        elif disposition is not DockerResourceDisposition.OWNED_COMPATIBLE:
            raise UnsupportedDockerRuntimeFeature("Docker network ownership conflict")

    def start_container(
        self,
        *,
        name: str,
        image: str,
        network: str,
        environment: Mapping[str, str],
        command: tuple[str, ...],
        timeout_seconds: int = 30,
    ) -> None:
        ownership = DockerOwnership.legacy_container(name, image)
        inspected = self.inspect_container(name, timeout_seconds=timeout_seconds)
        disposition = classify_docker_resource(inspected, ownership)
        if disposition is DockerResourceDisposition.ABSENT:
            self.run_container(
                name=name,
                image=image,
                network=network,
                environment=environment,
                command=command,
                labels=ownership.labels(),
                timeout_seconds=timeout_seconds,
            )
        elif disposition is DockerResourceDisposition.OWNED_COMPATIBLE:
            if inspected is not None and not inspected.running:
                self.start_existing_container(name, timeout_seconds=timeout_seconds)
        else:
            raise UnsupportedDockerRuntimeFeature("Docker container ownership conflict")

    def stop_container(self, name: str, *, timeout_seconds: int = 30) -> None:
        inspected = self.inspect_container(name, timeout_seconds=timeout_seconds)
        _require_legacy_owned(inspected, DockerOwnership.legacy_container_from_inspection(name, inspected))
        if inspected is not None and inspected.running:
            self._run("stop", name, timeout_seconds=timeout_seconds)

    def remove_container(self, name: str, *, timeout_seconds: int = 30) -> None:
        inspected = self.inspect_container(name, timeout_seconds=timeout_seconds)
        _require_legacy_owned(inspected, DockerOwnership.legacy_container_from_inspection(name, inspected))
        if inspected is not None:
            self._run("rm", "-f", name, timeout_seconds=timeout_seconds)

    def remove_network(self, name: str, *, timeout_seconds: int = 30) -> None:
        inspected = self.inspect_network(name, timeout_seconds=timeout_seconds)
        _require_legacy_owned(inspected, DockerOwnership.legacy_network(name))
        if inspected is not None:
            self._run("network", "rm", name, timeout_seconds=timeout_seconds)

    def _run(
        self,
        *args: str,
        check: bool = True,
        timeout_seconds: int = 30,
        environment: Mapping[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            (self.docker, *args),
            check=check,
            text=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=timeout_seconds,
            env=environment,
        )

    def _capture(
        self,
        *args: str,
        check: bool = True,
        timeout_seconds: int = 30,
    ) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            (self.docker, *args),
            check=check,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=timeout_seconds,
        )
        if len(result.stdout.encode()) > 65_536:
            raise UnsupportedDockerRuntimeFeature("Docker inspection exceeded its output bound")
        return result


class DockerResourceKind(StrEnum):
    NETWORK = "network"
    CONTAINER = "container"


class DockerResourceDisposition(StrEnum):
    ABSENT = "absent"
    OWNED_COMPATIBLE = "owned-compatible"
    OWNED_CONFLICTING = "owned-conflicting"
    UNOWNED = "unowned"


class DockerOwnershipConflict(ValueError):
    """Raised when a Docker name resolves to incompatible ownership truth."""

    def __init__(self, disposition: DockerResourceDisposition) -> None:
        self.disposition = disposition
        super().__init__(f"Docker resource is {disposition.value}")


_LABEL_PREFIX = "io.control-plane-kit"


@dataclass(frozen=True)
class DockerOwnership:
    workspace_id: str
    runtime_id: str
    resource_kind: DockerResourceKind
    intent_fingerprint: str
    node_id: str | None = None
    effect_id: str | None = None

    def labels(self) -> dict[str, str]:
        values = {
            f"{_LABEL_PREFIX}.package": "control-plane-kit",
            f"{_LABEL_PREFIX}.workspace": self.workspace_id,
            f"{_LABEL_PREFIX}.runtime": self.runtime_id,
            f"{_LABEL_PREFIX}.resource": self.resource_kind.value,
            f"{_LABEL_PREFIX}.intent": self.intent_fingerprint,
        }
        if self.node_id is not None:
            values[f"{_LABEL_PREFIX}.node"] = self.node_id
        if self.effect_id is not None:
            values[f"{_LABEL_PREFIX}.created-by"] = self.effect_id
        return values

    @classmethod
    def legacy_network(cls, name: str) -> "DockerOwnership":
        return cls("legacy", name, DockerResourceKind.NETWORK, _fingerprint({"name": name}))

    @classmethod
    def legacy_container(cls, name: str, image: str) -> "DockerOwnership":
        return cls("legacy", "legacy", DockerResourceKind.CONTAINER, _fingerprint({"name": name, "image": image}), name)

    @classmethod
    def legacy_container_from_inspection(
        cls,
        name: str,
        inspected: "DockerResourceInspection | None",
    ) -> "DockerOwnership":
        image = inspected.image if inspected is not None and inspected.image else ""
        return cls.legacy_container(name, image)


@dataclass(frozen=True)
class DockerResourceInspection:
    kind: DockerResourceKind
    resource_id: str
    name: str
    running: bool
    image: str | None
    labels: Mapping[str, str]


def classify_docker_resource(
    inspected: DockerResourceInspection | None,
    expected: DockerOwnership,
) -> DockerResourceDisposition:
    if inspected is None:
        return DockerResourceDisposition.ABSENT
    if inspected.kind is not expected.resource_kind:
        return DockerResourceDisposition.OWNED_CONFLICTING
    labels = inspected.labels
    if labels.get(f"{_LABEL_PREFIX}.package") != "control-plane-kit":
        return DockerResourceDisposition.UNOWNED
    identity_keys = ("workspace", "runtime", "resource")
    if any(
        labels.get(f"{_LABEL_PREFIX}.{key}") != expected.labels()[f"{_LABEL_PREFIX}.{key}"]
        for key in identity_keys
    ):
        return DockerResourceDisposition.OWNED_CONFLICTING
    if expected.node_id is not None and labels.get(f"{_LABEL_PREFIX}.node") != expected.node_id:
        return DockerResourceDisposition.OWNED_CONFLICTING
    if labels.get(f"{_LABEL_PREFIX}.intent") != expected.intent_fingerprint:
        return DockerResourceDisposition.OWNED_CONFLICTING
    return DockerResourceDisposition.OWNED_COMPATIBLE


def _parse_labels(value: str) -> dict[str, str]:
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError) as exc:
        raise UnsupportedDockerRuntimeFeature("Docker labels were malformed") from exc
    if parsed is None:
        return {}
    if not isinstance(parsed, dict) or not all(
        isinstance(key, str) and isinstance(label, str)
        for key, label in parsed.items()
    ):
        raise UnsupportedDockerRuntimeFeature("Docker labels were malformed")
    return parsed


def _fingerprint(value: Mapping[str, object]) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


def _require_legacy_owned(
    inspected: DockerResourceInspection | None,
    expected: DockerOwnership,
) -> None:
    disposition = classify_docker_resource(inspected, expected)
    if disposition not in (
        DockerResourceDisposition.ABSENT,
        DockerResourceDisposition.OWNED_COMPATIBLE,
    ):
        raise DockerOwnershipConflict(disposition)


class DockerSecretResolver(Protocol):
    """Resolve one opaque environment reference only at Docker dispatch."""

    def resolve(self, reference_id: str) -> str: ...


@dataclass(frozen=True)
class EnsureDockerNetworkEffect:
    runtime_id: str
    network_name: str
    ownership: DockerOwnership


@dataclass(frozen=True)
class StartDockerNodeEffect:
    runtime_id: str
    node_id: str
    container_name: str
    network_name: str
    image: str
    command: tuple[str, ...]
    environment: tuple[EnvironmentBindingMaterial, ...]
    ownership: DockerOwnership


@dataclass(frozen=True)
class StopDockerNodeEffect:
    runtime_id: str
    node_id: str
    container_name: str
    ownership: DockerOwnership


DockerEffectCommand: TypeAlias = (
    EnsureDockerNetworkEffect | StartDockerNodeEffect | StopDockerNodeEffect
)


@dataclass(frozen=True)
class DockerEffectInterpreter:
    """Execute one graph-pinned Docker lifecycle activity at a time."""

    project_name: str = "control-plane-kit"
    client: DockerClient = field(default_factory=DockerCliClient)
    secrets: DockerSecretResolver | None = None

    @property
    def capabilities(self) -> frozenset[EffectCapability]:
        return frozenset(
            {EffectCapability.NODE_LIFECYCLE, EffectCapability.RUNTIME_LIFECYCLE}
        )

    def execute(self, request: MaterializedEffectRequest) -> EffectResult:
        try:
            command = plan_docker_effect(request, project_name=self.project_name)
            timeout = request.timeout.total_seconds
            match command:
                case EnsureDockerNetworkEffect(network_name=name, ownership=ownership):
                    disposition = _ensure_owned_network(
                        self.client,
                        name,
                        ownership,
                        timeout_seconds=timeout,
                    )
                    return EffectSucceeded(
                        request.identity,
                        BoundedEvidence.from_mapping(
                            {
                                "operation": "ensure-network",
                                "runtime_id": command.runtime_id,
                                "disposition": disposition.value,
                                "ownership": _ownership_evidence(ownership),
                            }
                        ),
                    )
                case StartDockerNodeEffect():
                    disposition = _start_owned_container(
                        self.client,
                        command,
                        _resolve_environment(command.environment, self.secrets),
                        timeout_seconds=timeout,
                    )
                    return EffectSucceeded(
                        request.identity,
                        BoundedEvidence.from_mapping(
                            {
                                "operation": "start-container",
                                "node_id": command.node_id,
                                "disposition": disposition.value,
                                "ownership": _ownership_evidence(command.ownership),
                            }
                        ),
                        (
                            EffectObservation(
                                command.node_id,
                                ObservationKind.STATUS,
                                ObservationStatus.PROCESS_STARTED,
                                BoundedEvidence.from_mapping(
                                    {"runtime_id": command.runtime_id}
                                ),
                            ),
                        ),
                    )
                case StopDockerNodeEffect(container_name=name, ownership=ownership):
                    inspected = self.client.inspect_container(
                        name,
                        timeout_seconds=timeout,
                    )
                    disposition = classify_docker_resource(inspected, ownership)
                    if disposition not in (
                        DockerResourceDisposition.ABSENT,
                        DockerResourceDisposition.OWNED_COMPATIBLE,
                    ):
                        raise DockerOwnershipConflict(disposition)
                    self.client.stop_owned_container(
                        name,
                        ownership,
                        timeout_seconds=timeout,
                    )
                    return EffectSucceeded(
                        request.identity,
                        BoundedEvidence.from_mapping(
                            {
                                "operation": "stop-container",
                                "node_id": command.node_id,
                                "disposition": disposition.value,
                                "ownership": _ownership_evidence(ownership),
                            }
                        ),
                    )
        except UnsupportedDockerRuntimeFeature:
            return EffectUnsupported(request.identity, request.capability)
        except DockerOwnershipConflict as error:
            return EffectFailed(
                request.identity,
                FailureEvidence(
                    FailureCategory.TERMINAL,
                    "docker.ownership-conflict",
                    f"Docker mutation refused because the resource is {error.disposition.value}.",
                ),
            )
        except subprocess.TimeoutExpired:
            return EffectFailed(
                request.identity,
                FailureEvidence(
                    FailureCategory.UNCERTAIN,
                    "docker.timeout",
                    "The Docker operation timed out without trustworthy completion evidence.",
                ),
            )
        except (subprocess.SubprocessError, OSError, ValueError, KeyError, TypeError):
            return EffectFailed(
                request.identity,
                FailureEvidence(
                    FailureCategory.TERMINAL,
                    "docker.operation-failed",
                    "The Docker operation failed without publishable command output.",
                ),
            )


def plan_docker_effect(
    request: MaterializedEffectRequest,
    *,
    project_name: str,
) -> DockerEffectCommand:
    """Interpret one canonical activity as a pure narrow Docker command."""

    match request.action, request.material:
        case StartRuntime(), RuntimeMaterial() as runtime:
            _require_docker(runtime)
            network_name = runtime.network_name or f"{runtime.runtime_id}-network"
            return EnsureDockerNetworkEffect(
                runtime.runtime_id,
                network_name,
                DockerOwnership(
                    request.graphs.workspace_id,
                    runtime.runtime_id,
                    DockerResourceKind.NETWORK,
                    _fingerprint(
                        {
                            "runtime_id": runtime.runtime_id,
                            "network_name": network_name,
                        }
                    ),
                    effect_id=request.identity.idempotency_key,
                ),
            )
        case StartNode(), NodeMaterial() as node:
            _require_docker(node.runtime)
            if not node.implementation.image:
                raise UnsupportedDockerRuntimeFeature(
                    "Docker node material requires an image"
                )
            return StartDockerNodeEffect(
                node.runtime.runtime_id,
                node.node_id,
                _container_name(project_name, node.runtime.runtime_id, node.node_id),
                node.runtime.network_name or f"{node.runtime.runtime_id}-network",
                node.implementation.image,
                node.implementation.command,
                node.implementation.environment,
                _node_ownership(request, node),
            )
        case StopNode(), NodeMaterial() as node:
            _require_docker(node.runtime)
            return StopDockerNodeEffect(
                node.runtime.runtime_id,
                node.node_id,
                _container_name(project_name, node.runtime.runtime_id, node.node_id),
                _node_ownership(request, node),
            )
    raise UnsupportedDockerRuntimeFeature(
        "activity has no safe narrow Docker lifecycle interpretation"
    )


def _require_docker(runtime: RuntimeMaterial) -> None:
    if runtime.kind is not RuntimeKind.DOCKER:
        raise UnsupportedDockerRuntimeFeature("effect material is not a Docker runtime")


def _resolve_environment(
    bindings: tuple[EnvironmentBindingMaterial, ...],
    resolver: DockerSecretResolver | None,
) -> dict[str, str]:
    values = {}
    for binding in bindings:
        match binding.value:
            case LiteralMaterialValue(value=value):
                values[binding.name] = value
            case SecretReferenceMaterialValue(reference_id=reference):
                if resolver is None:
                    raise UnsupportedDockerRuntimeFeature(
                        "Docker environment secret reference has no resolver"
                    )
                values[binding.name] = resolver.resolve(reference)
    return values


def _node_ownership(
    request: MaterializedEffectRequest,
    node: NodeMaterial,
) -> DockerOwnership:
    material_environment = []
    for binding in node.implementation.environment:
        match binding.value:
            case LiteralMaterialValue(value=value):
                material_environment.append((binding.name, "literal", value))
            case SecretReferenceMaterialValue(reference_id=reference):
                material_environment.append((binding.name, "secret-reference", reference))
    return DockerOwnership(
        request.graphs.workspace_id,
        node.runtime.runtime_id,
        DockerResourceKind.CONTAINER,
        _fingerprint(
            {
                "node_id": node.node_id,
                "runtime_id": node.runtime.runtime_id,
                "network_name": node.runtime.network_name,
                "image": node.implementation.image,
                "command": list(node.implementation.command),
                "environment": material_environment,
            }
        ),
        node_id=node.node_id,
        effect_id=request.identity.idempotency_key,
    )


def _ownership_evidence(ownership: DockerOwnership) -> dict[str, object]:
    return {
        "workspace_id": ownership.workspace_id,
        "runtime_id": ownership.runtime_id,
        "resource_kind": ownership.resource_kind.value,
        "node_id": ownership.node_id,
        "intent_fingerprint": ownership.intent_fingerprint,
    }


def _ensure_owned_network(
    client: DockerClient,
    name: str,
    ownership: DockerOwnership,
    *,
    timeout_seconds: int,
) -> DockerResourceDisposition:
    inspected = client.inspect_network(name, timeout_seconds=timeout_seconds)
    disposition = classify_docker_resource(inspected, ownership)
    if disposition is DockerResourceDisposition.OWNED_COMPATIBLE:
        return disposition
    if disposition is not DockerResourceDisposition.ABSENT:
        raise DockerOwnershipConflict(disposition)
    try:
        client.create_network(
            name,
            ownership.labels(),
            timeout_seconds=timeout_seconds,
        )
    except subprocess.SubprocessError:
        raced = client.inspect_network(name, timeout_seconds=timeout_seconds)
        raced_disposition = classify_docker_resource(raced, ownership)
        if raced_disposition is not DockerResourceDisposition.OWNED_COMPATIBLE:
            raise
        return raced_disposition
    return disposition


def _start_owned_container(
    client: DockerClient,
    command: StartDockerNodeEffect,
    environment: Mapping[str, str],
    *,
    timeout_seconds: int,
) -> DockerResourceDisposition:
    inspected = client.inspect_container(
        command.container_name,
        timeout_seconds=timeout_seconds,
    )
    disposition = classify_docker_resource(inspected, command.ownership)
    if disposition is DockerResourceDisposition.OWNED_COMPATIBLE:
        if inspected is not None and not inspected.running:
            client.start_existing_container(
                inspected.resource_id,
                timeout_seconds=timeout_seconds,
            )
        return disposition
    if disposition is not DockerResourceDisposition.ABSENT:
        raise DockerOwnershipConflict(disposition)
    try:
        client.run_container(
            name=command.container_name,
            image=command.image,
            network=command.network_name,
            environment=environment,
            command=command.command,
            labels=command.ownership.labels(),
            timeout_seconds=timeout_seconds,
        )
    except subprocess.SubprocessError:
        raced = client.inspect_container(
            command.container_name,
            timeout_seconds=timeout_seconds,
        )
        raced_disposition = classify_docker_resource(raced, command.ownership)
        if raced_disposition is not DockerResourceDisposition.OWNED_COMPATIBLE:
            raise
        return raced_disposition
    return disposition


@dataclass(frozen=True)
class EnsureDockerNetwork:
    """Plan activity for making the runtime network available."""

    runtime_id: str
    network_name: str

    def descriptor(self) -> dict[str, object]:
        return {
            "type": "ensure-docker-network",
            "runtime_id": self.runtime_id,
            "network_name": self.network_name,
        }


@dataclass(frozen=True)
class StartDockerContainer:
    """Plan activity for starting one Docker-backed node."""

    runtime_id: str
    node_id: str
    container_name: str
    image: str
    network_name: str
    environment: Mapping[str, str] = field(default_factory=dict)
    command: tuple[str, ...] = ()
    ports: Mapping[str, int] = field(default_factory=dict)
    metadata: Mapping[str, object] = field(default_factory=dict)

    def descriptor(self) -> dict[str, object]:
        return {
            "type": "start-docker-container",
            "runtime_id": self.runtime_id,
            "node_id": self.node_id,
            "container_name": self.container_name,
            "image": self.image,
            "network_name": self.network_name,
            "environment": _redacted_environment(self.environment),
            "command": list(self.command),
            "ports": dict(sorted(self.ports.items())),
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class StopDockerContainer:
    """Plan activity for stopping one owned Docker container."""

    runtime_id: str
    node_id: str
    container_name: str
    cleanup_policy: CleanupPolicy

    def descriptor(self) -> dict[str, object]:
        return {
            "type": "stop-docker-container",
            "runtime_id": self.runtime_id,
            "node_id": self.node_id,
            "container_name": self.container_name,
            "cleanup_policy": self.cleanup_policy.value,
        }


@dataclass(frozen=True)
class RemoveDockerNetwork:
    """Plan activity for removing an owned Docker network."""

    runtime_id: str
    network_name: str
    cleanup_policy: CleanupPolicy

    def descriptor(self) -> dict[str, object]:
        return {
            "type": "remove-docker-network",
            "runtime_id": self.runtime_id,
            "network_name": self.network_name,
            "cleanup_policy": self.cleanup_policy.value,
        }


@dataclass(frozen=True)
class DockerRuntimeInterpreter:
    """Docker runtime planner/executor for one compiled runtime context."""

    project_name: str = "control-plane-kit"
    cleanup_policy: CleanupPolicy = CleanupPolicy.REMOVE_ON_STOP
    client: DockerClient = field(default_factory=DockerCliClient)

    def plan_start(self, graph: DeploymentGraph, runtime_id: str) -> RuntimePlan:
        runtime = _docker_runtime(graph, runtime_id)
        _reject_cross_runtime_edges(graph, runtime_id)
        network_name = _network_name(runtime_id, runtime.metadata)
        activities: list[RuntimeActivity] = [
            EnsureDockerNetwork(runtime_id=runtime_id, network_name=network_name)
        ]
        for node_id in runtime.children:
            activities.append(_start_activity(graph.node(node_id), runtime_id, network_name, self.project_name))
        return RuntimePlan(runtime_id=runtime_id, action="start", activities=tuple(activities))

    def up(self, graph: DeploymentGraph, runtime_id: str) -> RuntimeState:
        runtime = _docker_runtime(graph, runtime_id)
        plan = self.plan_start(graph, runtime_id)
        network_name = _network_name(runtime_id, runtime.metadata)
        nodes: dict[str, RuntimeNodeState] = {}
        for activity in plan.activities:
            match activity:
                case EnsureDockerNetwork(network_name=name):
                    self.client.ensure_network(name)
                case StartDockerContainer() as start:
                    self.client.start_container(
                        name=start.container_name,
                        image=start.image,
                        network=start.network_name,
                        environment=start.environment,
                        command=start.command,
                    )
                    graph_node = graph.node(start.node_id)
                    nodes[start.node_id] = RuntimeNodeState(
                        node_id=start.node_id,
                        kind=graph_node.kind,
                        runtime_id=runtime_id,
                        healthy=False,
                        environment=graph_node.environment,
                        endpoints=graph_node.endpoints,
                        metadata={
                            "container_name": start.container_name,
                            "image": start.image,
                            "network_name": start.network_name,
                        },
                    )
                case _:
                    raise UnsupportedDockerRuntimeFeature(f"unknown Docker start activity {activity!r}")
        return RuntimeState(
            runtime_id=runtime_id,
            kind=runtime.kind,
            cleanup_policy=self.cleanup_policy,
            nodes=nodes,
            metadata={"network_name": network_name, "interpreter": "docker"},
        )

    def plan_stop(self, state: RuntimeState) -> RuntimePlan:
        activities: list[RuntimeActivity] = []
        for node_id, node in sorted(state.nodes.items()):
            container_name = str(node.metadata.get("container_name", _container_name(self.project_name, state.runtime_id, node_id)))
            activities.append(
                StopDockerContainer(
                    runtime_id=state.runtime_id,
                    node_id=node_id,
                    container_name=container_name,
                    cleanup_policy=state.cleanup_policy,
                )
            )
        network_name = state.metadata.get("network_name")
        if isinstance(network_name, str):
            activities.append(
                RemoveDockerNetwork(
                    runtime_id=state.runtime_id,
                    network_name=network_name,
                    cleanup_policy=state.cleanup_policy,
                )
            )
        return RuntimePlan(runtime_id=state.runtime_id, action="stop", activities=tuple(activities))

    def down(self, state: RuntimeState) -> RuntimeState:
        plan = self.plan_stop(state)
        for activity in plan.activities:
            match activity:
                case StopDockerContainer(container_name=name):
                    self.client.stop_container(name)
                    if state.cleanup_policy is CleanupPolicy.REMOVE_ON_STOP:
                        self.client.remove_container(name)
                case RemoveDockerNetwork(network_name=name):
                    if state.cleanup_policy is CleanupPolicy.REMOVE_ON_STOP:
                        self.client.remove_network(name)
                case _:
                    raise UnsupportedDockerRuntimeFeature(f"unknown Docker stop activity {activity!r}")
        nodes = {} if state.cleanup_policy is CleanupPolicy.REMOVE_ON_STOP else state.nodes
        return replace(state, nodes=nodes, metadata={**state.metadata, "stopped": True})


def _docker_runtime(graph: DeploymentGraph, runtime_id: str):
    try:
        runtime = graph.runtimes[runtime_id]
    except KeyError as exc:
        available = ", ".join(sorted(graph.runtimes)) or "<none>"
        raise KeyError(f"missing runtime {runtime_id!r}; available: {available}") from exc
    if runtime.kind is not RuntimeKind.DOCKER:
        raise UnsupportedDockerRuntimeFeature(
            f"runtime {runtime_id!r} is {runtime.kind.value!r}; Docker interpreter only handles docker runtimes"
        )
    return runtime


def _reject_cross_runtime_edges(graph: DeploymentGraph, runtime_id: str) -> None:
    for edge in graph.edges.values():
        provider = graph.node(edge.provider_role)
        consumer = graph.node(edge.consumer_role)
        crosses_runtime = provider.runtime_id != consumer.runtime_id
        touches_runtime = runtime_id in {provider.runtime_id, consumer.runtime_id}
        if crosses_runtime and touches_runtime:
            raise UnsupportedDockerRuntimeFeature(
                f"Docker runtime {runtime_id!r} cannot realize cross-runtime edge {edge.edge_id!r} yet"
            )


def _start_activity(node: Node, runtime_id: str, network_name: str, project_name: str) -> StartDockerContainer:
    match node.kind:
        case "docker-image" | "docker-postgres":
            image = _required_metadata(node, "image")
            command = tuple(str(part) for part in node.metadata.get("command", ()))
            ports = {
                socket_name: int(endpoint.url.rsplit(":", 1)[1].split("/", 1)[0])
                for socket_name, endpoint in node.endpoints.items()
            }
            container_name = _container_name(project_name, runtime_id, node.node_id)
            static_environment = node.metadata.get("environment", {})
            if not isinstance(static_environment, Mapping):
                raise UnsupportedDockerRuntimeFeature(
                    f"node {node.node_id!r} metadata environment must be a mapping"
                )
            return StartDockerContainer(
                runtime_id=runtime_id,
                node_id=node.node_id,
                container_name=container_name,
                image=image,
                network_name=network_name,
                environment={**dict(static_environment), **node.environment},
                command=command,
                ports=ports,
                metadata={"kind": node.kind},
            )
        case _:
            raise UnsupportedDockerRuntimeFeature(
                f"node {node.node_id!r} has kind {node.kind!r}; Docker planner needs a Docker-backed implementation"
            )


def _redacted_environment(environment: Mapping[str, str]) -> dict[str, str]:
    return {key: "<redacted>" for key in sorted(environment)}


def _required_metadata(node: Node, key: str) -> str:
    value = node.metadata.get(key)
    if not isinstance(value, str) or not value:
        raise UnsupportedDockerRuntimeFeature(f"node {node.node_id!r} is missing metadata {key!r}")
    return value


def _network_name(runtime_id: str, metadata: Mapping[str, str]) -> str:
    return metadata.get("network_name", f"{runtime_id}-network")


def _container_name(project_name: str, runtime_id: str, node_id: str) -> str:
    safe = f"{project_name}-{runtime_id}-{node_id}"
    return safe.replace("_", "-").replace(".", "-")
