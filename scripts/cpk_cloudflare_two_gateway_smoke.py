"""Low-level Cloudflare/gateway capability smoke.

This script is not final cpk-server acceptance. It exists to preserve the live
lesson that the ingredients can work together:

CloudflareNamedIngressInterpreter
  -> cloudflared-connector
    -> cpk-local-gateway
      -> public hostname reaches a private runtime-island target

Final SEEDED.INGRESS acceptance must go through the actual operator program
rather than this host-side diagnostic:

operator -> cpk-server -> operations -> coordinator -> interpreters -> observations
"""

from __future__ import annotations

import argparse
import json
import json as json_module
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from control_plane_kit_core.public_ingress import (
    IngressAuthorityReference,
    NamedPublicIngress,
    PublicIngressLifecycle,
    PublicIngressTarget,
)
from control_plane_kit_core.secrets import (
    LocalDevelopmentSecretResolver,
    SecretProviderAuthority,
    SecretProviderId,
    SecretReference,
)
from control_plane_kit_interpreters.cloudflare import (
    CloudflareApiError,
    CloudflareHttpResponse,
    CloudflareNamedIngressInterpreter,
    CloudflareZoneAuthority,
)
from control_plane_kit_operations.ingress_authorities import (
    CloudflareOwnedIngressResource,
    CloudflareZoneIngressAuthority,
    cloudflare_ingress_teardown_plan,
    cloudflare_tunnel_token_delivery_plan,
    require_cloudflared_tunnel_token_delivery,
)


ROOT = Path(__file__).resolve().parents[1]
PROJECT_LABEL = "org.openj92.project=control-plane-kit-servers"
WORKSPACE_LABEL_KEY = "org.openj92.cpk.workspace"
WORKSPACE_LABEL = f"{WORKSPACE_LABEL_KEY}=cloudflare-two-gateway-ingress"
HOSTNAMES = ("cpk-gateway-001.openj92.dev", "cpk-gateway-002.openj92.dev")


@dataclass
class StdlibCloudflareTransport:
    timeout: float = 20.0

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        json: dict[str, object] | None = None,
        params: dict[str, str] | None = None,
    ) -> CloudflareHttpResponse:
        if params:
            url = f"{url}?{urllib.parse.urlencode(params)}"
        data = None if json is None else json_module.dumps(json).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=data,
            headers=dict(headers),
            method=method,
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                body = _json_body(response.read().decode("utf-8"))
                return CloudflareHttpResponse(response.status, body)
        except urllib.error.HTTPError as error:
            raw_body = error.read().decode("utf-8")
            body = _json_body(raw_body)
            return CloudflareHttpResponse(error.code, body)


def _json_body(raw_body: str) -> dict[str, object]:
    if not raw_body:
        return {}
    body = json_module.loads(raw_body)
    if not isinstance(body, dict):
        return {}
    return body


@dataclass
class Island:
    index: int
    hostname: str
    network: str
    gateway_container: str = ""
    hello_container: str = ""
    cloudflared_container: str = ""
    tunnel_id: str = ""
    dns_record_id: str = ""
    tunnel_name: str = ""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run two CPK local gateways through owned Cloudflare tunnels."
    )
    parser.add_argument(
        "--env-file",
        default=str(ROOT / "env" / "cloudflare.openj92.local.dev"),
        help="local env file containing Cloudflare account credentials",
    )
    parser.add_argument(
        "--keep-resources",
        action="store_true",
        help="leave Docker and Cloudflare resources running for manual inspection",
    )
    args = parser.parse_args(argv)

    _load_env(Path(args.env_file))
    if os.environ.get("CPK_CLOUDFLARE_LIVE_ACCEPTANCE") != "1":
        raise SystemExit(
            "set CPK_CLOUDFLARE_LIVE_ACCEPTANCE=1 to run live Cloudflare acceptance"
        )

    authority = _authority()
    ops_authority = _operations_authority()
    interpreter = CloudflareNamedIngressInterpreter(
        secret_resolver=_secret_resolver(),
        transport=StdlibCloudflareTransport(),
    )
    images = {
        "gateway": _image_from_descriptor("products/cpk_local_gateway/product.cpk.json"),
        "hello": _image_from_descriptor("products/hello_server/product.cpk.json"),
        "cloudflared": _image_from_descriptor(
            "products/cloudflared_connector/product.cpk.json"
        ),
    }
    for image in images.values():
        _run("docker", "pull", image)

    islands = [
        Island(index=index, hostname=hostname, network=f"cpk-cloudflare-gateway-{index}")
        for index, hostname in enumerate(HOSTNAMES, start=1)
    ]
    try:
        for island in islands:
            _start_island(island, images)
            _allocate_ingress(island, authority, ops_authority, interpreter, images)
        for island in islands:
            _wait_public_ready(island.hostname)
            _assert_public_private_probe(island.hostname)
        print(
            json.dumps(
                {
                    "status": "passed",
                    "hostnames": [island.hostname for island in islands],
                    "resource_evidence": [
                        {
                            "hostname": island.hostname,
                            "tunnel_id": island.tunnel_id,
                            "dns_record_id": island.dns_record_id,
                        }
                        for island in islands
                    ],
                },
                sort_keys=True,
            )
        )
        return 0
    finally:
        if not args.keep_resources:
            for island in reversed(islands):
                _cleanup_island(island, ops_authority, interpreter)


def _start_island(island: Island, images: dict[str, str]) -> None:
    _run("docker", "network", "rm", island.network, check=False)
    _run(
        "docker",
        "network",
        "create",
        "--label",
        PROJECT_LABEL,
        "--label",
        WORKSPACE_LABEL,
        island.network,
    )
    island.hello_container = _run(
        "docker",
        "run",
        "-d",
        "--label",
        PROJECT_LABEL,
        "--label",
        WORKSPACE_LABEL,
        "--network",
        island.network,
        "--network-alias",
        "hello",
        "-e",
        f"HELLO_MESSAGE=Hello from island {island.index}",
        images["hello"],
    ).stdout.strip()
    targets_json = json.dumps(
        {"hello.http": {"protocol": "http", "url": "http://hello:8000"}},
        separators=(",", ":"),
        sort_keys=True,
    )
    island.gateway_container = _run(
        "docker",
        "run",
        "-d",
        "--label",
        PROJECT_LABEL,
        "--label",
        WORKSPACE_LABEL,
        "--network",
        island.network,
        "--network-alias",
        "gateway",
        "-e",
        f"CPK_GATEWAY_TARGETS_JSON={targets_json}",
        images["gateway"],
    ).stdout.strip()
    _wait_gateway_ready(island.gateway_container)


def _allocate_ingress(
    island: Island,
    authority: CloudflareZoneAuthority,
    ops_authority: CloudflareZoneIngressAuthority,
    interpreter: CloudflareNamedIngressInterpreter,
    images: dict[str, str],
) -> None:
    ingress = NamedPublicIngress(
        ingress_id=f"gateway-{island.index:03d}",
        authority_ref=IngressAuthorityReference("openj92-cloudflare"),
        target=PublicIngressTarget("gateway", "control"),
        hostname=island.hostname,
    )
    allocation = interpreter.create(
        ingress,
        authority=authority,
        origin_service_url="http://gateway:8000",
    )
    island.tunnel_id = allocation.tunnel_id
    island.dns_record_id = allocation.dns_record_id
    island.tunnel_name = allocation.tunnel_name
    resource = CloudflareOwnedIngressResource(
        workspace_id="cloudflare-two-gateway-ingress",
        runtime_id=f"docker-island-{island.index:03d}",
        ingress_id=ingress.ingress_id,
        tunnel_name=allocation.tunnel_name,
        tunnel_id=allocation.tunnel_id,
        dns_record_id=allocation.dns_record_id,
        hostname=allocation.hostname,
        zone_id=ops_authority.zone_id,
        lifecycle=ingress.lifecycle,
        created_at="live-cloudflare-allocation",
        observed_at="live-cloudflare-allocation",
    )
    delivery_plan = cloudflare_tunnel_token_delivery_plan(
        authority=ops_authority,
        resource=resource,
        connector_node_id=f"cloudflared-{island.index:03d}",
        tunnel_token_ref=SecretReference(
            f"secret://cloudflare/openj92/{ingress.ingress_id}-tunnel-token"
        ),
    )
    require_cloudflared_tunnel_token_delivery((delivery_plan.secret_delivery,))
    island.cloudflared_container = _run(
        "docker",
        "run",
        "-d",
        "--label",
        PROJECT_LABEL,
        "--label",
        WORKSPACE_LABEL,
        "--network",
        island.network,
        "--network-alias",
        "cloudflared",
        "-e",
        "TUNNEL_TOKEN",
        images["cloudflared"],
        "tunnel",
        "run",
        env={"TUNNEL_TOKEN": allocation.tunnel_token.reveal()},
    ).stdout.strip()


def _cleanup_island(
    island: Island,
    authority: CloudflareZoneIngressAuthority,
    interpreter: CloudflareNamedIngressInterpreter,
) -> None:
    for container in (
        island.cloudflared_container,
        island.gateway_container,
        island.hello_container,
    ):
        if container:
            _run("docker", "rm", "-f", container, check=False)
    if island.network:
        _run("docker", "network", "rm", island.network, check=False)
    if island.tunnel_id and island.dns_record_id:
        resource = CloudflareOwnedIngressResource(
            workspace_id="cloudflare-two-gateway-ingress",
            runtime_id=f"docker-island-{island.index:03d}",
            ingress_id=f"gateway-{island.index:03d}",
            tunnel_name=island.tunnel_name,
            tunnel_id=island.tunnel_id,
            dns_record_id=island.dns_record_id,
            hostname=island.hostname,
            zone_id=authority.zone_id,
            lifecycle=PublicIngressLifecycle.EPHEMERAL,
            created_at="live-cloudflare-allocation",
            observed_at="live-cloudflare-allocation",
        )
        cloudflare_ingress_teardown_plan(authority=authority, resource=resource)
        _teardown_cloudflare_with_retry(
            tunnel_id=island.tunnel_id,
            dns_record_id=island.dns_record_id,
        )


def _teardown_cloudflare_with_retry(
    *,
    tunnel_id: str,
    dns_record_id: str,
) -> None:
    _delete_cloudflare_resource(
        f"/zones/{_env('OPENJ92_CLOUDFLARE_ZONE_ID')}/dns_records/{dns_record_id}"
    )
    for attempt in range(1, 13):
        try:
            _delete_cloudflare_resource(
                f"/accounts/{_env('OPENJ92_CLOUDFLARE_ACCOUNT_ID')}/cfd_tunnel/{tunnel_id}"
            )
            return
        except CloudflareApiError:
            if attempt == 12:
                raise
            time.sleep(10)


def _delete_cloudflare_resource(path: str) -> None:
    response = StdlibCloudflareTransport().request(
        "DELETE",
        f"https://api.cloudflare.com/client/v4{path}",
        headers={
            "Authorization": f"Bearer {_env('OPENJ92_CLOUDFLARE_API_TOKEN')}",
            "Content-Type": "application/json",
        },
    )
    if response.status_code == 404:
        return
    if response.status_code < 200 or response.status_code >= 300:
        raise CloudflareApiError(
            f"Cloudflare API delete failed with status {response.status_code}"
        )


def _wait_gateway_ready(container: str) -> None:
    for _ in range(30):
        result = _run(
            "docker",
            "exec",
            container,
            "python",
            "-c",
            (
                "import urllib.request;"
                "urllib.request.urlopen('http://127.0.0.1:8000/health/ready', timeout=2)"
            ),
            check=False,
        )
        if result.returncode == 0:
            return
        time.sleep(1)
    raise RuntimeError("gateway did not become ready")


def _wait_public_ready(hostname: str) -> None:
    url = f"https://{hostname}/health/ready"
    for _ in range(60):
        try:
            with urllib.request.urlopen(url, timeout=5) as response:
                if response.status == 200:
                    return
        except Exception:
            time.sleep(2)
    raise RuntimeError(f"public gateway did not become ready: {hostname}")


def _assert_public_private_probe(hostname: str) -> None:
    request = urllib.request.Request(
        f"https://{hostname}/cpk/probes",
        data=json.dumps(
            {"kind": "http-status", "target_id": "hello.http", "path": "/health/ready"}
        ).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if payload.get("outcome") != "passed" or payload.get("status") != 200:
        raise RuntimeError(f"public gateway private probe failed: {payload!r}")
    encoded = json.dumps(payload).lower()
    if "secret" in encoded or "token" in encoded or "password" in encoded:
        raise RuntimeError("public probe response leaked secret-shaped material")


def _image_from_descriptor(relative_path: str) -> str:
    descriptor = json.loads((ROOT / relative_path).read_text(encoding="utf-8"))
    image = descriptor["product"]["image"]
    return f"{image['registry']}/{image['repository']}@{image['digest']}"


def _authority() -> CloudflareZoneAuthority:
    return CloudflareZoneAuthority(
        account_id=_env("OPENJ92_CLOUDFLARE_ACCOUNT_ID"),
        zone_id=_env("OPENJ92_CLOUDFLARE_ZONE_ID"),
        zone_name=os.environ.get("OPENJ92_CLOUDFLARE_ZONE", "openj92.dev"),
        api_token_ref=SecretReference("secret://cloudflare/openj92/api-token"),
        allowed_hostname_pattern="cpk-gateway-*.openj92.dev",
    )


def _operations_authority() -> CloudflareZoneIngressAuthority:
    return CloudflareZoneIngressAuthority(
        account_id=_env("OPENJ92_CLOUDFLARE_ACCOUNT_ID"),
        zone_id=_env("OPENJ92_CLOUDFLARE_ZONE_ID"),
        zone_name=os.environ.get("OPENJ92_CLOUDFLARE_ZONE", "openj92.dev"),
        api_token_ref=SecretReference("secret://cloudflare/openj92/api-token"),
        allowed_hostname_pattern="cpk-gateway-*.openj92.dev",
    )


def _secret_resolver() -> LocalDevelopmentSecretResolver:
    return LocalDevelopmentSecretResolver(
        SecretProviderAuthority(SecretProviderId("cloudflare")),
        {
            "secret://cloudflare/openj92/api-token": _env(
                "OPENJ92_CLOUDFLARE_API_TOKEN"
            )
        },
    )


def _env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"{name} is required")
    return value


def _load_env(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


def _run(
    *command: str,
    check: bool = True,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        command,
        text=True,
        capture_output=True,
        check=False,
        env=None if env is None else {**os.environ, **env},
    )
    if check and result.returncode != 0:
        raise RuntimeError(
            f"command failed: {command[0]} {command[1] if len(command) > 1 else ''}"
        )
    return result


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv[1:]))
    except Exception as error:
        print(f"cpk Cloudflare two-gateway smoke failed: {error}", file=sys.stderr)
        raise SystemExit(1) from error
