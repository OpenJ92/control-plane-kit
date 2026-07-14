"""HTTP weighted load-balancer server block and in-memory behavior model."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

from control_plane_kit.algebra import BlockSockets, BlockSpec, ProviderSocket, ProxyBlock, RequirementSocket
from control_plane_kit.capabilities import CapabilityName
from control_plane_kit.contracts import RuntimeContract, RuntimeMapVariable
from control_plane_kit.implementations import DockerImageImplementation
from control_plane_kit.servers.http_messages import HttpHandler, HttpRequest, HttpResponse
from control_plane_kit.types import Protocol


class HttpWeightedLoadBalancerRuntime(RuntimeContract):
    """Runtime contract for HTTP weighted target routing."""

    targets = RuntimeMapVariable("targets", required=True)
    weights = RuntimeMapVariable("weights", required=True)


@dataclass
class HttpWeightedLoadBalancerServer:
    """In-memory weighted balancer behavior used by tests and examples.

    The balancer expands positive integer weights into a deterministic cycle.
    This gives tests and demos stable behavior while still representing the
    operational object: one provider endpoint distributing traffic to targets.
    """

    targets: Mapping[str, HttpHandler]
    weights: Mapping[str, int]
    runtime: HttpWeightedLoadBalancerRuntime = field(init=False)
    _sequence: tuple[str, ...] = field(init=False, default=())
    _cursor: int = field(init=False, default=0)

    def __post_init__(self) -> None:
        self.runtime = HttpWeightedLoadBalancerRuntime.from_mapping({
            "targets": {key: key for key in self.targets},
            "weights": dict(self.weights),
        })
        self._sequence = self._weighted_sequence()
        if not self._sequence:
            raise ValueError("weighted load balancer requires at least one positive target weight")

    def replace_weights(self, weights: Mapping[str, int]) -> None:
        self.weights = dict(weights)
        self.runtime.apply_patch({"weights": dict(weights)})
        self._sequence = self._weighted_sequence()
        self._cursor = 0
        if not self._sequence:
            raise ValueError("weighted load balancer requires at least one positive target weight")

    def handle(self, request: HttpRequest) -> HttpResponse:
        target = self._next_target()
        return self.targets[target](request)

    def _next_target(self) -> str:
        target = self._sequence[self._cursor % len(self._sequence)]
        self._cursor += 1
        return target

    def _weighted_sequence(self) -> tuple[str, ...]:
        sequence: list[str] = []
        for target in self.targets:
            weight = int(self.weights.get(target, 0))
            if weight > 0:
                sequence.extend([target] * weight)
        return tuple(sequence)


def http_weighted_load_balancer_block(
    block_id: str = "http-weighted-load-balancer",
    *,
    display_name: str = "HTTP Weighted Load Balancer",
    image: str = "python:3.13-alpine",
) -> ProxyBlock:
    """Return a Docker-backed HTTP weighted load-balancer block.

    The demo Docker command accepts two target URLs via `BALANCER_TARGET_A_URL`
    and `BALANCER_TARGET_B_URL`. Richer generated commands can grow from the
    same runtime contract without changing the block algebra.
    """

    return ProxyBlock(
        BlockSpec(
            block_id,
            display_name,
            health_path="/",
            capabilities=(
                CapabilityName.HEALTH_CHECKABLE,
                CapabilityName.TARGET_MUTABLE,
                CapabilityName.METRICS_READABLE,
            ),
            metadata={"behavior": "http-weighted-load-balancer"},
        ),
        DockerImageImplementation(
            image=image,
            command=http_weighted_load_balancer_command(),
            ports={"internal": 8080},
        ),
        BlockSockets(
            requirements=(
                RequirementSocket("target-a", Protocol.HTTP, ("BALANCER_TARGET_A_URL",)),
                RequirementSocket("target-b", Protocol.HTTP, ("BALANCER_TARGET_B_URL",)),
            ),
            providers=(ProviderSocket("internal", Protocol.HTTP),),
        ),
    )


def http_weighted_load_balancer_command() -> tuple[str, ...]:
    """Return a tiny stdlib two-target HTTP load-balancer command."""

    lines = [
        "import itertools, os, urllib.request",
        "from http.server import BaseHTTPRequestHandler, HTTPServer",
        "TARGETS = [os.environ['BALANCER_TARGET_A_URL'].rstrip('/'), os.environ['BALANCER_TARGET_B_URL'].rstrip('/')]",
        "CYCLE = itertools.cycle(TARGETS)",
        "class Handler(BaseHTTPRequestHandler):",
        "    def _forward(self):",
        "        body = self.rfile.read(int(self.headers.get('content-length', '0') or '0'))",
        "        target = next(CYCLE)",
        "        url = target + self.path",
        "        headers = {key: value for key, value in self.headers.items() if key.lower() != 'host'}",
        "        request = urllib.request.Request(url, data=body or None, headers=headers, method=self.command)",
        "        with urllib.request.urlopen(request) as response:",
        "            payload = response.read()",
        "            self.send_response(response.status)",
        "            for key, value in response.headers.items():",
        "                if key.lower() not in {'transfer-encoding', 'connection'}:",
        "                    self.send_header(key, value)",
        "            self.end_headers()",
        "            self.wfile.write(payload)",
        "    def do_GET(self): self._forward()",
        "    def do_POST(self): self._forward()",
        "    def do_PUT(self): self._forward()",
        "    def do_DELETE(self): self._forward()",
        "    def log_message(self, format, *args): pass",
        "HTTPServer(('0.0.0.0', 8080), Handler).serve_forever()",
    ]
    return ("python", "-c", chr(10).join(lines))
