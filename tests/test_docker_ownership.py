from __future__ import annotations

from dataclasses import dataclass, field, replace
import subprocess
import unittest

from control_plane_kit import (
    DockerEffectInterpreter,
    EffectFailed,
    EffectIdentity,
    EffectRequest,
    EffectSucceeded,
    EnvironmentBindingMaterial,
    ImplementationMaterial,
    LiteralMaterialValue,
    MaterializedEffectRequest,
    NodeMaterial,
    PinnedGraphSet,
    RuntimeKind,
    RuntimeMaterial,
)
from control_plane_kit.docker_runtime import (
    DockerOwnership,
    DockerResourceInspection,
    DockerResourceKind,
)
from control_plane_kit.planning import ActivityId, NodeTarget, StartNode, StopNode


@dataclass
class OwnershipClient:
    container: DockerResourceInspection | None = None
    calls: list[tuple[object, ...]] = field(default_factory=list)
    race_on_run: bool = False

    def inspect_container(self, name, *, timeout_seconds=30):
        self.calls.append(("inspect", name))
        return self.container

    def run_container(
        self,
        *,
        name,
        image,
        network,
        environment,
        command,
        labels,
        timeout_seconds=30,
    ):
        self.calls.append(("run", name, image, dict(labels)))
        self.container = DockerResourceInspection(
            DockerResourceKind.CONTAINER,
            "container-id",
            name,
            True,
            image,
            dict(labels),
        )
        if self.race_on_run:
            raise subprocess.CalledProcessError(125, ("docker", "run"))

    def start_existing_container(self, resource_id, *, timeout_seconds=30):
        self.calls.append(("start-existing", resource_id))
        assert self.container is not None
        self.container = replace(self.container, running=True)

    def stop_owned_container(self, name, ownership, *, timeout_seconds=30):
        self.calls.append(("stop", name))
        assert self.container is not None
        self.container = replace(self.container, running=False)

    def inspect_network(self, name, *, timeout_seconds=30):
        return None

    def create_network(self, name, labels, *, timeout_seconds=30):
        raise AssertionError("network mutation was not expected")

    def ensure_network(self, name, *, timeout_seconds=30):
        raise AssertionError("legacy network mutation was not expected")

    def start_container(self, **kwargs):
        raise AssertionError("legacy container mutation was not expected")

    def stop_container(self, name, *, timeout_seconds=30):
        raise AssertionError("legacy stop was not expected")

    def remove_container(self, name, *, timeout_seconds=30):
        raise AssertionError("removal was not expected")

    def remove_network(self, name, *, timeout_seconds=30):
        raise AssertionError("removal was not expected")


class DockerOwnershipTests(unittest.TestCase):
    def test_absent_resource_is_created_with_stable_ownership_labels(self) -> None:
        client = OwnershipClient()

        result = DockerEffectInterpreter(project_name="demo", client=client).execute(
            _request(StartNode(NodeTarget("api")), _node())
        )

        self.assertIsInstance(result, EffectSucceeded)
        labels = client.calls[1][3]
        self.assertEqual(labels["io.control-plane-kit.workspace"], "workspace")
        self.assertEqual(labels["io.control-plane-kit.runtime"], "docker")
        self.assertEqual(labels["io.control-plane-kit.node"], "api")
        self.assertEqual(labels["io.control-plane-kit.resource"], "container")
        self.assertIn("io.control-plane-kit.intent", labels)
        ownership = result.evidence.descriptor()["ownership"]
        self.assertEqual(ownership["workspace_id"], "workspace")
        self.assertEqual(ownership["node_id"], "api")
        self.assertEqual(
            ownership["intent_fingerprint"],
            labels["io.control-plane-kit.intent"],
        )

    def test_identical_replay_reuses_running_container(self) -> None:
        client = OwnershipClient()
        interpreter = DockerEffectInterpreter(project_name="demo", client=client)
        request = _request(StartNode(NodeTarget("api")), _node())

        first = interpreter.execute(request)
        second = interpreter.execute(request)

        self.assertIsInstance(first, EffectSucceeded)
        self.assertIsInstance(second, EffectSucceeded)
        self.assertEqual([call[0] for call in client.calls].count("run"), 1)
        self.assertEqual([call[0] for call in client.calls].count("start-existing"), 0)

    def test_compatible_stopped_container_is_started_without_recreation(self) -> None:
        client = OwnershipClient()
        interpreter = DockerEffectInterpreter(project_name="demo", client=client)
        request = _request(StartNode(NodeTarget("api")), _node())
        interpreter.execute(request)
        assert client.container is not None
        client.container = replace(client.container, running=False)

        result = interpreter.execute(request)

        self.assertIsInstance(result, EffectSucceeded)
        self.assertEqual([call[0] for call in client.calls].count("run"), 1)
        self.assertEqual([call[0] for call in client.calls].count("start-existing"), 1)

    def test_changed_material_conflicts_instead_of_retargeting_same_name(self) -> None:
        client = OwnershipClient()
        interpreter = DockerEffectInterpreter(project_name="demo", client=client)
        interpreter.execute(_request(StartNode(NodeTarget("api")), _node()))

        result = interpreter.execute(
            _request(StartNode(NodeTarget("api")), _node(image="api:v2"))
        )

        self.assertIsInstance(result, EffectFailed)
        self.assertEqual(result.failure.code, "docker.ownership-conflict")
        self.assertEqual([call[0] for call in client.calls].count("run"), 1)

    def test_unowned_name_collision_is_never_mutated(self) -> None:
        client = OwnershipClient(
            container=DockerResourceInspection(
                DockerResourceKind.CONTAINER,
                "foreign-id",
                "demo-docker-api",
                True,
                "foreign:latest",
                {},
            )
        )

        result = DockerEffectInterpreter(project_name="demo", client=client).execute(
            _request(StopNode(NodeTarget("api")), _node(), graph_id="base")
        )

        self.assertIsInstance(result, EffectFailed)
        self.assertEqual(result.failure.code, "docker.ownership-conflict")
        self.assertNotIn("stop", [call[0] for call in client.calls])

    def test_concurrent_equivalent_creation_converges_on_owned_resource(self) -> None:
        client = OwnershipClient(race_on_run=True)

        result = DockerEffectInterpreter(project_name="demo", client=client).execute(
            _request(StartNode(NodeTarget("api")), _node())
        )

        self.assertIsInstance(result, EffectSucceeded)
        self.assertEqual(result.evidence.descriptor()["disposition"], "owned-compatible")


def _runtime() -> RuntimeMaterial:
    return RuntimeMaterial("docker", RuntimeKind.DOCKER, ("api",), "deployment-network")


def _node(*, image: str = "api:latest") -> NodeMaterial:
    environment = (EnvironmentBindingMaterial("MODE", LiteralMaterialValue("live")),)
    return NodeMaterial(
        "api",
        _runtime(),
        ImplementationMaterial(
            "docker-image",
            image=image,
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
