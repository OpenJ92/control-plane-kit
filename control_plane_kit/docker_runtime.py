"""Docker runtime planning and execution values."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field, replace
from typing import Mapping, Protocol

from control_plane_kit.graph import DeploymentGraph, Node
from control_plane_kit.runtimes import CleanupPolicy, RuntimeActivity, RuntimeNodeState, RuntimePlan, RuntimeState
from control_plane_kit.types import RuntimeKind


class UnsupportedDockerRuntimeFeature(ValueError):
    """Raised when a graph cannot be realized by the Docker interpreter yet."""


class DockerClient(Protocol):
    """Small Docker capability surface used by the runtime executor."""

    def ensure_network(self, name: str) -> None:
        """Create the network if needed."""

    def start_container(
        self,
        *,
        name: str,
        image: str,
        network: str,
        environment: Mapping[str, str],
        command: tuple[str, ...],
    ) -> None:
        """Start a detached container."""

    def stop_container(self, name: str) -> None:
        """Stop a container if it is running."""

    def remove_container(self, name: str) -> None:
        """Remove a container if it exists."""

    def remove_network(self, name: str) -> None:
        """Remove a network if it exists."""


@dataclass(frozen=True)
class DockerCliClient:
    """Docker client backed by the local `docker` CLI."""

    docker: str = "docker"

    def ensure_network(self, name: str) -> None:
        result = self._run("network", "inspect", name, check=False)
        if result.returncode != 0:
            self._run("network", "create", name)

    def start_container(
        self,
        *,
        name: str,
        image: str,
        network: str,
        environment: Mapping[str, str],
        command: tuple[str, ...],
    ) -> None:
        self.remove_container(name)
        args = ["run", "-d", "--name", name, "--network", network]
        for key, value in sorted(environment.items()):
            args.extend(("-e", f"{key}={value}"))
        args.append(image)
        args.extend(command)
        self._run(*args)

    def stop_container(self, name: str) -> None:
        self._run("stop", name, check=False)

    def remove_container(self, name: str) -> None:
        self._run("rm", "-f", name, check=False)

    def remove_network(self, name: str) -> None:
        self._run("network", "rm", name, check=False)

    def _run(self, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            (self.docker, *args),
            check=check,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )


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
                        healthy=True,
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
