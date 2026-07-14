"""Optional server adapters for control-plane-kit."""

from control_plane_kit.servers.block_control import BlockControlState, create_block_control_app
from control_plane_kit.servers.hello import HelloEnvironment, hello_command, hello_server_block
from control_plane_kit.servers.http_active_router import (
    HttpActiveRouterRuntime,
    HttpActiveRouterServer,
    http_active_router_block,
    http_active_router_command,
)
from control_plane_kit.servers.http_messages import HttpHandler, HttpRequest, HttpResponse
from control_plane_kit.servers.http_multiplexer import (
    HttpMultiplexerRuntime,
    HttpMultiplexerServer,
    http_multiplexer_block,
    http_multiplexer_command,
)
from control_plane_kit.servers.http_proxy import HttpProxyRuntime, HttpProxyServer, http_proxy_block, http_proxy_command
from control_plane_kit.servers.http_weighted_balancer import (
    HttpWeightedLoadBalancerRuntime,
    HttpWeightedLoadBalancerServer,
    http_weighted_load_balancer_block,
    http_weighted_load_balancer_command,
)

__all__ = [
    "BlockControlState",
    "HelloEnvironment",
    "HttpActiveRouterRuntime",
    "HttpActiveRouterServer",
    "HttpHandler",
    "HttpMultiplexerRuntime",
    "HttpMultiplexerServer",
    "HttpProxyRuntime",
    "HttpProxyServer",
    "HttpRequest",
    "HttpResponse",
    "HttpWeightedLoadBalancerRuntime",
    "HttpWeightedLoadBalancerServer",
    "create_block_control_app",
    "hello_command",
    "hello_server_block",
    "http_active_router_block",
    "http_active_router_command",
    "http_multiplexer_block",
    "http_multiplexer_command",
    "http_proxy_block",
    "http_proxy_command",
    "http_weighted_load_balancer_block",
    "http_weighted_load_balancer_command",
]
