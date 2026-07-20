"""Live Docker proof for explicit TCP and UDP runtime material."""

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
    HostPublicationMaterial,
    ImplementationMaterial,
    LiteralEndpointMaterial,
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


PROJECT = "cpk-live-transport"
FIXTURE_IMAGE = "control-plane-kit-transport-fixture:local"
PORT = 5353


def main(mode: str) -> None:
    interpreter = DockerEffectInterpreter(project_name=PROJECT)
    if mode == "start":
        _require_success(
            interpreter.execute(
                _request(StartRuntime(RuntimeTarget("docker")), _runtime())
            )
        )
        result = interpreter.execute(_request(StartNode(NodeTarget("dns")), _node()))
        _require_success(result)
        publications = result.evidence.descriptor()["host_publications"]
        actual = {
            (value["container_port"], value["transport"])
            for value in publications
        }
        expected = {(PORT, "tcp"), (PORT, "udp")}
        if actual != expected:
            raise RuntimeError(
                f"live Docker publications were {actual!r}, expected {expected!r}"
            )
        return
    if mode == "cleanup":
        actions = (
            (StopNode(NodeTarget("dns")), _node()),
            (RemoveNodeResource(NodeTarget("dns")), _node()),
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
        ("dns",),
        f"{PROJECT}-network",
    )


def _node() -> NodeMaterial:
    return NodeMaterial(
        "dns",
        _runtime(),
        ImplementationMaterial(
            "docker-image",
            image=FIXTURE_IMAGE,
            host_publications=(
                HostPublicationMaterial(
                    "dns-tcp",
                    Protocol.DNS_TCP,
                    PORT,
                    IPv4Address("127.0.0.1"),
                ),
                HostPublicationMaterial(
                    "dns-udp",
                    Protocol.DNS_UDP,
                    PORT,
                    IPv4Address("127.0.0.1"),
                ),
            ),
        ),
        (
            EndpointMaterial(
                "dns-tcp",
                Protocol.DNS_TCP,
                EndpointScope.PRIVATE,
                LiteralEndpointMaterial(f"dns+tcp://{PROJECT}-docker-dns:{PORT}"),
            ),
            EndpointMaterial(
                "dns-udp",
                Protocol.DNS_UDP,
                EndpointScope.PRIVATE,
                LiteralEndpointMaterial(f"dns+udp://{PROJECT}-docker-dns:{PORT}"),
            ),
        ),
        (),
        None,
    )


def _request(action, material, *, graph_id: str = "desired") -> MaterializedEffectRequest:
    name = type(action).__name__
    return MaterializedEffectRequest(
        EffectRequest(
            EffectIdentity("transport-run", ActivityId(name), 1, f"transport:{name}:1"),
            action,
        ),
        PinnedGraphSet("transport-workspace", "transport-plan", "base", "desired"),
        graph_id,
        material,
    )


def _require_success(result) -> None:
    if not isinstance(result, EffectSucceeded):
        raise RuntimeError(f"live Docker transport effect failed: {result!r}")


if __name__ == "__main__":
    main(sys.argv[1])
