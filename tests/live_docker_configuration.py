"""Live Docker proof for graph-pinned read-only configuration artifacts."""

from __future__ import annotations

import sys

from control_plane_kit import (
    ActivityId,
    ConfigurationArtifact,
    ConfigurationFileMode,
    ConfigurationMediaType,
    EffectIdentity,
    EffectRequest,
    EffectSucceeded,
    ImplementationMaterial,
    MaterializedEffectRequest,
    NodeMaterial,
    NodeTarget,
    PinnedGraphSet,
    RemoveNodeResource,
    RemoveRuntimeResource,
    RuntimeKind,
    RuntimeMaterial,
    RuntimeTarget,
    StartNode,
    StartRuntime,
    StopNode,
    StopRuntime,
)
from control_plane_kit.docker_runtime import DockerEffectInterpreter


PROJECT = "cpk-live-configuration"
CONTENT = '{"message":"configuration-live"}\n'


def main(mode: str) -> None:
    interpreter = DockerEffectInterpreter(project_name=PROJECT)
    if mode == "start":
        _require_success(
            interpreter.execute(
                _request(StartRuntime(RuntimeTarget("docker")), _runtime())
            )
        )
        result = interpreter.execute(
            _request(StartNode(NodeTarget("configured")), _node())
        )
        _require_success(result)
        artifacts = result.evidence.descriptor()["configuration_artifacts"]
        if set(artifacts) != {"service-config"}:
            raise RuntimeError("live Docker start did not report pinned configuration")
        return
    if mode == "cleanup":
        actions = (
            (StopNode(NodeTarget("configured")), _node()),
            (RemoveNodeResource(NodeTarget("configured")), _node()),
            (StopRuntime(RuntimeTarget("docker")), _runtime()),
            (RemoveRuntimeResource(RuntimeTarget("docker")), _runtime()),
        )
        for action, material in actions:
            _require_success(
                interpreter.execute(_request(action, material, graph_id="base"))
            )
        return
    raise ValueError(f"unknown live Docker mode: {mode}")


def _runtime() -> RuntimeMaterial:
    return RuntimeMaterial(
        "docker",
        RuntimeKind.DOCKER,
        ("configured",),
        f"{PROJECT}-network",
    )


def _node() -> NodeMaterial:
    artifact = ConfigurationArtifact(
        "service-config",
        "/etc/service/config.json",
        ConfigurationMediaType.JSON,
        CONTENT,
        ConfigurationFileMode.READ_ONLY,
    )
    return NodeMaterial(
        "configured",
        _runtime(),
        ImplementationMaterial(
            "docker-image",
            image="python:3.14-slim",
            command=("sleep", "3600"),
            configuration_artifacts=(artifact,),
        ),
        (),
        (),
        None,
    )


def _request(action, material, *, graph_id: str = "desired") -> MaterializedEffectRequest:
    name = type(action).__name__
    return MaterializedEffectRequest(
        EffectRequest(
            EffectIdentity(
                "configuration-run",
                ActivityId(name),
                1,
                f"configuration:{name}:1",
            ),
            action,
        ),
        PinnedGraphSet(
            "configuration-workspace",
            "configuration-plan",
            "base",
            "desired",
        ),
        graph_id,
        material,
    )


def _require_success(result) -> None:
    if not isinstance(result, EffectSucceeded):
        raise RuntimeError(f"live Docker configuration effect failed: {result!r}")


if __name__ == "__main__":
    main(sys.argv[1])
