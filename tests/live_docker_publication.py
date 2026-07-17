"""Live Docker host-publication proof invoked by ``live-test.sh``."""

from __future__ import annotations

from ipaddress import IPv4Address
import sys

from control_plane_kit import (
    ActivityId,
    EffectIdentity,
    EffectRequest,
    EffectSucceeded,
    EndpointMaterial,
    EndpointScope,
    EnvironmentBindingMaterial,
    HostPublicationMaterial,
    ImplementationMaterial,
    LiteralEndpointMaterial,
    LiteralMaterialValue,
    MaterializedEffectRequest,
    NodeMaterial,
    NodeTarget,
    PinnedGraphSet,
    Protocol,
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
from control_plane_kit.servers.hello import hello_command


PROJECT = "cpk-live-publication"


def main(mode: str) -> None:
    interpreter = DockerEffectInterpreter(project_name=PROJECT)
    if mode == "start":
        _require_success(interpreter.execute(_request(StartRuntime(RuntimeTarget("docker")), _runtime())))
        result = interpreter.execute(_request(StartNode(NodeTarget("hello")), _node()))
        _require_success(result)
        publications = result.evidence.descriptor()["host_publications"]
        if len(publications) != 1:
            raise RuntimeError("live Docker start did not report exactly one host publication")
        print(publications[0]["host_port"])
        return
    if mode == "cleanup":
        actions = (
            (StopNode(NodeTarget("hello")), _node()),
            (RemoveNodeResource(NodeTarget("hello")), _node()),
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
        ("hello",),
        f"{PROJECT}-network",
    )


def _node() -> NodeMaterial:
    environment = (
        EnvironmentBindingMaterial(
            "HELLO_MESSAGE",
            LiteralMaterialValue("Hello, published world!"),
        ),
    )
    return NodeMaterial(
        "hello",
        _runtime(),
        ImplementationMaterial(
            "docker-image",
            image="python:3.13-alpine",
            command=hello_command(),
            environment=environment,
            host_publications=(
                HostPublicationMaterial(
                    "internal",
                    Protocol.HTTP,
                    8000,
                    IPv4Address("127.0.0.1"),
                ),
            ),
        ),
        (
            EndpointMaterial(
                "internal",
                Protocol.HTTP,
                EndpointScope.PRIVATE,
                LiteralEndpointMaterial("http://docker-hello:8000"),
            ),
        ),
        environment,
        "/",
    )


def _request(action, material, *, graph_id: str = "desired") -> MaterializedEffectRequest:
    name = type(action).__name__
    return MaterializedEffectRequest(
        EffectRequest(
            EffectIdentity("live-run", ActivityId(name), 1, f"live:{name}:1"),
            action,
        ),
        PinnedGraphSet("live-workspace", "live-plan", "base", "desired"),
        graph_id,
        material,
    )


def _require_success(result) -> None:
    if not isinstance(result, EffectSucceeded):
        raise RuntimeError(f"live Docker effect failed: {result!r}")


if __name__ == "__main__":
    main(sys.argv[1])
