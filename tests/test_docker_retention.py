from __future__ import annotations

from dataclasses import dataclass, field, replace
import unittest

from control_plane_kit import (
    ActivityId,
    DataMountMaterial,
    DataResourceTarget,
    DestroyDataResource,
    DockerEffectInterpreter,
    EffectFailed,
    EffectIdentity,
    EffectRequest,
    EffectSucceeded,
    EffectUnsupported,
    ImplementationMaterial,
    MaterializedEffectRequest,
    NodeMaterial,
    NodeTarget,
    PinnedGraphSet,
    RemoveNodeResource,
    RemoveRuntimeResource,
    ResourceLifecycle,
    ResourceOwnership,
    ResourcePersistence,
    RuntimeKind,
    RuntimeMaterial,
    RuntimeTarget,
    StartNode,
    StopNode,
)
from control_plane_kit.docker_runtime import (
    DockerOwnership,
    DockerResourceInspection,
    DockerResourceKind,
)


@dataclass
class RetentionClient:
    networks: dict[str, DockerResourceInspection] = field(default_factory=dict)
    containers: dict[str, DockerResourceInspection] = field(default_factory=dict)
    volumes: dict[str, DockerResourceInspection] = field(default_factory=dict)
    calls: list[tuple[object, ...]] = field(default_factory=list)

    def inspect_network(self, name, *, timeout_seconds=30):
        return self.networks.get(name)

    def create_network(self, name, labels, *, timeout_seconds=30):
        self.networks[name] = DockerResourceInspection(
            DockerResourceKind.NETWORK, "network-id", name, False, None, dict(labels)
        )
        self.calls.append(("create-network", name))

    def inspect_container(self, name, *, timeout_seconds=30):
        return self.containers.get(name)

    def run_container(
        self,
        *,
        name,
        image,
        network,
        environment,
        command,
        labels,
        mounts=None,
        configuration_mounts=(),
        secret_mounts=(),
        ports=(),
        timeout_seconds=30,
    ):
        self.containers[name] = DockerResourceInspection(
            DockerResourceKind.CONTAINER,
            "container-id",
            name,
            True,
            image,
            dict(labels),
        )
        self.calls.append(("run-container", name, dict(mounts or {})))

    def start_existing_container(self, resource_id, *, timeout_seconds=30):
        self.calls.append(("start-container", resource_id))

    def stop_owned_container(self, name, ownership, *, timeout_seconds=30):
        current = self.containers.get(name)
        if current is not None:
            self.containers[name] = replace(current, running=False)
        self.calls.append(("stop-container", name))

    def remove_owned_container(self, name, ownership, *, timeout_seconds=30):
        self.containers.pop(name, None)
        self.calls.append(("remove-container", name))

    def remove_owned_network(self, name, ownership, *, timeout_seconds=30):
        self.networks.pop(name, None)
        self.calls.append(("remove-network", name))

    def inspect_volume(self, name, *, timeout_seconds=30):
        return self.volumes.get(name)

    def create_volume(self, name, labels, *, timeout_seconds=30):
        self.volumes[name] = DockerResourceInspection(
            DockerResourceKind.VOLUME,
            name,
            name,
            False,
            None,
            dict(labels),
        )
        self.calls.append(("create-volume", name, dict(labels)))

    def remove_owned_volume(self, name, ownership, *, timeout_seconds=30):
        self.volumes.pop(name, None)
        self.calls.append(("remove-volume", name))

    def ensure_network(self, name, *, timeout_seconds=30):
        raise AssertionError("legacy network path is not expected")

    def start_container(self, **kwargs):
        raise AssertionError("legacy container path is not expected")

    def stop_container(self, name, *, timeout_seconds=30):
        raise AssertionError("legacy stop path is not expected")

    def remove_container(self, name, *, timeout_seconds=30):
        raise AssertionError("legacy removal path is not expected")

    def remove_network(self, name, *, timeout_seconds=30):
        raise AssertionError("legacy removal path is not expected")


class DockerRetentionTests(unittest.TestCase):
    def test_postgres_start_creates_owned_volume_and_mounts_it(self):
        client = RetentionClient()

        result = DockerEffectInterpreter(project_name="demo", client=client).execute(
            _request(StartNode(NodeTarget("postgres")), _postgres_node())
        )

        self.assertIsInstance(result, EffectSucceeded)
        volume_call = next(call for call in client.calls if call[0] == "create-volume")
        self.assertEqual(volume_call[1], "demo-docker-postgres-postgres-data")
        self.assertEqual(
            volume_call[2]["io.control-plane-kit.data-resource"],
            "postgres-data",
        )
        run_call = next(call for call in client.calls if call[0] == "run-container")
        self.assertEqual(
            run_call[2],
            {"demo-docker-postgres-postgres-data": "/var/lib/postgresql/data"},
        )

    def test_stop_and_compute_removal_preserve_named_data(self):
        client = RetentionClient()
        interpreter = DockerEffectInterpreter(project_name="demo", client=client)
        node = _postgres_node()
        interpreter.execute(_request(StartNode(NodeTarget("postgres")), node))

        stopped = interpreter.execute(
            _request(StopNode(NodeTarget("postgres")), node, graph_id="base")
        )
        removed = interpreter.execute(
            _request(RemoveNodeResource(NodeTarget("postgres")), node, graph_id="base")
        )

        self.assertIsInstance(stopped, EffectSucceeded)
        self.assertIsInstance(removed, EffectSucceeded)
        self.assertIn("demo-docker-postgres-postgres-data", client.volumes)
        self.assertNotIn("demo-docker-postgres", client.containers)
        self.assertNotIn("remove-volume", [call[0] for call in client.calls])

    def test_explicit_data_destruction_removes_only_the_named_owned_volume(self):
        client = RetentionClient()
        interpreter = DockerEffectInterpreter(project_name="demo", client=client)
        node = _postgres_node()
        interpreter.execute(_request(StartNode(NodeTarget("postgres")), node))

        result = interpreter.execute(
            _request(
                DestroyDataResource(
                    DataResourceTarget("postgres", "postgres-data")
                ),
                node,
                graph_id="base",
            )
        )

        self.assertIsInstance(result, EffectSucceeded)
        self.assertEqual(
            [call for call in client.calls if call[0] == "remove-volume"],
            [("remove-volume", "demo-docker-postgres-postgres-data")],
        )

    def test_unowned_volume_collision_fails_before_container_start(self):
        client = RetentionClient(
            volumes={
                "demo-docker-postgres-postgres-data": DockerResourceInspection(
                    DockerResourceKind.VOLUME,
                    "foreign-volume",
                    "demo-docker-postgres-postgres-data",
                    False,
                    None,
                    {},
                )
            }
        )

        result = DockerEffectInterpreter(project_name="demo", client=client).execute(
            _request(StartNode(NodeTarget("postgres")), _postgres_node())
        )

        self.assertIsInstance(result, EffectFailed)
        self.assertEqual(result.failure.code, "docker.ownership-conflict")
        self.assertNotIn("run-container", [call[0] for call in client.calls])

    def test_start_and_explicit_destruction_replay_converge(self):
        client = RetentionClient()
        interpreter = DockerEffectInterpreter(project_name="demo", client=client)
        node = _postgres_node()
        start = _request(StartNode(NodeTarget("postgres")), node)
        destroy = _request(
            DestroyDataResource(DataResourceTarget("postgres", "postgres-data")),
            node,
            graph_id="base",
        )

        first_start = interpreter.execute(start)
        replayed_start = interpreter.execute(start)
        first_destroy = interpreter.execute(destroy)
        replayed_destroy = interpreter.execute(destroy)

        self.assertIsInstance(first_start, EffectSucceeded)
        self.assertIsInstance(replayed_start, EffectSucceeded)
        self.assertIsInstance(first_destroy, EffectSucceeded)
        self.assertIsInstance(replayed_destroy, EffectSucceeded)
        self.assertEqual(
            len([call for call in client.calls if call[0] == "create-volume"]),
            1,
        )
        self.assertEqual(
            len([call for call in client.calls if call[0] == "run-container"]),
            1,
        )
        self.assertNotIn("demo-docker-postgres-postgres-data", client.volumes)

    def test_retained_and_external_compute_cannot_cross_removal_boundary(self):
        client = RetentionClient()
        interpreter = DockerEffectInterpreter(project_name="demo", client=client)
        retained = replace(
            _postgres_node(),
            lifecycle=ResourceLifecycle(
                ResourceOwnership.OWNED,
                ResourcePersistence.RETAINED,
                _postgres_node().lifecycle.data,
            ),
        )
        external_runtime = replace(
            _runtime(),
            lifecycle=ResourceLifecycle.external(),
        )

        retained_result = interpreter.execute(
            _request(
                RemoveNodeResource(NodeTarget("postgres")),
                retained,
                graph_id="base",
            )
        )
        external_result = interpreter.execute(
            _request(
                RemoveRuntimeResource(RuntimeTarget("docker")),
                external_runtime,
                graph_id="base",
            )
        )

        self.assertIsInstance(retained_result, EffectUnsupported)
        self.assertIsInstance(external_result, EffectUnsupported)
        self.assertEqual(client.calls, [])


def _runtime() -> RuntimeMaterial:
    return RuntimeMaterial(
        "docker",
        RuntimeKind.DOCKER,
        ("postgres",),
        "deployment-network",
    )


def _postgres_node() -> NodeMaterial:
    lifecycle = ResourceLifecycle.owned_with_retained_data("postgres-data")
    return NodeMaterial(
        "postgres",
        _runtime(),
        ImplementationMaterial(
            "docker-postgres",
            image="postgres:16-alpine",
            data_mounts=(
                DataMountMaterial("postgres-data", "/var/lib/postgresql/data"),
            ),
        ),
        (),
        (),
        None,
        lifecycle,
    )


def _request(action, material, *, graph_id="desired") -> MaterializedEffectRequest:
    return MaterializedEffectRequest(
        EffectRequest(
            EffectIdentity(
                "run",
                ActivityId(type(action).__name__),
                1,
                f"run:{type(action).__name__}:1",
            ),
            action,
        ),
        PinnedGraphSet("workspace", "plan", "base", "desired"),
        graph_id,
        material,
    )


if __name__ == "__main__":
    unittest.main()
