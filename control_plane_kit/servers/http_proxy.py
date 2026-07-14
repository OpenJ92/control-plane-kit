"""HTTP proxy server block and in-memory behavior model."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

from control_plane_kit.algebra import BlockSockets, BlockSpec, ProviderSocket, ProxyBlock, RequirementSocket
from control_plane_kit.capabilities import CapabilityName
from control_plane_kit.contracts import RuntimeContract, RuntimeValueVariable
from control_plane_kit.implementations import DockerImageImplementation
from control_plane_kit.servers.http_messages import HttpHandler, HttpRequest, HttpResponse
from control_plane_kit.types import Protocol


class HttpProxyRuntime(RuntimeContract):
    """Runtime contract for one-target HTTP proxy state."""

    target = RuntimeValueVariable("target", required=True)


@dataclass
class HttpProxyServer:
    """In-memory HTTP proxy behavior used by tests and examples.

    The proxy forwards method, path, query, headers, and body unchanged to the
    active target handler and returns the target response.
    """

    targets: Mapping[str, HttpHandler]
    target: str
    runtime: HttpProxyRuntime = field(init=False)

    def __post_init__(self) -> None:
        self.runtime = HttpProxyRuntime.from_mapping({"target": self.target})
        self._require_target(self.target)

    def set_target(self, target: str) -> None:
        self._require_target(target)
        self.runtime.apply_patch({"target": target})

    def handle(self, request: HttpRequest) -> HttpResponse:
        target = str(self.runtime.get("target"))
        self._require_target(target)
        return self.targets[target](request)

    def _require_target(self, target: str) -> None:
        if target not in self.targets:
            raise KeyError(f"unknown target {target!r}")


def http_proxy_block(
    block_id: str = "http-proxy",
    *,
    display_name: str = "HTTP Proxy",
    image: str = "python:3.13-alpine",
) -> ProxyBlock:
    """Return a Docker-backed HTTP proxy block.

    The target URL is supplied by a socket connection through `PROXY_TARGET_URL`.
    """

    return ProxyBlock(
        BlockSpec(
            block_id,
            display_name,
            health_path="/",
            capabilities=(CapabilityName.HEALTH_CHECKABLE, CapabilityName.TARGET_MUTABLE),
            metadata={"behavior": "http-proxy"},
        ),
        DockerImageImplementation(
            image=image,
            command=http_proxy_command(),
            ports={"internal": 8080},
        ),
        BlockSockets(
            requirements=(RequirementSocket("target", Protocol.HTTP, ("PROXY_TARGET_URL",)),),
            providers=(ProviderSocket("internal", Protocol.HTTP),),
        ),
    )


def http_proxy_command() -> tuple[str, ...]:
    """Return a tiny stdlib HTTP proxy command for Docker examples."""

    lines = [
        "import os, urllib.request",
        "from http.server import BaseHTTPRequestHandler, HTTPServer",
        "TARGET = os.environ['PROXY_TARGET_URL'].rstrip('/')",
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
