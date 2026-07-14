"""HTTP multiplexer server block and in-memory behavior model."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

from control_plane_kit.algebra import BlockSockets, BlockSpec, ProviderSocket, ProxyBlock, RequirementSocket
from control_plane_kit.capabilities import CapabilityName
from control_plane_kit.contracts import RuntimeContract, RuntimeMapVariable, RuntimeValueVariable
from control_plane_kit.implementations import DockerImageImplementation
from control_plane_kit.servers.http_messages import HttpHandler, HttpRequest, HttpResponse
from control_plane_kit.types import Protocol


class HttpMultiplexerRuntime(RuntimeContract):
    """Runtime contract for one-primary, many-observer HTTP multiplexing."""

    primary_target = RuntimeValueVariable("primary_target", required=True)
    observers = RuntimeMapVariable("observers", required=False)


@dataclass
class HttpMultiplexerServer:
    """In-memory multiplexer behavior used by tests and examples.

    The primary target receives the request and owns the response. Observers
    receive the same immutable request value as side effects. Observer failures
    are recorded but do not fail the primary response path.
    """

    targets: Mapping[str, HttpHandler]
    primary_target: str
    observers: Mapping[str, HttpHandler] = field(default_factory=dict)
    runtime: HttpMultiplexerRuntime = field(init=False)
    observer_errors: list[str] = field(init=False, default_factory=list)

    def __post_init__(self) -> None:
        self.runtime = HttpMultiplexerRuntime.from_mapping({
            "primary_target": self.primary_target,
            "observers": {key: key for key in self.observers},
        })
        self._require_primary(self.primary_target)

    def set_primary_target(self, target: str) -> None:
        self._require_primary(target)
        self.runtime.apply_patch({"primary_target": target})

    def replace_observers(self, observers: Mapping[str, HttpHandler]) -> None:
        self.observers = dict(observers)
        self.runtime.apply_patch({"observers": {key: key for key in self.observers}})

    def handle(self, request: HttpRequest) -> HttpResponse:
        primary_target = str(self.runtime.get("primary_target"))
        self._require_primary(primary_target)
        response = self.targets[primary_target](request)
        for observer_id, observer in self.observers.items():
            try:
                observer(request)
            except Exception as exc:  # noqa: BLE001 - observers fail open by design here.
                self.observer_errors.append(f"{observer_id}: {exc}")
        return response

    def _require_primary(self, target: str) -> None:
        if target not in self.targets:
            raise KeyError(f"unknown primary target {target!r}")


def http_multiplexer_block(
    block_id: str = "http-multiplexer",
    *,
    display_name: str = "HTTP Multiplexer",
    image: str = "python:3.13-alpine",
) -> ProxyBlock:
    """Return a Docker-backed HTTP multiplexer block.

    The demo Docker command accepts one primary URL and up to two observer URLs.
    Observers receive copied request data; the client receives only the primary
    target response.
    """

    return ProxyBlock(
        BlockSpec(
            block_id,
            display_name,
            health_path="/",
            capabilities=(
                CapabilityName.HEALTH_CHECKABLE,
                CapabilityName.TARGET_MUTABLE,
                CapabilityName.OBSERVER_MUTABLE,
            ),
            metadata={"behavior": "http-multiplexer"},
        ),
        DockerImageImplementation(
            image=image,
            command=http_multiplexer_command(),
            ports={"internal": 8080},
        ),
        BlockSockets(
            requirements=(
                RequirementSocket("primary", Protocol.HTTP, ("MULTIPLEXER_PRIMARY_URL",)),
                RequirementSocket("observer-a", Protocol.HTTP, ("MULTIPLEXER_OBSERVER_A_URL",)),
                RequirementSocket("observer-b", Protocol.HTTP, ("MULTIPLEXER_OBSERVER_B_URL",)),
            ),
            providers=(ProviderSocket("internal", Protocol.HTTP),),
        ),
    )


def http_multiplexer_command() -> tuple[str, ...]:
    """Return a tiny stdlib HTTP multiplexer command for Docker examples."""

    lines = [
        "import os, urllib.error, urllib.request",
        "from http.server import BaseHTTPRequestHandler, HTTPServer",
        "PRIMARY = os.environ['MULTIPLEXER_PRIMARY_URL'].rstrip('/')",
        "OBSERVERS = [value.rstrip('/') for value in (os.environ.get('MULTIPLEXER_OBSERVER_A_URL'), os.environ.get('MULTIPLEXER_OBSERVER_B_URL')) if value]",
        "class Handler(BaseHTTPRequestHandler):",
        "    def _request(self, target, body, headers):",
        "        return urllib.request.Request(target + self.path, data=body or None, headers=headers, method=self.command)",
        "    def _forward(self):",
        "        body = self.rfile.read(int(self.headers.get('content-length', '0') or '0'))",
        "        headers = {key: value for key, value in self.headers.items() if key.lower() != 'host'}",
        "        with urllib.request.urlopen(self._request(PRIMARY, body, headers)) as response:",
        "            payload = response.read()",
        "            status = response.status",
        "            response_headers = [(key, value) for key, value in response.headers.items() if key.lower() not in {'transfer-encoding', 'connection'}]",
        "        for observer in OBSERVERS:",
        "            try:",
        "                urllib.request.urlopen(self._request(observer, body, headers), timeout=1).read()",
        "            except Exception:",
        "                pass",
        "        self.send_response(status)",
        "        for key, value in response_headers:",
        "            self.send_header(key, value)",
        "        self.end_headers()",
        "        self.wfile.write(payload)",
        "    def do_GET(self): self._forward()",
        "    def do_POST(self): self._forward()",
        "    def do_PUT(self): self._forward()",
        "    def do_DELETE(self): self._forward()",
        "    def log_message(self, format, *args): pass",
        "HTTPServer(('0.0.0.0', 8080), Handler).serve_forever()",
    ]
    return ("python", "-c", chr(10).join(lines))
