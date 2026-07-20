"""Inspectable implementation contracts for package-owned servers."""

from __future__ import annotations

from control_plane_kit.core.algebra import (
    PackageServerProduct,
    ProductMaturity,
)
from control_plane_kit.core.capabilities import CapabilityName
from control_plane_kit.core.control_routes import ControlRouteSetName
from control_plane_kit.products.servers import (
    CapabilityImplementation,
    ExecutableCapability,
    ProductCatalog,
    ProductDeclaration,
)
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
from control_plane_kit.domains.idempotency import (
    IdempotencyGatewayPolicy,
    IdempotencyMethod,
    IdempotencyRoutePolicy,
)
from control_plane_kit.servers.http_idempotency_gateway import http_idempotency_gateway_block
from control_plane_kit.domains.load_generation import LoadGeneratorPolicy
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
from control_plane_kit.servers.coredns import coredns_block
from control_plane_kit.servers.webhook_delivery import webhook_delivery_block
from control_plane_kit.servers.opentelemetry_collector import (
    opentelemetry_collector_block,
)
from control_plane_kit.servers.tcp_switch import tcp_switch_block


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


def _runtime(capability: CapabilityName) -> ExecutableCapability:
    return ExecutableCapability(
        capability,
        CapabilityImplementation.RUNTIME_LIFECYCLE,
    )


PACKAGE_SERVER_CONTRACTS = (
    ProductDeclaration(
        PackageServerProduct.COREDNS,
        ProductMaturity.OPERATIONAL,
        coredns_block(),
        (
            _probe(path="/health"),
            _runtime(CapabilityName.RESTARTABLE),
        ),
    ),
    ProductDeclaration(
        PackageServerProduct.TCP_SWITCH,
        ProductMaturity.TEST_ONLY,
        tcp_switch_block(),
        (
            _control(CapabilityName.HEALTH_CHECKABLE, ControlRouteSetName.COMMON_STATUS),
            _control(CapabilityName.TARGET_MUTABLE, ControlRouteSetName.TARGETS),
            _control(CapabilityName.SWITCHABLE, ControlRouteSetName.TARGETS),
        ),
    ),
    ProductDeclaration(
        PackageServerProduct.WEBHOOK_DELIVERY,
        ProductMaturity.OPERATIONAL,
        webhook_delivery_block(),
        (_probe(path="/health/ready"),),
    ),
    ProductDeclaration(
        PackageServerProduct.OPENTELEMETRY_COLLECTOR,
        ProductMaturity.OPERATIONAL,
        opentelemetry_collector_block(),
        (_probe(path="/"),),
    ),
    ProductDeclaration(
        PackageServerProduct.SERVICE_DISCOVERY,
        ProductMaturity.TEST_ONLY,
        service_discovery_block(),
        (
            _probe(path="/health/ready"),
            _control(CapabilityName.DISCOVERY_READABLE, ControlRouteSetName.DISCOVERY),
            _control(CapabilityName.DISCOVERY_MUTABLE, ControlRouteSetName.DISCOVERY),
        ),
    ),
    ProductDeclaration(
        PackageServerProduct.HTTP_LOAD_GENERATOR,
        ProductMaturity.TEST_ONLY,
        http_load_generator_block(policy=LoadGeneratorPolicy(("/",))),
        (
            _probe(path="/health"),
            _control(CapabilityName.LOAD_STATE_READABLE, ControlRouteSetName.LOADS),
            _control(CapabilityName.LOAD_MUTABLE, ControlRouteSetName.LOADS),
        ),
    ),
    ProductDeclaration(
        PackageServerProduct.HTTP_IDEMPOTENCY_GATEWAY,
        ProductMaturity.TEST_ONLY,
        http_idempotency_gateway_block(
            policy=IdempotencyGatewayPolicy(
                (IdempotencyRoutePolicy("/", IdempotencyMethod.POST),),
            ),
        ),
        (_probe(path="/health"),),
    ),
    ProductDeclaration(
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
    ProductDeclaration(
        PackageServerProduct.HELLO,
        ProductMaturity.TEACHING,
        hello_server_block(),
        (_probe(),),
    ),
    ProductDeclaration(
        PackageServerProduct.HTTP_PROXY,
        ProductMaturity.TEACHING,
        http_proxy_block(),
        (_probe(),),
    ),
    ProductDeclaration(
        PackageServerProduct.HTTP_ACTIVE_ROUTER,
        ProductMaturity.TEACHING,
        http_active_router_block(),
        (_probe(),),
    ),
    ProductDeclaration(
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
    ProductDeclaration(
        PackageServerProduct.HTTP_BULKHEAD,
        ProductMaturity.TEACHING,
        http_bulkhead_block(),
        (
            _probe(path="/health"),
            _control(CapabilityName.METRICS_READABLE, ControlRouteSetName.METRICS),
        ),
    ),
    ProductDeclaration(
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
    ProductDeclaration(
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
    ProductDeclaration(
        PackageServerProduct.HTTP_MULTIPLEXER,
        ProductMaturity.TEACHING,
        http_multiplexer_block(),
        (_probe(),),
    ),
    ProductDeclaration(
        PackageServerProduct.HTTP_RATE_LIMITER,
        ProductMaturity.TEACHING,
        http_rate_limiter_block(),
        (_probe(),),
    ),
    ProductDeclaration(
        PackageServerProduct.HTTP_RETRY,
        ProductMaturity.TEACHING,
        http_retry_block(),
        (
            _probe(path="/health"),
            _control(CapabilityName.METRICS_READABLE, ControlRouteSetName.METRICS),
        ),
    ),
    ProductDeclaration(
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
    ProductDeclaration(
        PackageServerProduct.HTTP_TIMEOUT,
        ProductMaturity.TEACHING,
        http_timeout_block(),
        (
            _probe(path="/health"),
            _control(CapabilityName.METRICS_READABLE, ControlRouteSetName.METRICS),
        ),
    ),
    ProductDeclaration(
        PackageServerProduct.HTTP_WEIGHTED_LOAD_BALANCER,
        ProductMaturity.TEACHING,
        http_weighted_load_balancer_block(),
        (_probe(),),
    ),
    ProductDeclaration(
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
    ProductDeclaration(
        PackageServerProduct.REQUEST_OBSERVER,
        ProductMaturity.TEACHING,
        request_observer_block(),
        (
            _probe(path="/health"),
            _control(CapabilityName.METRICS_READABLE, ControlRouteSetName.METRICS),
        ),
    ),
)

PACKAGE_SERVER_CATALOG = ProductCatalog(PACKAGE_SERVER_CONTRACTS)


def package_server_contract(
    product: PackageServerProduct,
) -> ProductDeclaration:
    """Return the exact package contract for one closed product."""

    return PACKAGE_SERVER_CATALOG.declaration(product)
