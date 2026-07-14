"""HTTP rate-limiter server block and in-memory behavior model."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

from control_plane_kit.algebra import BlockSockets, BlockSpec, ProviderSocket, ProxyBlock, RequirementSocket
from control_plane_kit.capabilities import CapabilityName
from control_plane_kit.contracts import RuntimeContract, RuntimeValueVariable
from control_plane_kit.implementations import DockerImageImplementation
from control_plane_kit.servers.http_messages import HttpHandler, HttpRequest, HttpResponse
from control_plane_kit.types import Protocol


class HttpRateLimiterRuntime(RuntimeContract):
    """Runtime contract for quota-gated HTTP forwarding."""

    target = RuntimeValueVariable("target", required=True)
    limit = RuntimeValueVariable("limit", required=True)
    remaining = RuntimeValueVariable("remaining", required=True)


@dataclass
class HttpRateLimiterServer:
    """In-memory rate limiter behavior used by tests and examples.

    This first limiter is deliberately not time-based. It is a deterministic
    gate: allowed requests decrement remaining quota and forward to the target;
    exhausted quota returns a generated 429 response.
    """

    targets: Mapping[str, HttpHandler]
    target: str
    limit: int
    runtime: HttpRateLimiterRuntime = field(init=False)

    def __post_init__(self) -> None:
        if self.limit < 0:
            raise ValueError("rate limit must be non-negative")
        self._require_target(self.target)
        self.runtime = HttpRateLimiterRuntime.from_mapping({
            "target": self.target,
            "limit": self.limit,
            "remaining": self.limit,
        })

    def reset(self, *, limit: int | None = None) -> None:
        next_limit = self.limit if limit is None else limit
        if next_limit < 0:
            raise ValueError("rate limit must be non-negative")
        self.limit = next_limit
        self.runtime.apply_patch({"limit": next_limit, "remaining": next_limit})

    def handle(self, request: HttpRequest) -> HttpResponse:
        remaining = int(self.runtime.get("remaining"))
        if remaining <= 0:
            return HttpResponse.text("Too Many Requests", status_code=429)
        self.runtime.apply_patch({"remaining": remaining - 1})
        target = str(self.runtime.get("target"))
        self._require_target(target)
        return self.targets[target](request)

    def _require_target(self, target: str) -> None:
        if target not in self.targets:
            raise KeyError(f"unknown target {target!r}")


def http_rate_limiter_block(
    block_id: str = "http-rate-limiter",
    *,
    display_name: str = "HTTP Rate Limiter",
    image: str = "python:3.13-alpine",
) -> ProxyBlock:
    """Return a Docker-backed HTTP rate-limiter block."""

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
            metadata={"behavior": "http-rate-limiter"},
        ),
        DockerImageImplementation(
            image=image,
            command=http_rate_limiter_command(),
            ports={"internal": 8080},
        ),
        BlockSockets(
            requirements=(RequirementSocket("target", Protocol.HTTP, ("RATE_LIMIT_TARGET_URL",)),),
            providers=(ProviderSocket("internal", Protocol.HTTP),),
        ),
    )


def http_rate_limiter_command() -> tuple[str, ...]:
    """Return a tiny stdlib quota-gated HTTP forwarding command."""

    lines = [
        "import os, urllib.request",
        "from http.server import BaseHTTPRequestHandler, HTTPServer",
        "TARGET = os.environ['RATE_LIMIT_TARGET_URL'].rstrip('/')",
        "REMAINING = int(os.environ.get('RATE_LIMIT_REQUESTS', '60'))",
        "class Handler(BaseHTTPRequestHandler):",
        "    def _forward(self):",
        "        global REMAINING",
        "        if REMAINING <= 0:",
        "            self.send_response(429)",
        "            self.send_header('content-type', 'text/plain')",
        "            self.end_headers()",
        "            self.wfile.write(b'Too Many Requests')",
        "            return",
        "        REMAINING -= 1",
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
