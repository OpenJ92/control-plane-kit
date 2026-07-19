"""Live Docker proof for graph-pinned runtime-only secret files."""

from __future__ import annotations

import sys

from control_plane_kit import (
    ActivityId,
    DockerEffectInterpreter,
    EffectFailed,
    EffectIdentity,
    EffectRequest,
    EffectSucceeded,
    ImplementationMaterial,
    LocalDevelopmentSecretResolver,
    MaterializedEffectRequest,
    NodeMaterial,
    NodeTarget,
    PinnedGraphSet,
    RemoveNodeResource,
    RemoveRuntimeResource,
    RuntimeKind,
    RuntimeMaterial,
    RuntimeTarget,
    SecretFileMaterial,
    SecretFileMode,
    SecretProviderAuthority,
    SecretProviderId,
    StartNode,
    StartRuntime,
    StopNode,
    StopRuntime,
)


PROJECT = "cpk-live-secret"
REFERENCE = "secret://live/application/fixture-token"
FIXTURE_VALUE = "live-secret-fixture-value"


def main(mode: str) -> None:
    if mode == "denied":
        result = DockerEffectInterpreter(
            project_name=PROJECT,
            secrets=LocalDevelopmentSecretResolver(
                SecretProviderAuthority(SecretProviderId("different")),
                {},
            ),
        ).execute(_request(StartNode(NodeTarget("consumer")), _node()))
        if not isinstance(result, EffectFailed):
            raise RuntimeError("unauthorized secret reference did not fail closed")
        if result.failure.code != "docker.secret-denied":
            raise RuntimeError("unauthorized secret reference returned the wrong failure")
        return

    interpreter = DockerEffectInterpreter(
        project_name=PROJECT,
        secrets=LocalDevelopmentSecretResolver(
            SecretProviderAuthority(SecretProviderId("live")),
            {REFERENCE: FIXTURE_VALUE},
        ),
    )
    if mode == "start":
        _require_success(
            interpreter.execute(
                _request(StartRuntime(RuntimeTarget("docker")), _runtime())
            )
        )
        _require_success(
            interpreter.execute(_request(StartNode(NodeTarget("consumer")), _node()))
        )
        return
    if mode == "cleanup":
        for action, material in (
            (StopNode(NodeTarget("consumer")), _node()),
            (RemoveNodeResource(NodeTarget("consumer")), _node()),
            (StopRuntime(RuntimeTarget("docker")), _runtime()),
            (RemoveRuntimeResource(RuntimeTarget("docker")), _runtime()),
        ):
            _require_success(
                interpreter.execute(_request(action, material, graph_id="base"))
            )
        return
    raise ValueError(f"unknown live Docker mode: {mode}")


def _runtime() -> RuntimeMaterial:
    return RuntimeMaterial(
        "docker",
        RuntimeKind.DOCKER,
        ("consumer",),
        f"{PROJECT}-network",
    )


def _node() -> NodeMaterial:
    script = (
        "from pathlib import Path\n"
        "from time import sleep\n"
        "value = Path('/run/secrets/fixture-token').read_bytes()\n"
        "if not value: raise SystemExit(3)\n"
        "Path('/tmp/secret-ready').write_text('ready')\n"
        "sleep(3600)\n"
    )
    return NodeMaterial(
        "consumer",
        _runtime(),
        ImplementationMaterial(
            "docker-image",
            image="python:3.14-slim",
            command=("python", "-B", "-c", script),
            secret_files=(
                SecretFileMaterial(
                    REFERENCE,
                    "/run/secrets/fixture-token",
                    SecretFileMode.OWNER_READ_ONLY,
                ),
            ),
        ),
        (),
        (),
        None,
    )


def _request(action, material, *, graph_id: str = "desired") -> MaterializedEffectRequest:
    name = type(action).__name__
    return MaterializedEffectRequest(
        EffectRequest(
            EffectIdentity("secret-run", ActivityId(name), 1, f"secret:{name}:1"),
            action,
        ),
        PinnedGraphSet("secret-workspace", "secret-plan", "base", "desired"),
        graph_id,
        material,
    )


def _require_success(result) -> None:
    if not isinstance(result, EffectSucceeded):
        raise RuntimeError(f"live Docker secret effect failed: {result!r}")


if __name__ == "__main__":
    main(sys.argv[1])
