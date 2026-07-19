"""Inspectable implementation contracts for package-owned servers."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from control_plane_kit.algebra import (
    DeployBlock,
    PackageServerProduct,
    PackageServerSpec,
    ProductMaturity,
)
from control_plane_kit.capabilities import CapabilityName
from control_plane_kit.control_routes import ControlRouteSetName
from control_plane_kit.servers.hello import hello_server_block
from control_plane_kit.servers.http_active_router import http_active_router_block
from control_plane_kit.servers.http_circuit_breaker import http_circuit_breaker_block
from control_plane_kit.servers.http_bulkhead import http_bulkhead_block
from control_plane_kit.servers.http_cache import http_cache_block
from control_plane_kit.servers.http_auth_gateway import (
    AuthenticationMechanism,
    AuthGatewayPolicy,
    GatewayMethod,
    RouteAuthorizationPolicy,
    http_auth_gateway_block,
)
from control_plane_kit.idempotency import (
    IdempotencyGatewayPolicy,
    IdempotencyMethod,
    IdempotencyRoutePolicy,
)
from control_plane_kit.servers.http_idempotency_gateway import http_idempotency_gateway_block
from control_plane_kit.load_generation import LoadGeneratorPolicy
from control_plane_kit.servers.http_load_generator import http_load_generator_block
from control_plane_kit.servers.http_fault_injector import http_fault_injector_block
from control_plane_kit.servers.http_multiplexer import http_multiplexer_block
from control_plane_kit.servers.http_proxy import http_proxy_block
from control_plane_kit.servers.http_rate_limiter import http_rate_limiter_block
from control_plane_kit.servers.http_retry import http_retry_block
from control_plane_kit.servers.http_traffic_logger import http_traffic_logger_block
from control_plane_kit.servers.http_timeout import http_timeout_block
from control_plane_kit.servers.http_weighted_balancer import http_weighted_load_balancer_block
from control_plane_kit.servers.managed_http_router import managed_http_router_block
from control_plane_kit.servers.request_observer import request_observer_block
from control_plane_kit.servers.service_discovery import service_discovery_block
from control_plane_kit.servers.opentelemetry_collector import (
    opentelemetry_collector_block,
)


class CapabilityImplementation(StrEnum):
    """Closed implementation boundaries that can realize a capability."""

    APPLICATION_PROBE = "application-probe"
    CONTROL_ROUTE = "control-route"


@dataclass(frozen=True)
class ExecutableCapability:
    """Evidence that one product can execute an advertised capability."""

    capability: CapabilityName
    implementation: CapabilityImplementation
    route_set: ControlRouteSetName | None = None
    path: str | None = None

    def __post_init__(self) -> None:
        match self.implementation:
            case CapabilityImplementation.APPLICATION_PROBE:
                if self.route_set is not None:
                    raise ValueError("application probe cannot name a control route set")
                if self.path is None or not self.path.startswith("/"):
                    raise ValueError("application probe requires an absolute path")
            case CapabilityImplementation.CONTROL_ROUTE:
                if self.route_set is None:
                    raise ValueError("control route requires a route set")
                if self.path is not None:
                    raise ValueError("control route cannot name an application path")


@dataclass(frozen=True)
class UnsupportedCapability:
    """Explicit closed result for a capability a product does not implement."""

    capability: CapabilityName
    reason: str


CapabilityResolution = ExecutableCapability | UnsupportedCapability


@dataclass(frozen=True)
class PackageServerContract:
    """Package product identity paired with its executable operational truth."""

    product: PackageServerProduct
    maturity: ProductMaturity
    block: DeployBlock
    capabilities: tuple[ExecutableCapability, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.block.spec, PackageServerSpec):
            raise TypeError("package server contract requires PackageServerSpec")
        if self.block.spec.product is not self.product:
            raise ValueError("package server contract product does not match block spec")
        if self.block.spec.maturity is not self.maturity:
            raise ValueError("package server contract maturity does not match block spec")
        implemented = tuple(value.capability for value in self.capabilities)
        if len(set(implemented)) != len(implemented):
            raise ValueError("package server contract repeats a capability")
        if self.block.spec.capabilities != implemented:
            raise ValueError(
                "advertised capabilities must exactly match executable capability evidence"
            )

    def resolve(self, capability: CapabilityName) -> CapabilityResolution:
        """Return executable evidence or an explicit unsupported result."""

        for support in self.capabilities:
            if support.capability is capability:
                return support
        return UnsupportedCapability(
            capability,
            f"{self.product.value} does not implement {capability.value}",
        )

    def descriptor(self) -> dict[str, object]:
        """Return the capability matrix row used by reviews and tooling."""

        return {
            "product": self.product.value,
            "maturity": self.maturity.value,
            "requirements": [
                {
                    "name": socket.name,
                    "protocol": socket.protocol.descriptor(),
                    "binding": socket.binding.value,
                    "env_bindings": list(socket.env_bindings),
                    "required": socket.required,
                }
                for socket in self.block.sockets.requirements
            ],
            "providers": [
                {"name": socket.name, "protocol": socket.protocol.descriptor()}
                for socket in self.block.sockets.providers
            ],
            "capabilities": [
                {
                    "name": support.capability.value,
                    "implementation": support.implementation.value,
                    **(
                        {"route_set": support.route_set.value}
                        if support.route_set is not None
                        else {}
                    ),
                    **({"path": support.path} if support.path is not None else {}),
                }
                for support in self.capabilities
            ],
        }


def _probe(
    capability: CapabilityName = CapabilityName.HEALTH_CHECKABLE,
    *,
    path: str = "/",
) -> ExecutableCapability:
    return ExecutableCapability(
        capability,
        CapabilityImplementation.APPLICATION_PROBE,
        path=path,
    )


def _control(
    capability: CapabilityName,
    route_set: ControlRouteSetName,
) -> ExecutableCapability:
    return ExecutableCapability(
        capability,
        CapabilityImplementation.CONTROL_ROUTE,
        route_set=route_set,
    )


PACKAGE_SERVER_CONTRACTS = (
    PackageServerContract(
        PackageServerProduct.OPENTELEMETRY_COLLECTOR,
        ProductMaturity.OPERATIONAL,
        opentelemetry_collector_block(),
        (_probe(path="/"),),
    ),
    PackageServerContract(
        PackageServerProduct.SERVICE_DISCOVERY,
        ProductMaturity.TEST_ONLY,
        service_discovery_block(),
        (
            _probe(path="/health/ready"),
            _control(CapabilityName.DISCOVERY_READABLE, ControlRouteSetName.DISCOVERY),
            _control(CapabilityName.DISCOVERY_MUTABLE, ControlRouteSetName.DISCOVERY),
        ),
    ),
    PackageServerContract(
        PackageServerProduct.HTTP_LOAD_GENERATOR,
        ProductMaturity.TEST_ONLY,
        http_load_generator_block(policy=LoadGeneratorPolicy(("/",))),
        (
            _probe(path="/health"),
            _control(CapabilityName.LOAD_STATE_READABLE, ControlRouteSetName.LOADS),
            _control(CapabilityName.LOAD_MUTABLE, ControlRouteSetName.LOADS),
        ),
    ),
    PackageServerContract(
        PackageServerProduct.HTTP_IDEMPOTENCY_GATEWAY,
        ProductMaturity.TEST_ONLY,
        http_idempotency_gateway_block(
            policy=IdempotencyGatewayPolicy(
                (IdempotencyRoutePolicy("/", IdempotencyMethod.POST),),
            ),
        ),
        (_probe(path="/health"),),
    ),
    PackageServerContract(
        PackageServerProduct.HTTP_AUTH_GATEWAY,
        ProductMaturity.TEST_ONLY,
        http_auth_gateway_block(
            policy=AuthGatewayPolicy(
                AuthenticationMechanism.API_KEY,
                (RouteAuthorizationPolicy("/", (GatewayMethod.GET,)),),
            ),
        ),
        (
            _probe(path="/health"),
            _control(CapabilityName.METRICS_READABLE, ControlRouteSetName.METRICS),
        ),
    ),
    PackageServerContract(
        PackageServerProduct.HELLO,
        ProductMaturity.TEACHING,
        hello_server_block(),
        (_probe(),),
    ),
    PackageServerContract(
        PackageServerProduct.HTTP_PROXY,
        ProductMaturity.TEACHING,
        http_proxy_block(),
        (_probe(),),
    ),
    PackageServerContract(
        PackageServerProduct.HTTP_ACTIVE_ROUTER,
        ProductMaturity.TEACHING,
        http_active_router_block(),
        (_probe(),),
    ),
    PackageServerContract(
        PackageServerProduct.HTTP_CIRCUIT_BREAKER,
        ProductMaturity.TEACHING,
        http_circuit_breaker_block(),
        (
            _probe(path="/health"),
            _control(
                CapabilityName.CIRCUIT_STATE_READABLE,
                ControlRouteSetName.CIRCUIT,
            ),
            _control(
                CapabilityName.CIRCUIT_RESETTABLE,
                ControlRouteSetName.CIRCUIT,
            ),
        ),
    ),
    PackageServerContract(
        PackageServerProduct.HTTP_BULKHEAD,
        ProductMaturity.TEACHING,
        http_bulkhead_block(),
        (
            _probe(path="/health"),
            _control(CapabilityName.METRICS_READABLE, ControlRouteSetName.METRICS),
        ),
    ),
    PackageServerContract(
        PackageServerProduct.HTTP_CACHE,
        ProductMaturity.TEACHING,
        http_cache_block(),
        (
            _probe(path="/health"),
            _control(
                CapabilityName.CACHE_STATE_READABLE,
                ControlRouteSetName.CACHE,
            ),
            _control(
                CapabilityName.CACHE_PURGEABLE,
                ControlRouteSetName.CACHE,
            ),
        ),
    ),
    PackageServerContract(
        PackageServerProduct.HTTP_FAULT_INJECTOR,
        ProductMaturity.TEST_ONLY,
        http_fault_injector_block(),
        (
            _probe(path="/health"),
            _control(
                CapabilityName.FAULT_STATE_READABLE,
                ControlRouteSetName.FAULTS,
            ),
            _control(
                CapabilityName.FAULT_MUTABLE,
                ControlRouteSetName.FAULTS,
            ),
        ),
    ),
    PackageServerContract(
        PackageServerProduct.HTTP_MULTIPLEXER,
        ProductMaturity.TEACHING,
        http_multiplexer_block(),
        (_probe(),),
    ),
    PackageServerContract(
        PackageServerProduct.HTTP_RATE_LIMITER,
        ProductMaturity.TEACHING,
        http_rate_limiter_block(),
        (_probe(),),
    ),
    PackageServerContract(
        PackageServerProduct.HTTP_RETRY,
        ProductMaturity.TEACHING,
        http_retry_block(),
        (
            _probe(path="/health"),
            _control(CapabilityName.METRICS_READABLE, ControlRouteSetName.METRICS),
        ),
    ),
    PackageServerContract(
        PackageServerProduct.HTTP_TRAFFIC_LOGGER,
        ProductMaturity.TEACHING,
        http_traffic_logger_block(),
        (
            _probe(path="/health"),
            _control(
                CapabilityName.TRAFFIC_EVIDENCE_READABLE,
                ControlRouteSetName.TRAFFIC_EVIDENCE,
            ),
        ),
    ),
    PackageServerContract(
        PackageServerProduct.HTTP_TIMEOUT,
        ProductMaturity.TEACHING,
        http_timeout_block(),
        (
            _probe(path="/health"),
            _control(CapabilityName.METRICS_READABLE, ControlRouteSetName.METRICS),
        ),
    ),
    PackageServerContract(
        PackageServerProduct.HTTP_WEIGHTED_LOAD_BALANCER,
        ProductMaturity.TEACHING,
        http_weighted_load_balancer_block(),
        (_probe(),),
    ),
    PackageServerContract(
        PackageServerProduct.MANAGED_HTTP_ROUTER,
        ProductMaturity.OPERATIONAL,
        managed_http_router_block(),
        (
            _control(CapabilityName.HEALTH_CHECKABLE, ControlRouteSetName.COMMON_STATUS),
            _control(CapabilityName.TARGET_MUTABLE, ControlRouteSetName.TARGETS),
            _control(CapabilityName.SWITCHABLE, ControlRouteSetName.TARGETS),
            _control(CapabilityName.DRAINABLE, ControlRouteSetName.TARGETS),
        ),
    ),
    PackageServerContract(
        PackageServerProduct.REQUEST_OBSERVER,
        ProductMaturity.TEACHING,
        request_observer_block(),
        (
            _probe(path="/health"),
            _control(CapabilityName.METRICS_READABLE, ControlRouteSetName.METRICS),
        ),
    ),
)

_CONTRACT_BY_PRODUCT = {contract.product: contract for contract in PACKAGE_SERVER_CONTRACTS}


def package_server_contract(
    product: PackageServerProduct,
) -> PackageServerContract:
    """Return the exact package contract for one closed product."""

    return _CONTRACT_BY_PRODUCT[product]
