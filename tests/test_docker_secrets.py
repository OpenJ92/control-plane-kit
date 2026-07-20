from __future__ import annotations

from dataclasses import dataclass, field, replace
import subprocess
import unittest

from control_plane_kit import (
    EffectFailed,
    EffectIdentity,
    EffectRequest,
    EffectSucceeded,
    ImplementationMaterial,
    LocalDevelopmentSecretResolver,
    MaterializedEffectRequest,
    NodeMaterial,
    PinnedGraphSet,
    RemoveNodeResource,
    RuntimeKind,
    RuntimeMaterial,
    SecretFileMaterial,
    SecretFileMode,
    SecretProviderAuthority,
    SecretProviderId,
    SecretReference,
    SecretValue,
)
from control_plane_kit.docker_runtime import (
    DockerCliClient,
    DockerEffectInterpreter,
)
from control_plane_kit.docker_runtime import (
    DockerOwnership,
    DockerResourceInspection,
    DockerResourceKind,
    plan_docker_effect,
)
from control_plane_kit.execution import FailureCategory
from control_plane_kit.core.planning import ActivityId, NodeTarget, StartNode


SECRET_TEXT = "correct-horse-battery-staple"


@dataclass
class SecretClient:
    containers: dict[str, DockerResourceInspection] = field(default_factory=dict)
    volumes: dict[str, DockerResourceInspection] = field(default_factory=dict)
    ready: set[str] = field(default_factory=set)
    calls: list[tuple[object, ...]] = field(default_factory=list)
    fail_materialization: bool = False
    fail_start: bool = False

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
                tuple(mount.docker_argument() for mount in secret_mounts),
            )
        )
        if self.fail_start:
            raise subprocess.CalledProcessError(125, ("docker", "run"))
        self.containers[name] = DockerResourceInspection(
            DockerResourceKind.CONTAINER,
            "container-id",
            name,
            True,
            image,
            dict(labels),
        )

    def start_existing_container(self, resource_id, *, timeout_seconds=30):
        self.calls.append(("start-container", resource_id))

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
        self.calls.append(("create-volume", name, dict(labels)))
        self.volumes[name] = DockerResourceInspection(
            DockerResourceKind.VOLUME,
            name,
            name,
            False,
            None,
            dict(labels),
        )

    def materialize_secret_file(
        self,
        volume_name,
        value,
        file_mode,
        *,
        timeout_seconds=30,
    ):
        self.calls.append(
            ("materialize", volume_name, repr(value), file_mode.value)
        )
        if self.fail_materialization:
            raise subprocess.TimeoutExpired("secret-helper", 1)
        self.ready.add(volume_name)

    def secret_file_ready(self, volume_name, file_mode, *, timeout_seconds=30):
        self.calls.append(("ready", volume_name, file_mode.value))
        return volume_name in self.ready

    def remove_owned_volume(self, name, ownership, *, timeout_seconds=30):
        self.calls.append(("remove-volume", name))
        self.volumes.pop(name, None)
        self.ready.discard(name)

    def inspect_network(self, name, *, timeout_seconds=30):
        return None

    def create_network(self, name, labels, *, timeout_seconds=30):
        raise AssertionError("network mutation is outside this fixture")

    def remove_owned_network(self, name, ownership, *, timeout_seconds=30):
        raise AssertionError("network mutation is outside this fixture")

    def materialize_configuration_artifact(self, *args, **kwargs):
        raise AssertionError("configuration mutation is outside this fixture")

    def configuration_artifact_digest(self, *args, **kwargs):
        raise AssertionError("configuration inspection is outside this fixture")


class DockerSecretTests(unittest.TestCase):
    def test_start_materializes_before_container_and_replay_does_not_rewrite(self) -> None:
        client = SecretClient()
        interpreter = DockerEffectInterpreter(
            project_name="demo",
            client=client,
            secrets=_resolver(),
        )
        request = _request(StartNode(NodeTarget("api")), _node())

        first = interpreter.execute(request)
        second = interpreter.execute(request)

        self.assertIsInstance(first, EffectSucceeded)
        self.assertIsInstance(second, EffectSucceeded)
        operations = [call[0] for call in client.calls]
        self.assertLess(operations.index("materialize"), operations.index("run-container"))
        self.assertEqual(operations.count("materialize"), 1)
        self.assertEqual(operations.count("run-container"), 1)
        mount = next(call for call in client.calls if call[0] == "run-container")[2][0]
        self.assertIn("target=/run/secrets/api-token", mount)
        self.assertIn("volume-subpath=content", mount)
        self.assertTrue(mount.endswith(",readonly"))
        self.assertNotIn(SECRET_TEXT, repr(client.calls))
        self.assertNotIn(SECRET_TEXT, repr(first))

    def test_missing_or_denied_reference_fails_before_volume_mutation(self) -> None:
        for resolver, expected_code in (
            (_resolver(values={}), "docker.secret-missing"),
            (_resolver_for_provider("different"), "docker.secret-denied"),
        ):
            with self.subTest(expected_code=expected_code):
                client = SecretClient()
                result = DockerEffectInterpreter(
                    project_name="demo",
                    client=client,
                    secrets=resolver,
                ).execute(_request(StartNode(NodeTarget("api")), _node()))

                self.assertIsInstance(result, EffectFailed)
                self.assertIs(result.failure.category, FailureCategory.TERMINAL)
                self.assertEqual(result.failure.code, expected_code)
                self.assertFalse(
                    any(call[0] in {"create-volume", "materialize", "run-container"} for call in client.calls)
                )
                self.assertNotIn(SECRET_TEXT, repr(result))

    def test_known_container_start_failure_removes_owned_secret_volume(self) -> None:
        client = SecretClient(fail_start=True)
        result = DockerEffectInterpreter(
            project_name="demo",
            client=client,
            secrets=_resolver(),
        ).execute(_request(StartNode(NodeTarget("api")), _node()))

        self.assertIsInstance(result, EffectFailed)
        self.assertEqual(result.failure.code, "docker.operation-failed")
        self.assertIn("remove-volume", [call[0] for call in client.calls])
        self.assertEqual(client.volumes, {})

    def test_uncertain_materialization_preserves_owned_volume_for_recovery(self) -> None:
        client = SecretClient(fail_materialization=True)
        result = DockerEffectInterpreter(
            project_name="demo",
            client=client,
            secrets=_resolver(),
        ).execute(_request(StartNode(NodeTarget("api")), _node()))

        self.assertIsInstance(result, EffectFailed)
        self.assertIs(result.failure.category, FailureCategory.UNCERTAIN)
        self.assertEqual(result.failure.code, "docker.postcondition-unknown")
        self.assertEqual(len(client.volumes), 1)
        self.assertNotIn("run-container", [call[0] for call in client.calls])

    def test_teardown_preflights_secret_volume_before_container_removal(self) -> None:
        client = SecretClient()
        interpreter = DockerEffectInterpreter(
            project_name="demo",
            client=client,
            secrets=_resolver(),
        )
        node = _node()
        self.assertIsInstance(
            interpreter.execute(_request(StartNode(NodeTarget("api")), node)),
            EffectSucceeded,
        )
        client.calls.clear()

        result = interpreter.execute(
            _request(RemoveNodeResource(NodeTarget("api")), node, graph_id="base")
        )

        self.assertIsInstance(result, EffectSucceeded)
        operations = [call[0] for call in client.calls]
        self.assertLess(operations.index("inspect-volume"), operations.index("remove-container"))
        self.assertLess(operations.index("remove-container"), operations.index("remove-volume"))
        self.assertEqual(client.volumes, {})

    def test_reference_rotation_changes_container_and_volume_ownership(self) -> None:
        first = plan_docker_effect(
            _request(StartNode(NodeTarget("api")), _node()),
            project_name="demo",
        )
        second = plan_docker_effect(
            _request(
                StartNode(NodeTarget("api")),
                _node(reference="secret://test/application/api-token-v2"),
            ),
            project_name="demo",
        )

        self.assertNotEqual(first.ownership.intent_fingerprint, second.ownership.intent_fingerprint)
        self.assertNotEqual(
            first.secret_mounts[0].ownership.intent_fingerprint,
            second.secret_mounts[0].ownership.intent_fingerprint,
        )
        self.assertNotIn(SECRET_TEXT, repr(first))
        self.assertNotIn(SECRET_TEXT, repr(second))

    def test_same_immutable_reference_does_not_rewrite_provider_side_change(self) -> None:
        client = SecretClient()
        request = _request(StartNode(NodeTarget("api")), _node())
        first = DockerEffectInterpreter(
            project_name="demo",
            client=client,
            secrets=_resolver(),
        ).execute(request)
        changed_provider = LocalDevelopmentSecretResolver(
            SecretProviderAuthority(SecretProviderId("test")),
            {"secret://test/application/api-token": "changed-behind-same-reference"},
        )

        replay = DockerEffectInterpreter(
            project_name="demo",
            client=client,
            secrets=changed_provider,
        ).execute(request)

        self.assertIsInstance(first, EffectSucceeded)
        self.assertIsInstance(replay, EffectSucceeded)
        self.assertEqual(
            [call[0] for call in client.calls].count("materialize"),
            1,
        )

    def test_teardown_ownership_conflict_preserves_container_and_volume(self) -> None:
        client = SecretClient()
        interpreter = DockerEffectInterpreter(
            project_name="demo",
            client=client,
            secrets=_resolver(),
        )
        node = _node()
        self.assertIsInstance(
            interpreter.execute(_request(StartNode(NodeTarget("api")), node)),
            EffectSucceeded,
        )
        volume_name = next(iter(client.volumes))
        current = client.volumes[volume_name]
        client.volumes[volume_name] = replace(current, labels={})
        client.calls.clear()

        result = interpreter.execute(
            _request(RemoveNodeResource(NodeTarget("api")), node, graph_id="base")
        )

        self.assertIsInstance(result, EffectFailed)
        self.assertEqual(result.failure.code, "docker.ownership-conflict")
        self.assertIn("demo-docker-api", client.containers)
        self.assertIn(volume_name, client.volumes)
        self.assertNotIn("remove-container", [call[0] for call in client.calls])

    def test_cli_materialization_passes_value_only_through_stdin(self) -> None:
        class RecordingCli(DockerCliClient):
            recorded: tuple[str, tuple[str, ...]] | None = None

            def _run_with_input(self, input_text, *args, **kwargs):
                self.recorded = (input_text, args)
                return subprocess.CompletedProcess(args, 0)

        client = RecordingCli()
        client.materialize_secret_file(
            "secret-volume",
            SecretValue(SECRET_TEXT),
            SecretFileMode.OWNER_READ_ONLY,
        )

        self.assertIsNotNone(client.recorded)
        input_text, args = client.recorded
        self.assertEqual(input_text, SECRET_TEXT)
        self.assertNotIn(SECRET_TEXT, args)
        self.assertIn("--network", args)
        self.assertIn("none", args)
        self.assertIn("--read-only", args)
        self.assertIn("no-new-privileges", args)
        self.assertEqual(args[-1], "0400")


def _resolver(
    *,
    values: dict[str, str] | None = None,
) -> LocalDevelopmentSecretResolver:
    return LocalDevelopmentSecretResolver(
        SecretProviderAuthority(SecretProviderId("test")),
        {
            "secret://test/application/api-token": SECRET_TEXT,
            **({} if values is None else values),
        }
        if values is None
        else values,
    )


def _resolver_for_provider(provider: str) -> LocalDevelopmentSecretResolver:
    return LocalDevelopmentSecretResolver(
        SecretProviderAuthority(SecretProviderId(provider)),
        {},
    )


def _node(
    *,
    reference: str = "secret://test/application/api-token",
) -> NodeMaterial:
    runtime = RuntimeMaterial(
        "docker",
        RuntimeKind.DOCKER,
        ("api",),
        "deployment-network",
    )
    return NodeMaterial(
        "api",
        runtime,
        ImplementationMaterial(
            "docker-image",
            image="python:3.14-slim",
            command=("python", "-V"),
            secret_files=(
                SecretFileMaterial(
                    reference,
                    "/run/secrets/api-token",
                    SecretFileMode.OWNER_READ_ONLY,
                ),
            ),
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
