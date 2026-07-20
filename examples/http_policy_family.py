"""Heterogeneous HTTP policy and resilience topology.

The expression is intentionally ordinary deployment algebra. It introduces no
combined gateway product and no acceptance-only graph representation.
"""

from __future__ import annotations

from dataclasses import replace

from control_plane_kit import (
    BlockSockets,
    BlockSpec,
    DataBlock,
    DeploymentRecipe,
    DockerPostgresImplementation,
    DockerRuntime,
    HttpCheck,
    Protocol,
    ProviderSocket,
    SocketConnection,
    VerificationContract,
)
from control_plane_kit.domains.idempotency import (
    IdempotencyGatewayPolicy,
    IdempotencyMethod,
    IdempotencyRoutePolicy,
)
from control_plane_kit.domains.load_generation import LoadGeneratorPolicy
from control_plane_kit.products.servers import (
    AuthGatewayPolicy,
    AuthenticationMechanism,
    GatewayMethod,
    RouteAuthorizationPolicy,
    http_auth_gateway_block,
)
from control_plane_kit.servers import (
    hello_server_block,
    http_active_router_block,
    http_bulkhead_block,
    http_cache_block,
    http_circuit_breaker_block,
    http_fault_injector_block,
    http_idempotency_gateway_block,
    http_load_generator_block,
    http_multiplexer_block,
    http_proxy_block,
    http_rate_limiter_block,
    http_retry_block,
    http_timeout_block,
    http_traffic_logger_block,
    http_weighted_load_balancer_block,
    managed_http_router_block,
    request_observer_block,
)


def http_policy_family_recipe() -> DeploymentRecipe:
    """Return one graph containing the complete package-owned HTTP family."""

    entry = managed_http_router_block("entry-router")
    entry = replace(
        entry,
        spec=replace(
            entry.spec,
            verification=VerificationContract(
                (
                    HttpCheck(
                        check_id="entry-can-reach-application",
                        provider_socket="internal",
                        path="/probe",
                    ),
                )
            ),
        ),
    )
    database = DataBlock(
        BlockSpec("idempotency-postgres", "Idempotency Postgres"),
        DockerPostgresImplementation(database="idempotency"),
        BlockSockets(providers=(ProviderSocket("internal", Protocol.POSTGRES),)),
    )
    children = (
        hello_server_block("hello-a", message="Hello from branch A"),
        hello_server_block("hello-b", message="Hello from branch B"),
        hello_server_block("hello-green", message="Hello from green"),
        hello_server_block("idempotency-target", message="Idempotent target"),
        request_observer_block("request-observer"),
        http_cache_block("cache"),
        http_circuit_breaker_block("circuit"),
        http_retry_block("retry"),
        http_traffic_logger_block("traffic-logger"),
        http_multiplexer_block("multiplexer"),
        http_fault_injector_block("fault-injector"),
        http_bulkhead_block("bulkhead"),
        http_timeout_block("timeout"),
        http_weighted_load_balancer_block("balancer"),
        http_rate_limiter_block("rate-limiter"),
        http_auth_gateway_block(
            "auth-gateway",
            policy=AuthGatewayPolicy(
                AuthenticationMechanism.API_KEY,
                (
                    RouteAuthorizationPolicy(
                        "/",
                        (GatewayMethod.GET,),
                        ("read",),
                    ),
                ),
            ),
            api_key_scopes=("read",),
        ),
        http_proxy_block("proxy"),
        http_active_router_block("active-router"),
        entry,
        http_idempotency_gateway_block(
            "idempotency-gateway",
            policy=IdempotencyGatewayPolicy(
                (
                    IdempotencyRoutePolicy(
                        "/commands",
                        IdempotencyMethod.POST,
                    ),
                )
            ),
        ),
        database,
        http_load_generator_block(
            "load-generator",
            policy=LoadGeneratorPolicy(("/probe",)),
        ),
        SocketConnection("hello-a", "internal", "cache", "target"),
        SocketConnection("cache", "internal", "circuit", "target"),
        SocketConnection("circuit", "internal", "retry", "target"),
        SocketConnection("retry", "internal", "traffic-logger", "target"),
        SocketConnection("traffic-logger", "internal", "balancer", "target-a"),
        SocketConnection("hello-b", "internal", "multiplexer", "primary"),
        SocketConnection(
            "request-observer",
            "internal",
            "multiplexer",
            "observer-a",
        ),
        SocketConnection("multiplexer", "internal", "fault-injector", "target"),
        SocketConnection("fault-injector", "internal", "bulkhead", "target"),
        SocketConnection("bulkhead", "internal", "timeout", "target"),
        SocketConnection("timeout", "internal", "balancer", "target-b"),
        SocketConnection("balancer", "internal", "rate-limiter", "target"),
        SocketConnection("rate-limiter", "internal", "auth-gateway", "target"),
        SocketConnection("auth-gateway", "internal", "proxy", "target"),
        SocketConnection("proxy", "internal", "active-router", "active"),
        SocketConnection(
            "active-router",
            "internal",
            "entry-router",
            "target-blue",
        ),
        SocketConnection(
            "active-router",
            "internal",
            "entry-router",
            "active",
        ),
        SocketConnection(
            "hello-green",
            "internal",
            "entry-router",
            "target-green",
        ),
        SocketConnection(
            "idempotency-target",
            "internal",
            "idempotency-gateway",
            "target",
        ),
        SocketConnection(
            "idempotency-postgres",
            "internal",
            "idempotency-gateway",
            "database",
        ),
        SocketConnection(
            "entry-router",
            "internal",
            "load-generator",
            "target",
        ),
    )
    return DeploymentRecipe(
        "http-policy-family",
        DockerRuntime(runtime_id="http-policy-runtime", children=children),
    )
