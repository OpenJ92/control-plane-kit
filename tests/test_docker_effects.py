from __future__ import annotations

from dataclasses import dataclass, field
from ipaddress import IPv4Address
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
    HostPublicationMaterial,
    ImplementationMaterial,
    LiteralMaterialValue,
    MaterializedEffectRequest,
    NodeMaterial,
    PinnedGraphSet,
    RuntimeKind,
    RuntimeMaterial,
    Protocol,
    Transport,
)
from control_plane_kit.docker_runtime import DockerPublishedPort, DockerPortBinding
from control_plane_kit.docker_runtime import UnsupportedDockerRuntimeFeature
from control_plane_kit.execution import ObservationStatus
from control_plane_kit.planning import (
    ActivityId,
    NodeTarget,
    RuntimeTarget,
    StartNode,
    StartRuntime,
    StopNode,
    StopRuntime,
    RemoveNodeResource,
    RemoveRuntimeResource,
)


@dataclass
class NarrowClient:
    timeout: bool = False
    calls: list[tuple] = field(default_factory=list)
    networks: dict[str, object] = field(default_factory=dict)
    containers: dict[str, object] = field(default_factory=dict)
    volumes: dict[str, object] = field(default_factory=dict)

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

    def run_container(self, *, name, image, network, environment, command, labels, mounts=None, ports=(), timeout_seconds=30):
        from control_plane_kit.docker_runtime import DockerResourceInspection, DockerResourceKind

        self._record(("start-container", name, image, network, dict(environment), tuple(command), dict(mounts or {}), tuple(ports), timeout_seconds))
        self.containers[name] = DockerResourceInspection(
            DockerResourceKind.CONTAINER,
            "container-id",
            name,
            True,
            image,
            dict(labels),
            tuple(
                DockerPublishedPort(
                    value.container_port,
                    value.protocol.transport,
                    value.host_address,
                    value.host_port or 49_152 + index,
                )
                for index, value in enumerate(ports)
            ),
        )

    def start_existing_container(self, resource_id, *, timeout_seconds=30):
        self._record(("start-existing-container", resource_id, timeout_seconds))

    def stop_owned_container(self, name, ownership, *, timeout_seconds=30):
        self._record(("stop-container", name, timeout_seconds))

    def remove_owned_container(self, name, ownership, *, timeout_seconds=30):
        self._record(("remove-container", name, timeout_seconds))

    def remove_owned_network(self, name, ownership, *, timeout_seconds=30):
        self._record(("remove-network", name, timeout_seconds))

    def inspect_volume(self, name, *, timeout_seconds=30):
        return self.volumes.get(name)

    def create_volume(self, name, labels, *, timeout_seconds=30):
        from control_plane_kit.docker_runtime import DockerResourceInspection, DockerResourceKind

        self._record(("create-volume", name, timeout_seconds))
        self.volumes[name] = DockerResourceInspection(
            DockerResourceKind.VOLUME, name, name, False, None, dict(labels)
        )

    def remove_owned_volume(self, name, ownership, *, timeout_seconds=30):
        self._record(("remove-volume", name, timeout_seconds))

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
        self.assertNotIn("--publish", args)
        self.assertEqual(kwargs["environment"]["API_TOKEN"], "never-in-argv")

    def test_cli_distinguishes_absence_from_failed_inspection(self) -> None:
        class RecordingCli(DockerCliClient):
            failure: bool = False

            def _capture(self, *args, **kwargs):
                if self.failure:
                    raise subprocess.CalledProcessError(1, args)
                self.recorded = args
                return subprocess.CompletedProcess(args, 0, stdout="")

        client = RecordingCli()

        self.assertIsNone(client.inspect_volume("missing"))
        self.assertEqual(client.recorded[:2], ("volume", "ls"))

        object.__setattr__(client, "failure", True)
        with self.assertRaises(subprocess.CalledProcessError):
            client.inspect_volume("unknown-because-docker-failed")

    def test_cli_publishes_only_explicit_typed_port_bindings(self) -> None:
        class RecordingCli(DockerCliClient):
            def _run(self, *args, **kwargs):
                self.recorded = args
                return subprocess.CompletedProcess(args, 0)

        client = RecordingCli()
        client.run_container(
            name="api",
            image="api:latest",
            network="network",
            environment={},
            command=(),
            labels={},
            ports=(
                DockerPortBinding(
                    "internal",
                    Protocol.HTTP,
                    8000,
                    "127.0.0.1",
                    None,
                ),
            ),
        )

        publish_index = client.recorded.index("--publish")
        self.assertEqual(
            client.recorded[publish_index + 1],
            "127.0.0.1::8000/tcp",
        )

    def test_cli_renders_udp_from_the_protocol_transport(self) -> None:
        class RecordingCli(DockerCliClient):
            def _run(self, *args, **kwargs):
                self.recorded = args
                return subprocess.CompletedProcess(args, 0)

        client = RecordingCli()
        client.run_container(
            name="dns",
            image="dns:latest",
            network="network",
            environment={},
            command=(),
            labels={},
            ports=(
                DockerPortBinding(
                    "dns-udp",
                    Protocol.DNS_UDP,
                    53,
                    "127.0.0.1",
                    10_053,
                ),
            ),
        )

        publish_index = client.recorded.index("--publish")
        self.assertEqual(
            client.recorded[publish_index + 1],
            "127.0.0.1:10053:53/udp",
        )

    def test_cli_inspection_preserves_private_and_host_port_distinction(self) -> None:
        class RecordingCli(DockerCliClient):
            def _resource_exists(self, *args, **kwargs):
                return True

            def _capture(self, *args, **kwargs):
                return subprocess.CompletedProcess(
                    args,
                    0,
                    stdout=(
                        'container-id\t/api\ttrue\tapi:latest\t{}\t'
                        '{"8000/tcp":[{"HostIp":"127.0.0.1",'
                        '"HostPort":"49152"}],'
                        '"5353/udp":[{"HostIp":"127.0.0.1",'
                        '"HostPort":"49153"}]}\n'
                    ),
                )

        inspected = RecordingCli().inspect_container("api")

        self.assertIsNotNone(inspected)
        self.assertEqual(
            inspected.published_ports,
            (
                DockerPublishedPort(5353, Transport.UDP, "127.0.0.1", 49_153),
                DockerPublishedPort(8000, Transport.TCP, "127.0.0.1", 49_152),
            ),
        )

    def test_cli_inspection_rejects_unknown_transport_suffix(self) -> None:
        class RecordingCli(DockerCliClient):
            def _resource_exists(self, *args, **kwargs):
                return True

            def _capture(self, *args, **kwargs):
                return subprocess.CompletedProcess(
                    args,
                    0,
                    stdout=(
                        'container-id\t/dns\ttrue\tdns:latest\t{}\t'
                        '{"53/sctp":[{"HostIp":"127.0.0.1",'
                        '"HostPort":"49153"}]}\n'
                    ),
                )

        with self.assertRaisesRegex(
            UnsupportedDockerRuntimeFeature,
            "port inspection was malformed",
        ):
            RecordingCli().inspect_container("dns")

    def test_host_publication_is_distinct_from_private_node_material(self) -> None:
        client = NarrowClient()

        result = DockerEffectInterpreter(project_name="demo", client=client).execute(
            _request(StartNode(NodeTarget("api")), _published_node())
        )

        self.assertIsInstance(result, EffectSucceeded)
        started = next(call for call in client.calls if call[0] == "start-container")
        self.assertEqual(len(started[7]), 1)
        self.assertEqual(started[7][0].socket_name, "internal")
        evidence = result.evidence.descriptor()["host_publications"]
        self.assertEqual(evidence[0]["host_address"], "127.0.0.1")
        self.assertEqual(evidence[0]["host_port"], 49_152)

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

    def test_runtime_stop_is_a_non_deleting_logical_barrier(self) -> None:
        client = NarrowClient()
        request = _request(StopRuntime(RuntimeTarget("docker")), _runtime(), graph_id="base")

        result = DockerEffectInterpreter(client=client).execute(request)

        self.assertIsInstance(result, EffectSucceeded)
        self.assertEqual(client.calls, [])

    def test_compute_removal_is_explicit_and_separate_from_stop(self) -> None:
        client = NarrowClient()
        node_result = DockerEffectInterpreter(project_name="demo", client=client).execute(
            _request(RemoveNodeResource(NodeTarget("api")), _node(), graph_id="base")
        )
        runtime_result = DockerEffectInterpreter(project_name="demo", client=client).execute(
            _request(
                RemoveRuntimeResource(RuntimeTarget("docker")),
                _runtime(),
                graph_id="base",
            )
        )

        self.assertIsInstance(node_result, EffectSucceeded)
        self.assertIsInstance(runtime_result, EffectSucceeded)
        self.assertEqual(
            client.calls,
            [
                ("remove-container", "demo-docker-api", 30),
                ("remove-network", "deployment-network", 30),
            ],
        )

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


def _published_node() -> NodeMaterial:
    node = _node()
    return NodeMaterial(
        node.node_id,
        node.runtime,
        ImplementationMaterial(
            node.implementation.kind,
            image=node.implementation.image,
            command=node.implementation.command,
            environment=node.implementation.environment,
            host_publications=(
                HostPublicationMaterial(
                    "internal",
                    Protocol.HTTP,
                    8000,
                    IPv4Address("127.0.0.1"),
                ),
            ),
        ),
        node.endpoints,
        node.environment,
        node.health_path,
        node.lifecycle,
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
