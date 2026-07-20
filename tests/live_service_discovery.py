"""Authenticated live lifecycle proof for the packaged discovery server."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import os

import httpx

from control_plane_kit import (
    Endpoint,
    EndpointScope,
    LiteralAddress,
    Protocol,
)
from control_plane_kit.domains.discovery import (
    DeregisterDiscoveryInstance,
    DiscoveryIdentity,
    DiscoveryLease,
    DiscoveryRegistration,
    DiscoveryRegistrationMode,
    ExpireDiscoveryLeases,
    HeartbeatDiscoveryInstance,
    RegisterDiscoveryInstance,
    discovery_command_descriptor,
)


BASE_URL = os.environ["CPK_DISCOVERY_LIVE_URL"].rstrip("/")
TOKEN = os.environ["CPK_DISCOVERY_LIVE_TOKEN"]
WORKSPACE = "discovery-live"


def main() -> None:
    now = datetime.now(timezone.utc)
    headers = _headers()
    unauthorized = httpx.post(
        f"{BASE_URL}/__deploy/discovery/registrations",
        json=discovery_command_descriptor(
            RegisterDiscoveryInstance("denied", _registration("denied", now))
        ),
        timeout=5,
    )
    _status(unauthorized, 401)
    assert TOKEN not in unauthorized.text

    first = _registration("hello-a", now)
    registered = _post(
        "/__deploy/discovery/registrations",
        RegisterDiscoveryInstance("register-a", first),
        headers,
    )
    assert registered["result"]["outcome"] == "registered"
    assert _resolve("resolve-a", now, headers) == ["hello-a"]

    renewed_lease = DiscoveryLease(
        now + timedelta(seconds=10),
        now + timedelta(seconds=120),
    )
    heartbeat = _post(
        "/__deploy/discovery/registrations/hello-a/heartbeat",
        HeartbeatDiscoveryInstance(
            "heartbeat-a",
            first.identity,
            first.lease.expires_at,
            renewed_lease,
        ),
        headers,
    )
    assert heartbeat["result"]["registrations"][0]["revision"] == 2

    deregistered = _post(
        "/__deploy/discovery/registrations/hello-a/deregister",
        DeregisterDiscoveryInstance(
            "deregister-a", first.identity, renewed_lease.expires_at
        ),
        headers,
    )
    assert deregistered["result"]["outcome"] == "deregistered"
    assert _resolve("resolve-after-deregister", now, headers) == []

    second = _registration("hello-b", now)
    _post(
        "/__deploy/discovery/registrations",
        RegisterDiscoveryInstance("register-b", second),
        headers,
    )
    expired = _post(
        "/__deploy/discovery/expiry",
        ExpireDiscoveryLeases(
            "expire-b", WORKSPACE, second.lease.expires_at, 100
        ),
        headers,
    )
    assert expired["result"]["affected_count"] == 1
    assert _resolve("resolve-after-expiry", second.lease.expires_at, headers) == []

    print(
        "Live discovery passed: unauthorized=401, register, resolve, "
        "heartbeat, deregister, expiry"
    )


def _registration(instance_id: str, now: datetime) -> DiscoveryRegistration:
    return DiscoveryRegistration(
        DiscoveryIdentity(WORKSPACE, "hello", instance_id),
        Endpoint(
            LiteralAddress(f"http://{instance_id}:8080"),
            Protocol.HTTP,
            EndpointScope.PRIVATE,
        ),
        DiscoveryRegistrationMode.CONTROL_PLANE,
        DiscoveryLease(now, now + timedelta(seconds=60)),
    )


def _headers() -> dict[str, str]:
    return {
        "x-cpk-identity-attestation": TOKEN,
        "x-cpk-authenticated-subject": "live-manager",
        "x-cpk-authenticated-workspace": WORKSPACE,
        "x-cpk-discovery-scopes": "discovery:manage,discovery:resolve",
    }


def _post(path: str, command, headers: dict[str, str]) -> dict[str, object]:
    response = httpx.post(
        f"{BASE_URL}{path}",
        json=discovery_command_descriptor(command),
        headers=headers,
        timeout=5,
    )
    _status(response, 200)
    return response.json()


def _resolve(
    command_id: str,
    observed_at: datetime,
    headers: dict[str, str],
) -> list[str]:
    response = httpx.get(
        f"{BASE_URL}/__deploy/discovery/services/hello",
        params={
            "command_id": command_id,
            "workspace_id": WORKSPACE,
            "observed_at": observed_at.isoformat(),
            "limit": 100,
        },
        headers=headers,
        timeout=5,
    )
    _status(response, 200)
    return [
        value["registration"]["identity"]["instance_id"]
        for value in response.json()["result"]["registrations"]
    ]


def _status(response: httpx.Response, expected: int) -> None:
    if response.status_code != expected:
        raise AssertionError(
            f"expected HTTP {expected}, got {response.status_code}: {response.text}"
        )


if __name__ == "__main__":
    main()
