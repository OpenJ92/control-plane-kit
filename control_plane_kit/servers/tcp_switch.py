"""Byte-transparent TCP switch with an authenticated HTTP control plane."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from enum import StrEnum
from threading import Lock
from urllib.parse import urlsplit

from control_plane_kit.core.algebra import (
    BlockSockets,
    PackageServerProduct,
    PackageServerSpec,
    ProductMaturity,
    ProviderSocket,
    ProxyBlock,
    RequirementSocket,
)
from control_plane_kit.core.capabilities import CapabilityName
from control_plane_kit.contracts import EnvironmentContract, TextVariable
from control_plane_kit.core.environment import PublicStaticEnvironmentBinding
from control_plane_kit.implementations import DockerImageImplementation, HostPublication
from control_plane_kit.core.secrets import SecretEnvironmentDelivery, SecretReference
from control_plane_kit.servers.block_control import BlockControlState, create_block_control_app
from control_plane_kit.core.types import Protocol, SocketBinding


class TcpSwitchMode(StrEnum):
    """Closed connection-selection policies for the transparent data plane."""

    ACTIVE_TARGET = "active-target"
    ROUND_ROBIN = "round-robin"


class TcpSwitchEnvironment(EnvironmentContract):
    """Startup contract for one package TCP switch process."""

    block_id = TextVariable("block_id", metadata={"env": "CPK_TCP_SWITCH_BLOCK_ID"})
    target_a = TextVariable("target_a", metadata={"env": "CPK_TCP_SWITCH_TARGET_A"})
    target_b = TextVariable("target_b", metadata={"env": "CPK_TCP_SWITCH_TARGET_B"})
    active_target = TextVariable(
        "active_target", metadata={"env": "CPK_TCP_SWITCH_ACTIVE_TARGET"}
    )
    mode = TextVariable("mode", metadata={"env": "CPK_TCP_SWITCH_MODE"})
    control_token = TextVariable("control_token", metadata={"env": "CPK_CONTROL_TOKEN"})


@dataclass(frozen=True)
class TcpTarget:
    """Validated raw-TCP target selected without inspecting payload bytes."""

    host: str
    port: int

    @classmethod
    def parse(cls, value: str) -> "TcpTarget":
        if not isinstance(value, str) or len(value.encode()) > 2_048:
            raise ValueError("TCP target must be a bounded URL")
        parsed = urlsplit(value)
        if (
            parsed.scheme != "tcp"
            or not parsed.hostname
            or parsed.port is None
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in ("", "/")
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("TCP target must be tcp://host:port without credentials or path")
        return cls(parsed.hostname, parsed.port)


@dataclass(frozen=True)
class TcpSwitchSettings:
    """Bounded process settings for the data and control planes."""

    block_id: str
    targets: dict[str, str]
    active_target: str
    mode: TcpSwitchMode
    control_token: str
    data_port: int = 7000
    connect_timeout_seconds: float = 5.0
    idle_timeout_seconds: float = 30.0
    max_connections: int = 128
    max_targets: int = 32

    def __post_init__(self) -> None:
        if not self.block_id or len(self.block_id) > 128:
            raise ValueError("TCP switch block id must be bounded")
        if not self.control_token:
            raise ValueError("TCP switch requires a control token")
        if type(self.data_port) is not int or not 0 <= self.data_port <= 65_535:
            raise ValueError("TCP switch data port must be between 0 and 65535")
        if type(self.max_connections) is not int or self.max_connections < 1:
            raise ValueError("TCP switch connection limit must be positive")
        if type(self.max_targets) is not int or self.max_targets < 1:
            raise ValueError("TCP switch target limit must be positive")
        _validated_targets(self.targets, limit=self.max_targets)
        if self.active_target not in self.targets:
            raise ValueError("TCP switch active target must be registered")

    @classmethod
    def from_process(cls) -> "TcpSwitchSettings":
        values = TcpSwitchEnvironment.from_process()
        return cls(
            block_id=values.get("block_id"),
            targets={"target-a": values.get("target_a"), "target-b": values.get("target_b")},
            active_target=values.get("active_target"),
            mode=TcpSwitchMode(values.get("mode")),
            control_token=values.get("control_token"),
        )


@dataclass
class TcpSwitchState(BlockControlState):
    """Control state plus deterministic connection-target selection."""

    mode: TcpSwitchMode = TcpSwitchMode.ACTIVE_TARGET
    max_targets: int = 32
    _cursor: int = field(default=0, init=False)
    _selection_lock: Lock = field(default_factory=Lock, init=False)

    def replace_targets(self, targets):
        _validated_targets(targets, limit=self.max_targets)
        result = super().replace_targets(targets)
        with self._selection_lock:
            self._cursor = 0
        return result

    def select_target(self) -> TcpTarget:
        with self._selection_lock:
            targets = dict(sorted((self.runtime.get("targets") or {}).items()))
            if not targets:
                raise RuntimeError("TCP switch has no targets")
            if self.mode is TcpSwitchMode.ACTIVE_TARGET:
                target_id = str(self.runtime.get("active_target") or "")
                if target_id not in targets:
                    raise RuntimeError("TCP switch has no active target")
            else:
                target_id = tuple(targets)[self._cursor % len(targets)]
                self._cursor += 1
            return TcpTarget.parse(targets[target_id])


def create_tcp_switch_app(settings: TcpSwitchSettings):
    """Create authenticated HTTP control and byte-transparent TCP data planes."""

    state = TcpSwitchState(
        block_id=settings.block_id,
        capabilities=(
            CapabilityName.HEALTH_CHECKABLE,
            CapabilityName.TARGET_MUTABLE,
            CapabilityName.SWITCHABLE,
        ),
        targets=dict(settings.targets),
        active_target=settings.active_target,
        mode=settings.mode,
        max_targets=settings.max_targets,
    )
    capacity = asyncio.Semaphore(settings.max_connections)

    async def copy(source: asyncio.StreamReader, destination: asyncio.StreamWriter) -> None:
        while True:
            data = await asyncio.wait_for(
                source.read(65_536), timeout=settings.idle_timeout_seconds
            )
            if not data:
                if destination.can_write_eof():
                    destination.write_eof()
                    await destination.drain()
                return
            destination.write(data)
            await destination.drain()

    async def forward(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        async with capacity:
            upstream_writer: asyncio.StreamWriter | None = None
            try:
                target = state.select_target()
                upstream_reader, upstream_writer = await asyncio.wait_for(
                    asyncio.open_connection(target.host, target.port),
                    timeout=settings.connect_timeout_seconds,
                )
                await asyncio.gather(
                    copy(reader, upstream_writer),
                    copy(upstream_reader, writer),
                )
            except (OSError, RuntimeError, TimeoutError):
                pass
            finally:
                if upstream_writer is not None:
                    upstream_writer.close()
                    await upstream_writer.wait_closed()
                writer.close()
                await writer.wait_closed()

    @asynccontextmanager
    async def lifespan(app):
        server = await asyncio.start_server(forward, "0.0.0.0", settings.data_port)
        app.state.tcp_data_port = server.sockets[0].getsockname()[1]
        try:
            yield
        finally:
            server.close()
            await server.wait_closed()

    app = create_block_control_app(state, token=settings.control_token, execution_mode=True)
    app.router.lifespan_context = lifespan
    app.state.tcp_switch_state = state

    from fastapi import Request
    from fastapi.responses import JSONResponse

    @app.exception_handler(ValueError)
    async def invalid_tcp_target(_request: Request, _error: ValueError) -> JSONResponse:
        return JSONResponse(status_code=400, content={"detail": "invalid TCP target material"})

    return app


def create_tcp_switch_app_from_environment():
    return create_tcp_switch_app(TcpSwitchSettings.from_process())


def tcp_switch_block(
    block_id: str = "tcp-switch",
    *,
    mode: TcpSwitchMode = TcpSwitchMode.ACTIVE_TARGET,
    image: str = "control-plane-kit-live-test:local",
    host_port: int | None = None,
    control_secret_reference: str = "secret://tcp-switch/control",
) -> ProxyBlock:
    """Return a graph-wired raw-TCP switch/load-balancer block."""

    return ProxyBlock(
        PackageServerSpec(
            role_id=block_id,
            product=PackageServerProduct.TCP_SWITCH,
            maturity=ProductMaturity.TEST_ONLY,
            display_name="TCP Switch",
            capabilities=(
                CapabilityName.HEALTH_CHECKABLE,
                CapabilityName.TARGET_MUTABLE,
                CapabilityName.SWITCHABLE,
            ),
            metadata={"behavior": "byte-transparent-tcp-switch"},
        ),
        DockerImageImplementation(
            image=image,
            command=(
                "uvicorn",
                "control_plane_kit.servers.tcp_switch:create_tcp_switch_app_from_environment",
                "--factory",
                "--host",
                "0.0.0.0",
                "--port",
                "8080",
            ),
            ports={"data": 7000, "control": 8080},
            environment=(
                PublicStaticEnvironmentBinding("CPK_TCP_SWITCH_BLOCK_ID", block_id),
                PublicStaticEnvironmentBinding(
                    "CPK_TCP_SWITCH_ACTIVE_TARGET", "target-a"
                ),
                PublicStaticEnvironmentBinding("CPK_TCP_SWITCH_MODE", mode.value),
            ),
            secret_deliveries=(
                SecretEnvironmentDelivery(
                    "CPK_CONTROL_TOKEN", SecretReference(control_secret_reference)
                ),
            ),
            host_publications={"data": HostPublication.loopback_v4(host_port)},
        ),
        BlockSockets(
            requirements=(
                RequirementSocket("target-a", Protocol.TCP, ("CPK_TCP_SWITCH_TARGET_A",)),
                RequirementSocket("target-b", Protocol.TCP, ("CPK_TCP_SWITCH_TARGET_B",)),
                RequirementSocket(
                    "active", Protocol.TCP, (), binding=SocketBinding.RUNTIME_CONTROL
                ),
            ),
            providers=(ProviderSocket("data", Protocol.TCP),),
        ),
    )


def _validated_targets(targets, *, limit: int) -> dict[str, str]:
    values = dict(targets)
    if not values or len(values) > limit:
        raise ValueError("TCP switch target count is outside configured bounds")
    for target_id, endpoint in values.items():
        if not isinstance(target_id, str) or not target_id or len(target_id) > 128:
            raise ValueError("TCP target id must be bounded")
        TcpTarget.parse(endpoint)
    return values
