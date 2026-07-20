"""Graph-pinned live Docker proof for the official CoreDNS integration."""

from __future__ import annotations

from ipaddress import IPv4Address
import os
import socket
import struct
import sys
import urllib.request

from control_plane_kit import (
    ActivityPlan,
    DeploymentGraph,
    DeploymentRecipe,
    DockerRuntime,
    EffectSucceeded,
    HostPublication,
    PinnedGraphSet,
    RemoveNodeResource,
    RemoveRuntimeResource,
    StartNode,
    StartRuntime,
    StopNode,
    StopRuntime,
    compile_activity_plan,
    compile_recipe,
    diff_graphs,
    effect_request_for_activity,
    materialize_effect_request,
    validate_graph,
)
from control_plane_kit.products.servers import (
    CoreDnsConfiguration,
    DnsARecord,
    DnsName,
    coredns_block,
)
from control_plane_kit.docker_runtime import DockerEffectInterpreter


PROJECT = "cpk-live-coredns"
GRAPH_NAME = "coredns-live"
BASE_GRAPH_ID = "coredns-empty"
DESIRED_GRAPH_ID = "coredns-desired"
QUERY_NAME = "orders.cpk.internal."
EXPECTED_ADDRESS = IPv4Address("10.42.0.17")


def main(mode: str) -> None:
    if mode == "verify":
        _verify(os.environ["CPK_COREDNS_HOST"])
        return
    empty = DeploymentGraph(GRAPH_NAME)
    desired = compile_recipe(_recipe())
    interpreter = DockerEffectInterpreter(project_name=PROJECT)
    if mode == "start":
        plan = compile_activity_plan(diff_graphs(validate_graph(empty), validate_graph(desired)))
        results = _execute_types(
            interpreter,
            plan,
            (StartRuntime, StartNode),
            empty,
            desired,
            BASE_GRAPH_ID,
            DESIRED_GRAPH_ID,
        )
        publications = results[-1].evidence.descriptor()["host_publications"]
        actual = {(value["container_port"], value["transport"]) for value in publications}
        if actual != {(53, "tcp"), (53, "udp")}:
            raise RuntimeError(f"CoreDNS publications were not exact: {actual!r}")
        return
    if mode == "cleanup":
        plan = compile_activity_plan(diff_graphs(validate_graph(desired), validate_graph(empty)))
        _execute_types(
            interpreter,
            plan,
            (StopNode, RemoveNodeResource, StopRuntime, RemoveRuntimeResource),
            desired,
            empty,
            DESIRED_GRAPH_ID,
            BASE_GRAPH_ID,
        )
        return
    raise ValueError(f"unknown CoreDNS live mode: {mode}")


def _verify(host: str) -> None:
    for path, port in (("/health", 8080), ("/ready", 8181)):
        with urllib.request.urlopen(f"http://{host}:{port}{path}", timeout=5) as response:
            if response.status != 200 or response.read(16).strip() != b"OK":
                raise RuntimeError(f"CoreDNS endpoint {path} was not ready")
    query = _dns_query(QUERY_NAME)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as client:
        client.settimeout(5)
        client.sendto(query, (host, 53))
        udp_response, _ = client.recvfrom(4096)
    _require_answer(udp_response, query)
    with socket.create_connection((host, 53), timeout=5) as client:
        client.sendall(struct.pack("!H", len(query)) + query)
        length = struct.unpack("!H", _read_exact(client, 2))[0]
        tcp_response = _read_exact(client, length)
    _require_answer(tcp_response, query)


def _dns_query(name: str) -> bytes:
    labels = name.rstrip(".").split(".")
    question = b"".join(bytes((len(label),)) + label.encode("ascii") for label in labels)
    return struct.pack("!HHHHHH", 0x4300, 0x0100, 1, 0, 0, 0) + question + b"\0" + struct.pack("!HH", 1, 1)


def _require_answer(response: bytes, query: bytes) -> None:
    if len(response) > 4096 or len(response) < 12:
        raise RuntimeError("CoreDNS returned malformed or unbounded DNS evidence")
    transaction, flags, _, answers, _, _ = struct.unpack("!HHHHHH", response[:12])
    if transaction != struct.unpack("!H", query[:2])[0] or flags & 0x000F or answers < 1:
        raise RuntimeError("CoreDNS did not return a successful bounded answer")
    if EXPECTED_ADDRESS.packed not in response:
        raise RuntimeError("CoreDNS response did not contain the projected address")


def _read_exact(client: socket.socket, count: int) -> bytes:
    chunks: list[bytes] = []
    remaining = count
    while remaining:
        chunk = client.recv(remaining)
        if not chunk:
            raise RuntimeError("CoreDNS TCP response ended early")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _execute_types(
    interpreter: DockerEffectInterpreter,
    plan: ActivityPlan,
    operation_types: tuple[type, ...],
    base: DeploymentGraph,
    desired: DeploymentGraph,
    base_graph_id: str,
    desired_graph_id: str,
) -> tuple[EffectSucceeded, ...]:
    activities = {type(activity.operation): activity for activity in plan.activities}
    results: list[EffectSucceeded] = []
    for attempt, operation_type in enumerate(operation_types, start=1):
        activity = activities[operation_type]
        request = effect_request_for_activity(
            activity,
            run_id="coredns-live-run",
            attempt=attempt,
            idempotency_key=f"coredns-live:{operation_type.__name__}:{attempt}",
        )
        materialized = materialize_effect_request(
            request,
            activity,
            PinnedGraphSet(
                "coredns-live-workspace",
                "coredns-live-plan",
                base_graph_id,
                desired_graph_id,
            ),
            base_graph_id=base_graph_id,
            base_graph=base,
            desired_graph_id=desired_graph_id,
            desired_graph=desired,
        )
        result = interpreter.execute(materialized)
        if not isinstance(result, EffectSucceeded):
            raise RuntimeError(f"CoreDNS live {operation_type.__name__} failed: {result!r}")
        results.append(result)
    return tuple(results)


def _recipe() -> DeploymentRecipe:
    configuration = CoreDnsConfiguration(
        DnsName("cpk.internal"),
        (DnsARecord(DnsName(QUERY_NAME), EXPECTED_ADDRESS),),
    )
    return DeploymentRecipe(
        GRAPH_NAME,
        DockerRuntime(
            runtime_id="docker",
            network_name=f"{PROJECT}-network",
            children=(
                coredns_block(
                    configuration=configuration,
                    host_publications={
                        "dns-tcp": HostPublication.loopback_v4(),
                        "dns-udp": HostPublication.loopback_v4(),
                    },
                ),
            ),
        ),
    )


if __name__ == "__main__":
    main(sys.argv[1])
