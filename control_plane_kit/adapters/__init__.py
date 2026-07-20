"""Concrete runtime and transport adapters for the pure control-plane algebra."""

from control_plane_kit._optional import require_optional_dependencies

require_optional_dependencies(
    "control_plane_kit.adapters",
    ("httpx",),
    extra="http",
)

from control_plane_kit.adapters.verification import (
    HttpVerificationInterpreter,
    RedisPingTransport,
    RedisVerificationInterpreter,
    SocketRedisPingTransport,
)

__all__ = [
    "HttpVerificationInterpreter",
    "RedisPingTransport",
    "RedisVerificationInterpreter",
    "SocketRedisPingTransport",
]
