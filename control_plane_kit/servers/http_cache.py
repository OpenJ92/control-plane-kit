"""Bounded HTTP cache block and teaching interpreter."""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
import hashlib
import time

from control_plane_kit.algebra import (
    BlockSockets,
    PackageServerProduct,
    PackageServerSpec,
    ProviderSocket,
    ProxyBlock,
    RequirementSocket,
)
from control_plane_kit.capabilities import CapabilityName
from control_plane_kit.implementations import DockerImageImplementation
from control_plane_kit.secrets import SecretEnvironmentDelivery, SecretReference
from control_plane_kit.servers._templates import render_python_command
from control_plane_kit.servers.http_messages import HttpHandler, HttpRequest, HttpResponse
from control_plane_kit.types import Protocol


def _bounded(label: str, value: int, minimum: int, maximum: int) -> None:
    if type(value) is not int or value < minimum or value > maximum:
        raise ValueError(f"{label} must be between {minimum} and {maximum}")


class CacheVaryHeader(StrEnum):
    ACCEPT = "accept"
    ACCEPT_ENCODING = "accept-encoding"


class CacheOutcome(StrEnum):
    HIT = "hit"
    MISS = "miss"
    STALE_REFRESHED = "stale-refreshed"
    STALE_REFRESH_FAILED = "stale-refresh-failed"
    BYPASS = "bypass"
    REQUEST_REJECTED = "request-rejected"
    RESPONSE_REJECTED = "response-rejected"
    TARGET_UNAVAILABLE = "target-unavailable"


@dataclass(frozen=True)
class HttpCachePolicy:
    ttl_ms: int = 30_000
    stale_while_revalidate_ms: int = 0
    max_object_bytes: int = 65_536
    total_capacity_bytes: int = 1_048_576
    max_entries: int = 1_024
    max_key_bytes: int = 4_096
    max_request_bytes: int = 65_536
    max_response_bytes: int = 1_048_576
    vary_headers: tuple[CacheVaryHeader, ...] = ()

    def __post_init__(self) -> None:
        _bounded("cache TTL", self.ttl_ms, 1, 86_400_000)
        _bounded(
            "cache stale window",
            self.stale_while_revalidate_ms,
            0,
            86_400_000,
        )
        _bounded("cache object byte limit", self.max_object_bytes, 1, 1_048_576)
        _bounded(
            "cache total capacity",
            self.total_capacity_bytes,
            self.max_object_bytes,
            1_073_741_824,
        )
        _bounded("cache entry limit", self.max_entries, 1, 100_000)
        _bounded("cache key byte limit", self.max_key_bytes, 1, 65_536)
        _bounded("cache request byte limit", self.max_request_bytes, 1, 1_048_576)
        _bounded("cache response byte limit", self.max_response_bytes, 1, 1_048_576)
        if any(not isinstance(value, CacheVaryHeader) for value in self.vary_headers):
            raise TypeError("cache vary headers must be typed")
        if len(set(self.vary_headers)) != len(self.vary_headers):
            raise ValueError("cache vary headers must be unique")


@dataclass(frozen=True)
class CacheObservation:
    entry_count: int
    retained_bytes: int
    hit_count: int
    miss_count: int
    stale_count: int
    bypass_count: int
    eviction_count: int
    purge_count: int
    latest_outcome: CacheOutcome | None

    def descriptor(self) -> dict[str, object]:
        return {
            "entry_count": self.entry_count,
            "retained_bytes": self.retained_bytes,
            "hit_count": self.hit_count,
            "miss_count": self.miss_count,
            "stale_count": self.stale_count,
            "bypass_count": self.bypass_count,
            "eviction_count": self.eviction_count,
            "purge_count": self.purge_count,
            "latest_outcome": (
                None if self.latest_outcome is None else self.latest_outcome.value
            ),
        }


@dataclass(frozen=True)
class _CacheEntry:
    response: HttpResponse
    stored_at: float
    size: int


@dataclass
class HttpCacheServer:
    targets: Mapping[str, HttpHandler]
    target: str
    policy: HttpCachePolicy = field(default_factory=HttpCachePolicy)
    clock: Callable[[], float] = time.monotonic
    _entries: OrderedDict[str, _CacheEntry] = field(
        init=False, default_factory=OrderedDict
    )
    _retained_bytes: int = field(init=False, default=0)
    _hit_count: int = field(init=False, default=0)
    _miss_count: int = field(init=False, default=0)
    _stale_count: int = field(init=False, default=0)
    _bypass_count: int = field(init=False, default=0)
    _eviction_count: int = field(init=False, default=0)
    _purge_count: int = field(init=False, default=0)
    _latest_outcome: CacheOutcome | None = field(init=False, default=None)

    def __post_init__(self) -> None:
        if self.target not in self.targets:
            raise KeyError(f"unknown target {self.target!r}")

    def handle(self, request: HttpRequest) -> HttpResponse:
        if len(request.body) > self.policy.max_request_bytes:
            self._latest_outcome = CacheOutcome.REQUEST_REJECTED
            return HttpResponse.text("Request Entity Too Large", status_code=413)
        key = self._cache_key(request)
        if key is None:
            self._bypass_count += 1
            self._latest_outcome = CacheOutcome.BYPASS
            return self._target(request)

        now = self.clock()
        entry = self._entries.get(key)
        if entry is not None:
            age_ms = (now - entry.stored_at) * 1_000
            if age_ms <= self.policy.ttl_ms:
                self._entries.move_to_end(key)
                self._hit_count += 1
                self._latest_outcome = CacheOutcome.HIT
                return entry.response
            if age_ms <= (
                self.policy.ttl_ms + self.policy.stale_while_revalidate_ms
            ):
                self._stale_count += 1
                refresh = self._target(request)
                if self._cacheable(request, refresh):
                    self._store(key, refresh, now)
                    self._latest_outcome = CacheOutcome.STALE_REFRESHED
                else:
                    self._latest_outcome = CacheOutcome.STALE_REFRESH_FAILED
                return entry.response
            self._remove(key)

        self._miss_count += 1
        response = self._target(request)
        if self._cacheable(request, response):
            self._store(key, response, now)
            self._latest_outcome = CacheOutcome.MISS
        elif self._latest_outcome not in {
            CacheOutcome.TARGET_UNAVAILABLE,
            CacheOutcome.RESPONSE_REJECTED,
        }:
            self._bypass_count += 1
            self._latest_outcome = CacheOutcome.BYPASS
        return response

    def purge(self) -> CacheObservation:
        self._entries.clear()
        self._retained_bytes = 0
        self._purge_count += 1
        return self.observation()

    def observation(self) -> CacheObservation:
        return CacheObservation(
            len(self._entries),
            self._retained_bytes,
            self._hit_count,
            self._miss_count,
            self._stale_count,
            self._bypass_count,
            self._eviction_count,
            self._purge_count,
            self._latest_outcome,
        )

    def _cache_key(self, request: HttpRequest) -> str | None:
        if request.method.upper() != "GET" or _has_header(
            request.headers, {"authorization", "cookie"}
        ):
            return None
        components = [request.method.upper(), request.path, request.query]
        for header in self.policy.vary_headers:
            components.append(header.value)
            components.append(_header(request.headers, header.value))
        material = "\0".join(components).encode()
        if len(material) > self.policy.max_key_bytes:
            return None
        return hashlib.sha256(material).hexdigest()

    def _target(self, request: HttpRequest) -> HttpResponse:
        try:
            response = self.targets[self.target](request)
        except Exception:  # noqa: BLE001 - target loss is a closed cache outcome.
            self._latest_outcome = CacheOutcome.TARGET_UNAVAILABLE
            return HttpResponse.text("Bad Gateway", status_code=502)
        if len(response.body) > self.policy.max_response_bytes:
            self._latest_outcome = CacheOutcome.RESPONSE_REJECTED
            return HttpResponse.text("Bad Gateway", status_code=502)
        return response

    def _cacheable(self, request: HttpRequest, response: HttpResponse) -> bool:
        if request.method.upper() != "GET" or response.status_code != 200:
            return False
        if len(response.body) > self.policy.max_object_bytes:
            return False
        if _has_header(response.headers, {"set-cookie"}):
            return False
        cache_control = _header(response.headers, "cache-control").lower()
        if any(
            directive in {"private", "no-store", "no-cache"}
            for directive in (
                value.strip().split("=", 1)[0]
                for value in cache_control.split(",")
                if value.strip()
            )
        ):
            return False
        vary = {
            value.strip().lower()
            for value in _header(response.headers, "vary").split(",")
            if value.strip()
        }
        return "*" not in vary and vary.issubset(
            {value.value for value in self.policy.vary_headers}
        )

    def _store(self, key: str, response: HttpResponse, now: float) -> None:
        if key in self._entries:
            self._remove(key)
        size = len(response.body)
        self._entries[key] = _CacheEntry(response, now, size)
        self._retained_bytes += size
        while (
            len(self._entries) > self.policy.max_entries
            or self._retained_bytes > self.policy.total_capacity_bytes
        ):
            oldest, _entry = next(iter(self._entries.items()))
            self._remove(oldest)
            self._eviction_count += 1

    def _remove(self, key: str) -> None:
        entry = self._entries.pop(key)
        self._retained_bytes -= entry.size


def http_cache_block(
    block_id: str = "http-cache",
    *,
    display_name: str = "HTTP Cache",
    image: str = "python:3.14-alpine",
    policy: HttpCachePolicy = HttpCachePolicy(),
    control_secret_reference: str = "secret://http-cache/control-token",
) -> ProxyBlock:
    return ProxyBlock(
        PackageServerSpec(
            role_id=block_id,
            product=PackageServerProduct.HTTP_CACHE,
            display_name=display_name,
            health_path="/health",
            capabilities=(
                CapabilityName.HEALTH_CHECKABLE,
                CapabilityName.CACHE_STATE_READABLE,
                CapabilityName.CACHE_PURGEABLE,
            ),
        ),
        DockerImageImplementation(
            image=image,
            command=http_cache_command(policy),
            ports={"internal": 8080},
            secret_deliveries=(
                SecretEnvironmentDelivery(
                    "CPK_CACHE_CONTROL_TOKEN",
                    SecretReference(control_secret_reference),
                ),
            ),
        ),
        BlockSockets(
            requirements=(
                RequirementSocket("target", Protocol.HTTP, ("CACHE_TARGET_URL",)),
            ),
            providers=(ProviderSocket("internal", Protocol.HTTP),),
        ),
    )


def http_cache_command(
    policy: HttpCachePolicy = HttpCachePolicy(),
    *,
    port: int = 8080,
) -> tuple[str, ...]:
    if not isinstance(policy, HttpCachePolicy):
        raise TypeError("cache policy must be typed")
    _bounded("cache port", port, 1, 65_535)
    return render_python_command(
        "http_cache.py.j2",
        ttl_ms=policy.ttl_ms,
        stale_while_revalidate_ms=policy.stale_while_revalidate_ms,
        max_object_bytes=policy.max_object_bytes,
        total_capacity_bytes=policy.total_capacity_bytes,
        max_entries=policy.max_entries,
        max_key_bytes=policy.max_key_bytes,
        max_request_bytes=policy.max_request_bytes,
        max_response_bytes=policy.max_response_bytes,
        vary_headers=[value.value for value in policy.vary_headers],
        port=port,
    )


def _header(headers: Mapping[str, str], name: str) -> str:
    return next(
        (value for key, value in headers.items() if key.lower() == name),
        "",
    )


def _has_header(headers: Mapping[str, str], names: set[str]) -> bool:
    return any(key.lower() in names for key in headers)
