from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace
import subprocess
from threading import Barrier, Lock
import unittest

from control_plane_kit import (
    ConfigurationArtifact,
    ConfigurationFileMode,
    ConfigurationMediaType,
    DataMountMaterial,
    EffectFailed,
    EffectIdentity,
    EffectRequest,
    EffectSucceeded,
    ImplementationMaterial,
    MaterializedEffectRequest,
    NodeMaterial,
    NodeTarget,
    PinnedGraphSet,
    RemoveNodeResource,
    RuntimeKind,
    RuntimeMaterial,
    StartNode,
)
from control_plane_kit.docker_runtime import (
    DockerCliClient,
    DockerEffectInterpreter,
    DockerResourceInspection,
    DockerResourceKind,
)
from control_plane_kit.core.planning import ActivityId
from control_plane_kit.docker_runtime import plan_docker_effect


@dataclass
class ConfigurationClient:
    containers: dict[str, DockerResourceInspection] = field(default_factory=dict)
    volumes: dict[str, DockerResourceInspection] = field(default_factory=dict)
    digests: dict[str, str] = field(default_factory=dict)
    calls: list[tuple[object, ...]] = field(default_factory=list)
    fail_materialization: bool = False

    def inspect_container(self, name, *, timeout_seconds=30):
        self.calls.append(("inspect-container", name))
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
        self.calls.append(
            (
                "run-container",
                name,
                tuple(value.docker_argument() for value in configuration_mounts),
                dict(mounts or {}),
            )
        )
        self.containers[name] = DockerResourceInspection(
            DockerResourceKind.CONTAINER,
            "container-id",
            name,
            True,
            image,
            dict(labels),
        )

    def start_existing_container(self, resource_id, *, timeout_seconds=30):
        raise AssertionError("configuration tests do not stop the fixture container")

    def stop_owned_container(self, name, ownership, *, timeout_seconds=30):
        current = self.containers.get(name)
        if current is not None:
            self.containers[name] = replace(current, running=False)

    def remove_owned_container(self, name, ownership, *, timeout_seconds=30):
        self.calls.append(("remove-container", name))
        self.containers.pop(name, None)

    def inspect_volume(self, name, *, timeout_seconds=30):
        self.calls.append(("inspect-volume", name))
        return self.volumes.get(name)

    def create_volume(self, name, labels, *, timeout_seconds=30):
        self.calls.append(("create-volume", name))
        self.volumes[name] = DockerResourceInspection(
            DockerResourceKind.VOLUME,
            name,
            name,
            False,
            None,
            dict(labels),
        )

    def materialize_configuration_artifact(
        self,
        volume_name,
        artifact,
        *,
        timeout_seconds=30,
    ):
        self.calls.append(("materialize", volume_name, artifact.artifact_id))
        if self.fail_materialization:
            raise subprocess.TimeoutExpired("configuration-helper", 1)
        self.digests[volume_name] = artifact.content_digest

    def configuration_artifact_digest(self, volume_name, *, timeout_seconds=30):
        self.calls.append(("digest", volume_name))
        return self.digests.get(volume_name)

    def remove_owned_volume(self, name, ownership, *, timeout_seconds=30):
        self.calls.append(("remove-volume", name))
        self.volumes.pop(name, None)
        self.digests.pop(name, None)

    def inspect_network(self, name, *, timeout_seconds=30):
        return None

    def create_network(self, name, labels, *, timeout_seconds=30):
        raise AssertionError("network mutation is outside this fixture")

    def remove_owned_network(self, name, ownership, *, timeout_seconds=30):
        raise AssertionError("network mutation is outside this fixture")

    def ensure_network(self, name, *, timeout_seconds=30):
        raise AssertionError("legacy network mutation is outside this fixture")

    def start_container(self, **kwargs):
        raise AssertionError("legacy container mutation is outside this fixture")

    def stop_container(self, name, *, timeout_seconds=30):
        raise AssertionError("legacy stop is outside this fixture")

    def remove_container(self, name, *, timeout_seconds=30):
        raise AssertionError("legacy removal is outside this fixture")

    def remove_network(self, name, *, timeout_seconds=30):
        raise AssertionError("legacy removal is outside this fixture")


class ConcurrentConfigurationClient(ConfigurationClient):
    def __init__(self) -> None:
        super().__init__()
        self._lock = Lock()
        self._container_preflight = Barrier(2)
        self._volume_preflight = Barrier(2)
        self._materialization = Barrier(2)
        self._container_inspections = 0
        self._volume_inspections = 0

    def inspect_container(self, name, *, timeout_seconds=30):
        with self._lock:
            self.calls.append(("inspect-container", name))
            self._container_inspections += 1
            preflight = self._container_inspections <= 2
            current = self.containers.get(name)
        if preflight:
            self._container_preflight.wait(timeout=timeout_seconds)
            return None
        return current

    def inspect_volume(self, name, *, timeout_seconds=30):
        with self._lock:
            self.calls.append(("inspect-volume", name))
            self._volume_inspections += 1
            preflight = self._volume_inspections <= 2
            current = self.volumes.get(name)
        if preflight:
            self._volume_preflight.wait(timeout=timeout_seconds)
            return None
        return current

    def create_volume(self, name, labels, *, timeout_seconds=30):
        with self._lock:
            self.calls.append(("create-volume", name))
            if name in self.volumes:
                raise subprocess.CalledProcessError(1, ("docker", "volume", "create"))
            self.volumes[name] = DockerResourceInspection(
                DockerResourceKind.VOLUME,
                name,
                name,
                False,
                None,
                dict(labels),
            )

    def materialize_configuration_artifact(
        self,
        volume_name,
        artifact,
        *,
        timeout_seconds=30,
    ):
        with self._lock:
            self.calls.append(("materialize", volume_name, artifact.artifact_id))
        self._materialization.wait(timeout=timeout_seconds)
        with self._lock:
            self.digests[volume_name] = artifact.content_digest

    def configuration_artifact_digest(self, volume_name, *, timeout_seconds=30):
        with self._lock:
            self.calls.append(("digest", volume_name))
            return self.digests.get(volume_name)

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
        with self._lock:
            for mount in configuration_mounts:
                if self.digests.get(mount.volume_name) != mount.artifact.content_digest:
                    raise AssertionError("container started before configuration verification")
            self.calls.append(("run-container", name))
            if name in self.containers:
                raise subprocess.CalledProcessError(1, ("docker", "run"))
            self.containers[name] = DockerResourceInspection(
                DockerResourceKind.CONTAINER,
                "container-id",
                name,
                True,
                image,
                dict(labels),
            )


class DockerConfigurationTests(unittest.TestCase):
    def test_start_materializes_before_container_and_replay_converges(self) -> None:
        client = ConfigurationClient()
        interpreter = DockerEffectInterpreter(project_name="demo", client=client)
        request = _request(StartNode(NodeTarget("api")), _node())

        first = interpreter.execute(request)
        second = interpreter.execute(request)

        self.assertIsInstance(first, EffectSucceeded)
        self.assertIsInstance(second, EffectSucceeded)
        operations = [value[0] for value in client.calls]
        self.assertLess(operations.index("materialize"), operations.index("run-container"))
        self.assertEqual(operations.count("materialize"), 1)
        self.assertEqual(operations.count("run-container"), 1)
        mount = next(value for value in client.calls if value[0] == "run-container")[2][0]
        self.assertIn("volume-subpath=content", mount)
        self.assertTrue(mount.endswith(",readonly"))
        evidence = first.evidence.descriptor()
        self.assertEqual(
            evidence["configuration_artifacts"],
            {"service-config": "absent"},
        )

    def test_owned_volume_with_wrong_digest_fails_without_starting(self) -> None:
        client = ConfigurationClient()
        interpreter = DockerEffectInterpreter(project_name="demo", client=client)
        request = _request(StartNode(NodeTarget("api")), _node())
        self.assertIsInstance(interpreter.execute(request), EffectSucceeded)
        volume_name = next(iter(client.digests))
        client.digests[volume_name] = "0" * 64

        result = interpreter.execute(request)

        self.assertIsInstance(result, EffectFailed)
        self.assertEqual(result.failure.code, "docker.configuration-conflict")
        self.assertEqual(
            [value[0] for value in client.calls].count("run-container"),
            1,
        )

    def test_owned_incomplete_volume_is_completed_by_exact_replay(self) -> None:
        client = ConfigurationClient(fail_materialization=True)
        interpreter = DockerEffectInterpreter(project_name="demo", client=client)
        request = _request(StartNode(NodeTarget("api")), _node())
        first = interpreter.execute(request)
        self.assertIsInstance(first, EffectFailed)
        self.assertEqual(first.failure.category.value, "uncertain")

        client.fail_materialization = False
        replay = interpreter.execute(request)

        self.assertIsInstance(replay, EffectSucceeded)
        self.assertEqual(
            next(iter(client.digests.values())),
            _artifact().content_digest,
        )

    def test_concurrent_identical_starts_converge_on_exact_artifact(self) -> None:
        client = ConcurrentConfigurationClient()
        request = _request(StartNode(NodeTarget("api")), _node())

        def execute():
            return DockerEffectInterpreter(project_name="demo", client=client).execute(
                request
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = tuple(executor.map(lambda _: execute(), range(2)))

        self.assertTrue(all(isinstance(result, EffectSucceeded) for result in results))
        self.assertEqual(len(client.volumes), 1)
        self.assertEqual(len(client.containers), 1)
        self.assertEqual(tuple(client.digests.values()), (_artifact().content_digest,))
        self.assertEqual(
            [call[0] for call in client.calls].count("materialize"),
            2,
        )
        self.assertEqual(
            [call[0] for call in client.calls].count("run-container"),
            1,
        )

    def test_partial_materialization_is_uncertain_and_cleanup_is_artifact_only(self) -> None:
        client = ConfigurationClient(fail_materialization=True)
        interpreter = DockerEffectInterpreter(project_name="demo", client=client)
        node = _node(with_data=True)

        failed = interpreter.execute(_request(StartNode(NodeTarget("api")), node))

        self.assertIsInstance(failed, EffectFailed)
        self.assertEqual(failed.failure.category.value, "uncertain")
        self.assertEqual(len(client.volumes), 2)
        client.fail_materialization = False

        removed = interpreter.execute(
            _request(RemoveNodeResource(NodeTarget("api")), node, graph_id="base")
        )

        self.assertIsInstance(removed, EffectSucceeded)
        self.assertIn("demo-docker-api-database", client.volumes)
        self.assertNotIn("demo-docker-api-config-service-config", client.volumes)

    def test_changed_content_changes_container_and_volume_ownership(self) -> None:
        original = _request(StartNode(NodeTarget("api")), _node())
        changed = _request(
            StartNode(NodeTarget("api")),
            _node(content='{"workers":3}\n'),
        )
        original_command = plan_docker_effect(original, project_name="demo")
        changed_command = plan_docker_effect(changed, project_name="demo")

        self.assertNotEqual(
            original_command.ownership.intent_fingerprint,
            changed_command.ownership.intent_fingerprint,
        )
        self.assertNotEqual(
            original_command.configuration_mounts[0].ownership.intent_fingerprint,
            changed_command.configuration_mounts[0].ownership.intent_fingerprint,
        )

    def test_container_conflict_preflights_before_artifact_mutation(self) -> None:
        client = ConfigurationClient(
            containers={
                "demo-docker-api": DockerResourceInspection(
                    DockerResourceKind.CONTAINER,
                    "foreign-container",
                    "demo-docker-api",
                    True,
                    "foreign:latest",
                    {},
                )
            }
        )

        result = DockerEffectInterpreter(project_name="demo", client=client).execute(
            _request(StartNode(NodeTarget("api")), _node())
        )

        self.assertIsInstance(result, EffectFailed)
        self.assertEqual(result.failure.code, "docker.ownership-conflict")
        self.assertNotIn("create-volume", [value[0] for value in client.calls])
        self.assertNotIn("materialize", [value[0] for value in client.calls])

    def test_cleanup_preflights_every_artifact_before_container_removal(self) -> None:
        client = ConfigurationClient()
        interpreter = DockerEffectInterpreter(project_name="demo", client=client)
        node = _node()
        self.assertIsInstance(
            interpreter.execute(_request(StartNode(NodeTarget("api")), node)),
            EffectSucceeded,
        )
        volume_name = next(iter(client.volumes))
        client.volumes[volume_name] = replace(
            client.volumes[volume_name],
            labels={},
        )

        result = interpreter.execute(
            _request(RemoveNodeResource(NodeTarget("api")), node, graph_id="base")
        )

        self.assertIsInstance(result, EffectFailed)
        self.assertEqual(result.failure.code, "docker.ownership-conflict")
        self.assertIn("demo-docker-api", client.containers)
        self.assertNotIn("remove-container", [value[0] for value in client.calls])

    def test_cli_never_places_content_in_argv_and_uses_read_only_helper(self) -> None:
        class RecordingCli(DockerCliClient):
            def _run_with_input(self, input_text, *args, **kwargs):
                self.recorded = (input_text, args)
                return subprocess.CompletedProcess(args, 0)

        artifact = _artifact('{"marker":"configuration-content-not-in-argv"}\n')
        client = RecordingCli()

        client.materialize_configuration_artifact("config-volume", artifact)

        content, args = client.recorded
        self.assertEqual(content, artifact.content)
        self.assertNotIn(artifact.content, args)
        self.assertIn("--network", args)
        self.assertIn("none", args)
        self.assertIn("--read-only", args)
        self.assertIn("no-new-privileges", args)
        helper_image = args[args.index("no-new-privileges") + 3]
        self.assertIn("@sha256:", helper_image)


def _artifact(content: str = '{"workers":2}\n') -> ConfigurationArtifact:
    return ConfigurationArtifact(
        "service-config",
        "/etc/service/config.json",
        ConfigurationMediaType.JSON,
        content,
        ConfigurationFileMode.READ_ONLY,
    )


def _node(*, content: str = '{"workers":2}\n', with_data: bool = False) -> NodeMaterial:
    runtime = RuntimeMaterial(
        "docker",
        RuntimeKind.DOCKER,
        ("api",),
        "deployment-network",
    )
    data_mounts = (
        (DataMountMaterial("database", "/var/lib/service"),)
        if with_data
        else ()
    )
    return NodeMaterial(
        "api",
        runtime,
        ImplementationMaterial(
            "docker-image",
            image="python:3.14-slim",
            command=("python", "-V"),
            data_mounts=data_mounts,
            configuration_artifacts=(_artifact(content),),
        ),
        (),
        (),
        None,
    )


def _request(action, material, *, graph_id="desired") -> MaterializedEffectRequest:
    return MaterializedEffectRequest(
        EffectRequest(
            EffectIdentity("run", ActivityId("activity"), 1, "run:activity:1"),
            action,
        ),
        PinnedGraphSet("workspace", "plan", "base", "desired"),
        graph_id,
        material,
    )


if __name__ == "__main__":
    unittest.main()
