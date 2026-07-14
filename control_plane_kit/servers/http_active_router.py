"""HTTP active-router server block and in-memory behavior model."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

from control_plane_kit.algebra import BlockSockets, BlockSpec, ProviderSocket, ProxyBlock, RequirementSocket
from control_plane_kit.capabilities import CapabilityName
from control_plane_kit.contracts import RuntimeContract, RuntimeMapVariable, RuntimeValueVariable
from control_plane_kit.implementations import DockerImageImplementation
from control_plane_kit.servers.http_messages import HttpHandler, HttpRequest, HttpResponse
from control_plane_kit.types import Protocol


class HttpActiveRouterRuntime(RuntimeContract):
    """Runtime contract for HTTP active-target routing."""

    targets = RuntimeMapVariable("targets", required=True)
    active_target = RuntimeValueVariable("active_target", required=True)


@dataclass
class HttpActiveRouterServer:
    """In-memory active router behavior used by tests and examples."""

    targets: Mapping[str, HttpHandler]
    active_target: str
    runtime: HttpActiveRouterRuntime = field(init=False)

    def __post_init__(self) -> None:
        self.runtime = HttpActiveRouterRuntime.from_mapping({
            "targets": {key: key for key in self.targets},
            "active_target": self.active_target,
        })
        self._require_target(self.active_target)

    def set_active_target(self, target: str) -> None:
        self._require_target(target)
        self.runtime.apply_patch({"active_target": target})

    def replace_targets(self, targets: Mapping[str, HttpHandler], *, active_target: str | None = None) -> None:
        next_active = active_target or str(self.runtime.get("active_target"))
        if next_active not in targets:
            next_active = ""
        self.targets = dict(targets)
        self.runtime.apply_patch({
            "targets": {key: key for key in self.targets},
            "active_target": next_active,
        })

    def handle(self, request: HttpRequest) -> HttpResponse:
        target = str(self.runtime.get("active_target"))
        self._require_target(target)
        return self.targets[target](request)

    def _require_target(self, target: str) -> None:
        if target not in self.targets:
            raise KeyError(f"unknown target {target!r}")


def http_active_router_block(
    block_id: str = "http-active-router",
    *,
    display_name: str = "HTTP Active Router",
    image: str = "python:3.13-alpine",
) -> ProxyBlock:
    """Return a Docker-backed HTTP active router block.

    The initial active target URL is supplied by a socket connection through
    `ACTIVE_TARGET_URL`. Runtime control routes can later switch targets in
    richer server implementations.
    """

    return ProxyBlock(
        BlockSpec(
            block_id,
            display_name,
            health_path="/",
            capabilities=(
                CapabilityName.HEALTH_CHECKABLE,
                CapabilityName.TARGET_MUTABLE,
                CapabilityName.SWITCHABLE,
                CapabilityName.DRAINABLE,
            ),
            metadata={"behavior": "http-active-router"},
        ),
        DockerImageImplementation(
            image=image,
            command=http_active_router_command(),
            ports={"internal": 8080},
        ),
        BlockSockets(
            requirements=(RequirementSocket("active", Protocol.HTTP, ("ACTIVE_TARGET_URL",)),),
            providers=(ProviderSocket("internal", Protocol.HTTP),),
        ),
    )


def http_active_router_command() -> tuple[str, ...]:
    """Return a tiny stdlib HTTP active-router command for Docker examples."""

    lines = [
        "import os, urllib.request",
        "from http.server import BaseHTTPRequestHandler, HTTPServer",
        "TARGET = os.environ['ACTIVE_TARGET_URL'].rstrip('/')",
        "class Handler(BaseHTTPRequestHandler):",
        "    def _forward(self):",
        "        body = self.rfile.read(int(self.headers.get('content-length', '0') or '0'))",
        "        url = TARGET + self.path",
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
