from __future__ import annotations

from dataclasses import dataclass, field
import subprocess
import unittest

from control_plane_kit import (
    DockerCliClient,
    DockerEffectInterpreter,
    EffectIdentity,
    EffectRequest,
    EffectSucceeded,
    EffectUnsupported,
    EnvironmentBindingMaterial,
    ImplementationMaterial,
    LiteralMaterialValue,
    MaterializedEffectRequest,
    NodeMaterial,
    PinnedGraphSet,
    RuntimeKind,
    RuntimeMaterial,
)
from control_plane_kit.execution import ObservationStatus
from control_plane_kit.planning import (
    ActivityId,
    NodeTarget,
    RuntimeTarget,
    StartNode,
    StartRuntime,
    StopNode,
    StopRuntime,
)


@dataclass
class NarrowClient:
    timeout: bool = False
    calls: list[tuple] = field(default_factory=list)
    networks: dict[str, object] = field(default_factory=dict)
    containers: dict[str, object] = field(default_factory=dict)

    def _record(self, value):
        if self.timeout:
            raise subprocess.TimeoutExpired("docker", 1)
        self.calls.append(value)

    def ensure_network(self, name, *, timeout_seconds=30):
        self._record(("ensure-network", name, timeout_seconds))

    def inspect_network(self, name, *, timeout_seconds=30):
        return self.networks.get(name)

    def create_network(self, name, labels, *, timeout_seconds=30):
        from control_plane_kit.docker_runtime import DockerResourceInspection, DockerResourceKind

        self._record(("ensure-network", name, timeout_seconds))
        self.networks[name] = DockerResourceInspection(
            DockerResourceKind.NETWORK, "network-id", name, False, None, dict(labels)
        )

    def start_container(self, *, name, image, network, environment, command, timeout_seconds=30):
        self._record(("start-container", name, image, network, dict(environment), tuple(command), timeout_seconds))

    def inspect_container(self, name, *, timeout_seconds=30):
        return self.containers.get(name)

    def run_container(self, *, name, image, network, environment, command, labels, timeout_seconds=30):
        from control_plane_kit.docker_runtime import DockerResourceInspection, DockerResourceKind

        self._record(("start-container", name, image, network, dict(environment), tuple(command), timeout_seconds))
        self.containers[name] = DockerResourceInspection(
            DockerResourceKind.CONTAINER, "container-id", name, True, image, dict(labels)
        )

    def start_existing_container(self, resource_id, *, timeout_seconds=30):
        self._record(("start-existing-container", resource_id, timeout_seconds))

    def stop_owned_container(self, name, ownership, *, timeout_seconds=30):
        self._record(("stop-container", name, timeout_seconds))

    def stop_container(self, name, *, timeout_seconds=30):
        self._record(("stop-container", name, timeout_seconds))

    def remove_container(self, name, *, timeout_seconds=30):
        self._record(("remove-container", name, timeout_seconds))

    def remove_network(self, name, *, timeout_seconds=30):
        self._record(("remove-network", name, timeout_seconds))


class DockerEffectTests(unittest.TestCase):
    def test_cli_keeps_environment_values_out_of_process_arguments(self) -> None:
        class RecordingCli(DockerCliClient):
            def _run(self, *args, **kwargs):
                self.recorded = (args, kwargs)
                return subprocess.CompletedProcess(args, 0)

        client = RecordingCli()
        client.run_container(
            name="api",
            image="api:latest",
            network="network",
            environment={"API_TOKEN": "never-in-argv"},
            command=(),
            labels={},
        )

        args, kwargs = client.recorded
        self.assertNotIn("never-in-argv", str(args))
        self.assertIn("API_TOKEN", args)
        self.assertEqual(kwargs["environment"]["API_TOKEN"], "never-in-argv")

    def test_runtime_start_ensures_only_the_pinned_network(self) -> None:
        client = NarrowClient()
        result = DockerEffectInterpreter(client=client).execute(
            _request(StartRuntime(RuntimeTarget("docker")), _runtime())
        )

        self.assertIsInstance(result, EffectSucceeded)
        self.assertEqual(client.calls, [("ensure-network", "deployment-network", 30)])

    def test_node_start_attempts_one_container_and_reports_process_not_health(self) -> None:
        client = NarrowClient()
        result = DockerEffectInterpreter(project_name="demo", client=client).execute(
            _request(StartNode(NodeTarget("api")), _node())
        )

        self.assertEqual(len(client.calls), 1)
        self.assertEqual(client.calls[0][:4], ("start-container", "demo-docker-api", "api:latest", "deployment-network"))
        self.assertEqual(client.calls[0][4], {"MODE": "live"})
        self.assertIs(result.observations[0].status, ObservationStatus.PROCESS_STARTED)
        self.assertNotIn("healthy", str(result.evidence.descriptor()).lower())

    def test_node_stop_does_not_remove_container_or_runtime(self) -> None:
        client = NarrowClient()
        result = DockerEffectInterpreter(project_name="demo", client=client).execute(
            _request(StopNode(NodeTarget("api")), _node(), graph_id="base")
        )

        self.assertIsInstance(result, EffectSucceeded)
        self.assertEqual(client.calls, [("stop-container", "demo-docker-api", 30)])

    def test_runtime_stop_is_unsupported_until_retention_policy_is_typed(self) -> None:
        client = NarrowClient()
        request = _request(StopRuntime(RuntimeTarget("docker")), _runtime(), graph_id="base")

        result = DockerEffectInterpreter(client=client).execute(request)

        self.assertEqual(result, EffectUnsupported(request.identity, request.capability))
        self.assertEqual(client.calls, [])

    def test_timeout_is_uncertain_and_does_not_publish_command_output(self) -> None:
        client = NarrowClient(timeout=True)
        result = DockerEffectInterpreter(client=client).execute(
            _request(StartRuntime(RuntimeTarget("docker")), _runtime())
        )

        self.assertEqual(result.failure.category.value, "uncertain")
        self.assertEqual(result.failure.code, "docker.timeout")
        self.assertNotIn("api:latest", result.failure.message)


def _runtime() -> RuntimeMaterial:
    return RuntimeMaterial(
        "docker",
        RuntimeKind.DOCKER,
        ("api",),
        "deployment-network",
    )


def _node() -> NodeMaterial:
    environment = (
        EnvironmentBindingMaterial("MODE", LiteralMaterialValue("live")),
    )
    return NodeMaterial(
        "api",
        _runtime(),
        ImplementationMaterial(
            "docker-image",
            image="api:latest",
            command=("python", "-m", "api"),
            environment=environment,
        ),
        (),
        environment,
        "/health",
    )


def _request(action, material, *, graph_id="desired") -> MaterializedEffectRequest:
    abstract = EffectRequest(
        EffectIdentity("run", ActivityId("activity"), 1, "run:activity:1"),
        action,
    )
    return MaterializedEffectRequest(
        abstract,
        PinnedGraphSet("workspace", "plan", "base", "desired"),
        graph_id,
        material,
    )


if __name__ == "__main__":
    unittest.main()
