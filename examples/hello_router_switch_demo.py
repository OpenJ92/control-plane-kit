"""Live Docker demo: switch one router between two hello app targets.

Run from the repository root after building the local test image:

    docker build --target test -t control-plane-kit:test .
    python3 -m examples.hello_router_switch_demo

The demo constructs a topology with the package algebra, compiles it to a graph,
starts two parameterized hello application containers and one router container,
registers target slugs through router-specific REST routes, and proves that the
same GET /hello request returns different values after switching.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

from control_plane_kit import (
    DeploymentRecipe,
    DockerImageImplementation,
    DockerRuntime,
    SocketConnection,
    compile_recipe,
    hello_application_block,
    http_active_router_block,
)

DEFAULT_IMAGE = "control-plane-kit:test"
DEFAULT_NETWORK = "cpk-hello-demo"
DEFAULT_PORT = 18080
ROUTER_ID = "hello-router"
EARTH_ID = "hello-earth"
MARS_ID = "hello-mars"


def recipe(image: str = DEFAULT_IMAGE) -> DeploymentRecipe:
    """Return the algebraic topology used by the live demo."""

    hello_earth = hello_application_block(
        EARTH_ID,
        world="earth",
        implementation=DockerImageImplementation(image, ports={"internal": 8000}),
    )
    hello_mars = hello_application_block(
        MARS_ID,
        world="mars",
        implementation=DockerImageImplementation(image, ports={"internal": 8000}),
    )
    router = http_active_router_block(
        ROUTER_ID,
        implementation=DockerImageImplementation(image, ports={"internal": 8000}),
    )
    return DeploymentRecipe(
        "hello-router-switch-demo",
        DockerRuntime(
            children=(
                hello_earth,
                hello_mars,
                router,
                SocketConnection(EARTH_ID, "internal", ROUTER_ID, "targets"),
                SocketConnection(MARS_ID, "internal", ROUTER_ID, "targets"),
            )
        ),
    )


@dataclass(frozen=True)
class DemoConfig:
    image: str = DEFAULT_IMAGE
    network: str = DEFAULT_NETWORK
    host_port: int = DEFAULT_PORT
    keep_running: bool = True

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.host_port}"


class HelloRouterDemoRunner:
    """Small concrete Docker runner for the hello/router switch recipe."""

    def __init__(self, config: DemoConfig):
        self.config = config
        self.graph = compile_recipe(recipe(config.image))

    def run(self) -> None:
        self.reset()
        try:
            self.start()
            self.wait_for_router()
            self.register_target(EARTH_ID)
            self.switch(EARTH_ID)
            earth = self.get_hello()
            self.register_target(MARS_ID)
            still_earth = self.get_hello()
            self.switch(MARS_ID)
            mars = self.get_hello()
            print(json.dumps({
                "router": self.config.base_url,
                "before_switch": earth,
                "after_adding_mars_inactive": still_earth,
                "after_switch": mars,
                "status": self.get_json("/__deploy/status"),
                "targets": self.get_json("/__deploy/targets"),
            }, indent=2, sort_keys=True))
        finally:
            if not self.config.keep_running:
                self.reset()

    def reset(self) -> None:
        _run([
            "docker",
            "rm",
            "-f",
            "docker-hello-earth",
            "docker-hello-mars",
            "docker-hello-router",
            "cpk-hello-earth",
            "cpk-hello-mars",
            "cpk-router-demo",
        ], check=False)
        _run(["docker", "network", "create", self.config.network], check=False)

    def start(self) -> None:
        self._run_hello(EARTH_ID)
        self._run_hello(MARS_ID)
        self._run_router()

    def _run_hello(self, node_id: str) -> None:
        world = str(self.graph.node(node_id).metadata["hello_world"])
        _run([
            "docker",
            "run",
            "-d",
            "--name",
            self._container_name(node_id),
            "--network",
            self.config.network,
            "-e",
            f"HELLO_WORLD={world}",
            self.config.image,
            "python",
            "-c",
            "from control_plane_kit import create_hello_app; import uvicorn; "
            "uvicorn.run(create_hello_app(), host='0.0.0.0', port=8000)",
        ])

    def _run_router(self) -> None:
        _run([
            "docker",
            "run",
            "-d",
            "--name",
            self._container_name(ROUTER_ID),
            "--network",
            self.config.network,
            "-p",
            f"{self.config.host_port}:8000",
            self.config.image,
            "python",
            "-c",
            "from control_plane_kit import BlockControlState, CapabilityName; "
            "from control_plane_kit.servers import create_http_active_router_app; "
            "import uvicorn; "
            "state=BlockControlState('hello-router', capabilities=(CapabilityName.HEALTH_CHECKABLE, "
            "CapabilityName.TARGET_MUTABLE, CapabilityName.SWITCHABLE, CapabilityName.DRAINABLE)); "
            "uvicorn.run(create_http_active_router_app(state), host='0.0.0.0', port=8000)",
        ])

    def wait_for_router(self) -> None:
        deadline = time.time() + 20
        while time.time() < deadline:
            try:
                self.get_json("/__deploy/status")
                return
            except Exception:
                time.sleep(0.25)
        raise RuntimeError("router did not become ready")

    def register_target(self, node_id: str) -> dict[str, Any]:
        endpoint = self.graph.node(node_id).endpoint("internal").url
        return self.put_json(
            f"/__deploy/routers/{ROUTER_ID}/targets/{self._target_slug(node_id)}",
            {"url": endpoint},
        )

    def switch(self, node_id: str) -> dict[str, Any]:
        return self.post_json(
            f"/__deploy/routers/{ROUTER_ID}/active-target",
            {"target_id": self._target_slug(node_id)},
        )

    def get_hello(self) -> dict[str, Any]:
        return self.get_json("/hello")

    def get_json(self, path: str) -> dict[str, Any]:
        return _request_json("GET", f"{self.config.base_url}{path}")

    def put_json(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        return _request_json("PUT", f"{self.config.base_url}{path}", payload)

    def post_json(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        return _request_json("POST", f"{self.config.base_url}{path}", payload)

    def _container_name(self, node_id: str) -> str:
        return f"docker-{node_id}"

    def _target_slug(self, node_id: str) -> str:
        return node_id.removeprefix("hello-")


def _request_json(method: str, url: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    body = None
    headers = {}
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        headers["content-type"] = "application/json"
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    with urllib.request.urlopen(request, timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))


def _run(args: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(args, text=True, capture_output=True)
    if check and result.returncode != 0:
        command = " ".join(args)
        raise RuntimeError(f"command failed: {command}\n{result.stderr.strip()}")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the hello/router switch live Docker demo.")
    parser.add_argument("--image", default=DEFAULT_IMAGE)
    parser.add_argument("--network", default=DEFAULT_NETWORK)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--cleanup", action="store_true", help="Stop demo containers after printing output.")
    args = parser.parse_args()
    runner = HelloRouterDemoRunner(
        DemoConfig(
            image=args.image,
            network=args.network,
            host_port=args.port,
            keep_running=not args.cleanup,
        )
    )
    try:
        runner.run()
    except urllib.error.URLError as exc:
        raise SystemExit(f"demo request failed: {exc}") from exc


if __name__ == "__main__":
    main()
