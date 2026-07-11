"""Composable proxy descriptors.

Proxy nodes are intentionally modeled as small product types:

``ProxyNode = Protocol x Behavior x Implementation``

That keeps the package from growing a dedicated class for every combination of
HTTP/TCP/Postgres and routing/load-balancing/pooling behavior.
"""

from control_plane_kit.proxies.behaviors import (
    ActiveTarget,
    ConnectionPool,
    MirrorTraffic,
    RoundRobin,
)
from control_plane_kit.proxies.implementations import (
    HAProxyImplementation,
    NginxImplementation,
    PgBouncerImplementation,
    PythonImplementation,
)
from control_plane_kit.proxies.nodes import ProxyNode
from control_plane_kit.proxies.protocols import HttpProtocol, PostgresProtocol, TcpProtocol

__all__ = [
    "ActiveTarget",
    "ConnectionPool",
    "HAProxyImplementation",
    "HttpProtocol",
    "MirrorTraffic",
    "NginxImplementation",
    "PgBouncerImplementation",
    "PostgresProtocol",
    "ProxyNode",
    "PythonImplementation",
    "RoundRobin",
    "TcpProtocol",
]
